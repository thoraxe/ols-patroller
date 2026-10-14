"""Image CI — contract tests for the post-merge build workflow (#2).

These tests pin the workflow STATICALLY: they parse
``.github/workflows/image.yml`` and assert the post-merge build & publish
contract. No workflow run is needed; they fail on a missing or non-conforming
file.

Contract: .ai/spec/what/container-image.md rules 13-16 [PLANNED: #2].

Run: uv run --group dev pytest tests/ -q
"""
from pathlib import Path

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


# ------------------------------------------------------------------ rules 13: trigger

def test_triggers_post_merge_only():
    triggers = _triggers(_wf())
    assert triggers == {"push": {"branches": ["main"]}}, (
        "the workflow must trigger on push to main ONLY — no pull_request, no "
        "workflow_dispatch, no tag triggers, no path filter (rule 13)"
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
