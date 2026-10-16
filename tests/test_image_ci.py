"""Image CI — contract tests for the post-merge build workflow (#2) and the
release retag path (#36).

These tests pin the workflow STATICALLY: they parse
``.github/workflows/image.yml`` and assert the post-merge build & publish
contract (rules 13-16) plus the release-tag contract (rules 17-18: published
`v*` releases and `workflow_dispatch` retag the matching `sha-<SHA>` image —
never a rebuild). No workflow run is needed; they fail on a missing or
non-conforming file.

Contract: .ai/spec/what/container-image.md rules 13-18
(ADR decisions/0010-release-retag-not-rebuild.md)
[rules 17-18: PLANNED: #36].

Run: uv run --group dev pytest tests/ -q
"""
from pathlib import Path

import pytest
import yaml


REPO = Path(__file__).resolve().parent.parent
WORKFLOW = REPO / ".github" / "workflows" / "image.yml"
IMAGE = "ghcr.io/thoraxe/ols-patroller"


# ------------------------------------------------------------------ helpers

def _wf() -> dict:
    assert WORKFLOW.exists(), (
        ".github/workflows/image.yml must exist — post-merge image build & "
        "publish (.ai/spec/what/container-image.md rules 13-16, #2)"
    )
    return yaml.safe_load(WORKFLOW.read_text())


def _triggers(wf: dict) -> dict:
    # YAML 1.1 parses a bare `on:` key as boolean True.
    triggers = wf.get("on")
    if triggers is None:
        triggers = wf.get(True)
    assert isinstance(triggers, dict), (
        "the workflow must declare its triggers (rule 13)"
    )
    return triggers


def _steps(wf: dict) -> list[dict]:
    steps: list[dict] = []
    for job in wf.get("jobs", {}).values():
        steps.extend(job.get("steps", []))
    assert steps, "the workflow must have at least one job with steps (rule 14)"
    return steps


def _step_using(steps: list[dict], action: str) -> dict:
    step = next((s for s in steps if action in str(s.get("uses", ""))), None)
    assert step, (
        f"the workflow must use {action} (rules 14-15)"
    )
    return step


def _retag_jobs(wf: dict) -> list[dict]:
    """Every job that performs the registry-side release retag (rule 17)."""
    jobs = [job for job in wf.get("jobs", {}).values()
            if "imagetools" in yaml.safe_dump(job)]
    assert jobs, (
        "a release-retag job must exist: a job running `docker buildx "
        "imagetools create` to copy the sha-<SHA> manifest to the release tag "
        "(.ai/spec/what/container-image.md rule 17, #36)"
    )
    return jobs


def _retag_step(jobs: list[dict]) -> dict:
    """The step whose run performs the manifest copy (rule 17)."""
    for job in jobs:
        for step in job.get("steps", []):
            if "imagetools create" in str(step.get("run", "")):
                return step
    pytest.fail("the retag job must run `docker buildx imagetools create` (rule 17)")


def _build_job(wf: dict) -> tuple[str, dict]:
    """The job that builds the image (docker/build-push-action, rule 14)."""
    for name, job in wf.get("jobs", {}).items():
        if any("docker/build-push-action" in str(s.get("uses", ""))
               for s in job.get("steps", [])):
            return name, job
    pytest.fail("the workflow must have a build job using docker/build-push-action (rule 14)")


# ------------------------------------------------------------------ rules 13, 17-18: triggers

def test_triggers_post_merge_push_and_release_retag_paths():
    """Rules 13 + 17-18: push to main is the ONLY build trigger (no
    pull_request, no push tag triggers); published releases and manual
    workflow_dispatch trigger the retag-only paths."""
    triggers = _triggers(_wf())
    assert set(triggers) == {"push", "release", "workflow_dispatch"}, (
        "the workflow must have exactly these three triggers — the build "
        "path (push to main) and the two retag-only paths; no others "
        "(rules 13, 17-18)"
    )
    assert triggers.get("push") == {"branches": ["main"]}, (
        "the build path must trigger on push to main ONLY — no pull_request, "
        "no other branches, no push tag triggers (rule 13)"
    )
    assert "pull_request" not in triggers, (
        "PR-time builds are deliberately excluded — CI stays free on "
        "draft/spec work (rule 13)"
    )
    assert triggers.get("release") == {"types": ["published"]}, (
        "published releases must trigger the workflow for the retag path "
        "(rule 17). The release trigger takes no tag filter — the v* gate "
        "is the release job's if-guard, not a trigger filter"
    )
    assert triggers.get("workflow_dispatch") is not None, (
        "workflow_dispatch must be declared (with its tag input) as the "
        "manual retag path (rule 18)"
    )


# ------------------------------------------------------------------ rule 15: permissions & auth

def test_permissions_minimal_github_token():
    wf = _wf()
    perms = wf.get("permissions")
    assert perms == {"contents": "read", "packages": "write"}, (
        "workflow permissions must be exactly contents: read + packages: "
        "write — the built-in GITHUB_TOKEN publishes, no PATs (rule 15)"
    )


def test_login_uses_builtin_github_token():
    login = _step_using(_steps(_wf()), "docker/login-action")
    with_ = login.get("with") or {}
    assert with_.get("registry") == "ghcr.io", (
        "login must target ghcr.io (rule 15)"
    )
    assert "GITHUB_TOKEN" in str(with_.get("password", "")), (
        "login must authenticate with the built-in GITHUB_TOKEN (rule 15)"
    )


# ------------------------------------------------------------------ rules 14-15: build & publish

def test_build_pushes_from_repo_root_containerfile():
    build = _step_using(_steps(_wf()), "docker/build-push-action")
    with_ = build.get("with") or {}
    assert with_.get("context") == ".", (
        "build context must be the repo root (rule 14)"
    )
    assert with_.get("file") == "./Containerfile", (
        "the workflow must name the Containerfile — the action's default "
        "'Dockerfile' name is wrong for this repo (rule 14)"
    )
    assert with_.get("push") is True, (
        "the workflow must push the built image (rule 14)"
    )
    assert with_.get("platforms") == "linux/amd64", (
        "linux/amd64 only — multi-arch is out of scope (rule 14)"
    )


def test_publishes_to_ghcr_with_main_and_sha_tags():
    build = _step_using(_steps(_wf()), "docker/build-push-action")
    tags = build.get("with", {}).get("tags")
    text = "\n".join(tags) if isinstance(tags, list) else str(tags)
    assert f"{IMAGE}:main" in text, (
        f"the published image must carry the rolling {IMAGE}:main tag "
        f"(rule 15)"
    )
    assert "github.sha" in text and "sha-" in text, (
        "the published image must also carry a sha-<commit SHA> tag keyed on "
        "github.sha — the per-merge traceable pin (rule 15)"
    )


# ------------------------------------------------- rules 17-18: release retag path

def test_dispatch_input_tag_required():
    """Rule 18: the manual retag path takes a required `tag` input — the
    release tag to apply."""
    dispatch = _triggers(_wf()).get("workflow_dispatch")
    inputs = (dispatch or {}).get("inputs") or {}
    tag = inputs.get("tag")
    assert tag and tag.get("required") is True, (
        "workflow_dispatch must declare a required `tag` input — the release "
        "tag the manual path applies (rule 18)"
    )


def test_release_retag_is_registry_side_not_a_build():
    """Rule 17: the release job retags via a registry-side manifest copy
    (docker buildx imagetools create) and never builds, never pulls."""
    jobs = _retag_jobs(_wf())
    text = yaml.safe_dump(jobs)
    for job in jobs:
        for step in job.get("steps", []):
            assert "docker/build-push-action" not in str(step.get("uses", "")), (
                "the release-retag job must never build — release tags are "
                "manifest copies of the sha-<SHA> image, not rebuilds "
                "(rule 17)"
            )
    assert "docker pull" not in text, (
        "the retag is registry-side — no pull (rule 17)"
    )


def test_build_job_is_event_guarded_to_push():
    """Rule 13: the build job is event-guarded — release and dispatch
    events must never trigger a build."""
    name, job = _build_job(_wf())
    guard = str(job.get("if", ""))
    assert "event_name" in guard and "push" in guard, (
        f"the build job ({name}) must be guarded to push events only via its "
        f"`if:` condition (rule 13) — got {guard!r}"
    )


def test_release_retag_resolves_tagged_commit_from_the_tag():
    """Rule 17: the tagged commit is resolved from the tag itself — a
    checkout of the tag (ref keyed on the release tag_name or the dispatch
    input) or git ls-remote — because github.sha is NOT the tagged commit on
    release/dispatch events."""
    jobs = _retag_jobs(_wf())
    text = yaml.safe_dump(jobs)
    checkouts = [s for job in jobs for s in job.get("steps", [])
                 if "actions/checkout" in str(s.get("uses", ""))]
    tag_checkout = any(
        ("tag_name" in str(s.get("with", {}).get("ref", "")))
        or ("inputs.tag" in str(s.get("with", {}).get("ref", "")))
        for s in checkouts)
    assert tag_checkout or "ls-remote" in text, (
        "the retag job must resolve the tagged commit from the tag — "
        "actions/checkout with ref: <tag> (then git rev-parse HEAD or the "
        "checkout commit output), or git ls-remote against the tag ref "
        "(rule 17)"
    )


def test_release_retag_source_sha_target_tag():
    """Rule 17: the retag copies the sha-<resolved SHA> tag of the commit
    the release tag points at to the release tag name, on the patrol image;
    it never keys the source on github.sha and never writes :main or
    :latest."""
    step = _retag_step(_retag_jobs(_wf()))
    cmd = str(step.get("run", "")) + "\n" + yaml.safe_dump(step.get("env") or {})
    assert IMAGE in cmd, (
        "the retag must target the patrol image on GHCR (rules 15, 17)"
    )
    assert "sha-" in cmd, (
        "the retag source must be the sha-<full commit SHA> tag of the "
        "commit the release tag points at (rule 17)"
    )
    assert "github.sha" not in cmd, (
        "the retag source must NOT key on github.sha — on release events "
        "that is the target-commitish HEAD and on dispatch the dispatched "
        "ref's HEAD, not the tagged commit; the SHA is resolved from the "
        "tag (rule 17)"
    )
    assert "tag_name" in cmd or "inputs.tag" in cmd, (
        "the retag target must be the release tag — the release's tag_name "
        "or the dispatch input tag (rules 17-18)"
    )
    assert ":main" not in yaml.safe_dump(_retag_jobs(_wf())), (
        "the release job must never write the :main tag (rule 17)"
    )
    assert ":latest" not in yaml.safe_dump(_retag_jobs(_wf())), (
        "the release path must publish no :latest tag — :main stays the "
        "rolling tag (rule 17)"
    )


def test_release_job_authenticates_per_rule_15():
    """Rule 17: the retag job authenticates exactly per rule 15 — GHCR login
    with the built-in GITHUB_TOKEN, no other credentials."""
    for job in _retag_jobs(_wf()):
        logins = [s for s in job.get("steps", [])
                  if "docker/login-action" in str(s.get("uses", ""))]
        assert logins, (
            "the release-retag job must log in to GHCR (docker/login-action) "
            "before the manifest copy (rule 17, per rule 15)"
        )
        for login in logins:
            with_ = login.get("with") or {}
            assert with_.get("registry") == "ghcr.io", (
                "the retag job's login must target ghcr.io (rule 15)"
            )
            assert "GITHUB_TOKEN" in str(with_.get("password", "")), (
                "the retag job's login must use the built-in GITHUB_TOKEN "
                "(rule 15)"
            )


def test_release_job_v_guard_covers_both_paths():
    """Rules 17-18: every retag job is v-guarded via its own `if:` (the
    release trigger has no tag filter), and the guards cover both trigger
    paths — the release tag_name and the dispatch input tag."""
    jobs = _retag_jobs(_wf())
    guards = [str(job.get("if", "")) for job in jobs]
    for guard in guards:
        assert "startsWith" in guard or "^v" in guard, (
            "every retag job must be v-guarded — a startsWith(tag, 'v') or "
            "equivalent prefix check in its `if:` condition (rules 17-18)"
        )
    all_guards = "\n".join(guards)
    assert "tag_name" in all_guards, (
        "the release path must be guarded on the release tag_name (rule 17)"
    )
    assert "inputs.tag" in all_guards, (
        "the dispatch path must be guarded on the dispatch input tag "
        "(rule 18)"
    )


def test_release_retag_fails_loudly():
    """Rule 17: a missing source sha-<SHA> tag must fail the job — the
    retag step carries no continue-on-error."""
    step = _retag_step(_retag_jobs(_wf()))
    assert not step.get("continue-on-error"), (
        "the retag step must fail loudly when the source sha-<SHA> tag does "
        "not exist — releasing an unbuilt commit is an error, not a silent "
        "skip (rule 17)"
    )
