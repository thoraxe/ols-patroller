# Container Image (in-cluster runtime)

The ubi9-minimal image that runs `patrol.py` in-cluster, built from the repo-root
`Containerfile` (#1). The image is what makes the patroller deployable; the
Kubernetes wiring around it (ServiceAccount, cluster-reader RBAC, PVC, Deployment)
is #2 and makes `safety-model.md` layer 3 real. Merges to `main` also publish the
image to GHCR from CI (rules 13–16, #2), and published `v*` releases retag it
(rules 17–18, #36) — that published image is what the
manifests deploy by default.

## Behavioral Rules

### Base & OS Packages

1. The image is built from `registry.access.redhat.com/ubi9/ubi-minimal`, with OS
   packages installed via `microdnf`: `python3.12`, `python3.12-pip`, and `git` —
   git is required by the wiki repo (`wiki-memory.md` rules 4a–4e) [DONE: #1].
2. UBI ships no `oc`. The build fetches the OpenShift client tarball
   (`openshift-client-*.tar.gz`, providing **both** `oc` and `kubectl`) from
   `mirror.openshift.com` at **build time**; the container never downloads
   anything at start [DONE: #1].
3. The client tarball URL is **version-pinned**: it names exactly one OpenShift
   client release — the release version appears **literally** in the URL as a
   digit-leading path segment immediately before the `openshift-client` archive
   name; `stable`, `latest`, and any other floating tag are forbidden. Bumping
   the client version is a deliberate `Containerfile` edit (the URL literal),
   never a rebuild side effect [DONE: #1].
4. The fetched tarball is verified against a SHA-256 digest pinned **in the
   same instruction that fetches it** (a `sha256sum -c` check) **before** it is
   extracted — the canonical form is a single RUN: fetch → verify → extract, in
   that order. The binary becomes the patrol's cluster CLI, so the supply chain
   is pinned to match the version pin [DONE: #1].

### Python Dependencies

5. Python dependencies are installed at **build time** by pip from the repo-root
   `requirements.txt`; the container performs no dependency resolution or
   installation at start — `patrol.py --once` / `--loop` must run without any
   network package management. Rule 9's direct ENTRYPOINT (`python3.12
   patrol.py`, no wrapper script) is what makes this statically checkable
   [DONE: #1].
6. `requirements.txt` pins every requirement exactly (`name==version`, one per
   line, nothing else) and lists exactly the top-level runtime dependencies —
   the same package set as `patrol.py`'s PEP 723 header (`deepagents`,
   `langchain-openai`, `pydantic`, `pyyaml` [DONE: #5 — the wiki
   skeleton's frontmatter parser], and `apprise` [PLANNED: #9 — multi-sink
   notifications, `reporting-notifications.md` rules 10a–10c; `requests`
   drops from the top-level set — its only direct use was the Slack webhook
   post, and apprise brings it transitively]). Transitive dependencies are resolved by pip
   at build time and are not listed; dev-only tools (e.g. pytest) never appear
   [DONE: #1].
7. `patrol.py`'s PEP 723 uv header is kept unchanged — `uv run patrol.py`
   remains the dev-mode entry — and `pyproject.toml` keeps mirroring the same
   top-level set for the test environment. `requirements.txt` must name exactly
   the packages in the PEP 723 header and cover every package in
   `pyproject.toml`'s `[project]` dependencies (drift guard; test-enforced).
   Decision record: `decisions/0005-pinned-requirements-for-image.md`
   [DONE: #1].

### User & Filesystem Layout

8. The image runs as a **non-root user** and is **random-UID compatible**
   (OpenShift SCCs assign arbitrary UIDs with primary GID 0): the runtime `USER`
   is a non-root UID (group 0 — the ubi9 default user 1001, or `USER <uid>:0`),
   and `/wiki` — the wiki mount point (`wiki-memory.md` rule 4a; the PVC from
   #2) — is created in the image group-owned by GID 0 with group write
   (`chgrp 0` + `chmod g=u`, or an equivalent form the tests enumerate:
   `g+w`, `g+rw`, `g+rwX`, `775`, `777`, `2775`, `install -d -g 0 -m <mode>`),
   so any assigned UID can write it.
   Git needs no `HOME` or user config: `wiki-memory.md` rule 4d already passes
   `-c user.name=… -c user.email=… safe.directory=…` per command [DONE: #1].
9. The image copies `patrol.py` and `requirements.txt` in, and its default
   command is `patrol.py --loop --interval 21600 --wiki /wiki/cluster-wiki.md` —
   `ENTRYPOINT` invokes the script via the image's `python3.12`; runtime wiring
   (Deployment args, Secret-sourced env) belongs to the #2 manifests and may
   override the default command [DONE: #1] [DONE: #25 — cadence 900 s →
   6 h, kept identical to the Deployment args].
10. The image bakes **no configuration and no secrets**: no `ENV` instruction
    sets any `PATROL_*` or `SLACK_*` variable. `PATROL_API_KEY` /
    `PATROL_BASE_URL` / `PATROL_MODEL` arrive at runtime from the Secret defined
    by the #2 manifests; the startup env check is the standard one
    (`system-overview.md` rule 6) [DONE: #1].

### Verification

11. The image contract is unit-checked **statically**:
    `tests/test_container_image.py` parses the `Containerfile` and
    `requirements.txt` and asserts rules 1–10 (the tarball's both-binaries
    content from rule 2 and the rule 12 procedure are verified by running the
    built image, not by parsing) — it fails on a missing or non-conforming
    file; no container runtime is needed [DONE: #1].
12. Build & runtime acceptance is the procedure below, performed with a local
    container runtime and documented **in this rule** — no separate repo
    artifact is planned (and it is not part of the pytest suite) [DONE: #1]:
    - `podman build .` (or `docker build .`) succeeds;
    - inside the container, `oc version`, `kubectl version --client`, and
      `git --version` all succeed;
    - `python3.12 -c "import deepagents, langchain_openai, pydantic, apprise, yaml"`
      succeeds inside the container (build-time dependency install; `yaml` is
      PyYAML, the wiki frontmatter parser [DONE: #5]; `apprise` is the
      notification sink library [PLANNED: #9]);
    - `podman run --rm <image>` with no `PATROL_*` env exits non-zero with the
      standard missing-env error (`system-overview.md` rule 6).
    A patrol inside the container against a real dev cluster — through the #2
    manifests — is #2's done-when, not this spec's.

### Continuous Build & Publication (CI)

13. Merges to `main` publish the image: the GitHub Actions workflow
    `.github/workflows/image.yml` builds and publishes on `push` to `main`
    only — no `pull_request` trigger, no other branches, no `push` tag
    triggers (post-merge artifact pipeline, not a merge gate; PR-time builds
    are deliberately excluded — CI stays free on draft/spec work). No path
    filter: every merge republishes `:main`, keeping the tag equal to "latest
    main" (accepted slack). The workflow's only other triggers are the
    retag-only release paths (rules 17–18), and the build job is
    event-guarded so that only a `push` event ever builds [DONE: #2]
    [build-job guard: DONE: #36].
14. The workflow builds with `docker/build-push-action` from the repo root:
    build context `.`, file `./Containerfile` (the action's default
    `Dockerfile` name is wrong for this repo — the workflow must name the
    file), platform `linux/amd64` only, `push: true` [DONE: #2].
15. Publication target is GHCR: `ghcr.io/thoraxe/ols-patroller`, tagged `main`
    (rolling, latest merged state) and `sha-<full commit SHA>` (per-merge,
    traceable pin). Login is `docker/login-action` with the built-in
    `GITHUB_TOKEN`; workflow permissions are exactly `contents: read` +
    `packages: write` — no personal access tokens, no registry credentials
    stored anywhere [DONE: #2].
16. The CI-published image is the deployment artifact: the #2 manifests
    reference `ghcr.io/thoraxe/ols-patroller:main` by default (kustomize
    `images` override — `in-cluster-deployment.md` rule 8). The first publish
    requires flipping the GHCR package to public once (the repo is public), or
    the deploying cluster uses an image pull secret / an overridden ref
    (ADR `decisions/0008-ci-published-image.md`) [DONE: #2].
17. Published GitHub releases tag the image — **retagged, never rebuilt**
    [DONE: #36]. The workflow also triggers on published releases
    (`release: types: [published]`); a dedicated release job applies the
    release's tag name (e.g. `v0.1`) to the container image by copying the
    registry manifest of the already-published `sha-<full commit SHA>` tag for
    the release's tagged commit to the release tag name: a registry-side
    manifest copy (`docker buildx imagetools create`), no pull, no build. The
    source SHA is the commit the release tag points at, resolved from the tag
    itself — never from `github.sha`, which on `release` events is the
    release's target-commitish HEAD (usually the default-branch tip) and on
    `workflow_dispatch` the dispatched ref's HEAD, neither of which is
    guaranteed to be the tagged commit, and which no step can mutate.
    Accepted resolution forms: checking out the tag (`actions/checkout` with
    `ref:` the tag) and reading the checked-out commit (`git rev-parse HEAD`
    or the checkout action's `commit` output), or `git ls-remote` against the
    tag ref (with the `^{}` dereference for annotated tags — a lightweight
    release tag resolves directly); the retag step then names its source
    `sha-<resolved SHA>` — literally in its `run:` or via a step `env:`
    variable, the accepted forms — and its target tag name and image
    reference appear in the retag step's `run:` or `env:` the same way. The
    release tag therefore carries the exact digest of the image the main-push
    build (rule 14) produced for that commit: a rebuild could drift (base
    image, transitive resolution) between merge and release, and the release
    must point at the bits CI built and published (ADR
    `decisions/0010-release-retag-not-rebuild.md`). The source `sha-<SHA>`
    tag must exist — a release of a commit whose main build failed or never
    ran fails the job loudly (no `continue-on-error` on the retag); it is an
    error, not a silently missing tag. The release job runs only for tags
    starting with `v` — an `if:` guard, because the `release` trigger has no
    tag filter; non-`v` published releases leave the job skipped. The release
    job never runs `docker/build-push-action`, never writes the `main` or
    `sha-*` tags (it necessarily reads the `sha-<SHA>` source), publishes no
    `latest` tag (`main` stays the rolling tag),
    and authenticates exactly per rule 15.
18. `workflow_dispatch` is the manual retag path [DONE: #36]: a required
    input `tag` (the release tag to apply). The release job (or a dedicated
    dispatch sibling) resolves the commit the input tag points at — per
    rule 17's tag resolution, never `github.sha` — then applies rule 17's
    retag: source `sha-<SHA>` of the tagged commit, target the input tag, no
    build, no `main`/`sha-*` writes, fail-loud on a missing source. The
    `v`-prefix guard applies to the input
    exactly as to release events, so a non-conforming input leaves the job
    skipped with no registry write. This is the retro path for releases that
    predate the trigger (v0.1) and the recovery path when the release job
    failed.

## Constraints

- Same code path as dev: the image runs the same single-file `patrol.py`; only
  the dependency-installation mode differs (ADR 0005).
- The image changes no read-only guarantee: the shipped `oc` is a full client, so
  the guard (`kubectl-guard.md`) and the in-cluster RBAC (`safety-model.md`
  layer 3, #2) remain the enforcement layers — the image adds none.
- PoC grade applies (constraints.md rule 6): base-image digest pinning,
  layer-size tuning, and multi-arch builds are explicitly out of scope.
- **CI is delivery, not verification**: post-merge means a broken build cannot
  block the merge that broke it (the gate is human review), and the workflow
  does not re-run rule 12's acceptance checks — automating them inside the
  workflow is a possible follow-up, not planned.
- Rolling `:main` caveat: a Deployment consuming it does not restart when the
  tag's digest changes — updating the running patroller is apply + rollout
  restart (`in-cluster-deployment.md` rule 17).
- Release tags are pointers, not artifacts: a release tag is a manifest copy
  of that commit's `sha-<SHA>` image, so it depends on the main-push pipeline
  having succeeded for that commit — releasing an unbuilt commit fails loudly
  instead of publishing (ADR `decisions/0010-release-retag-not-rebuild.md`)
  [DONE: #36].
- The CI contract (rules 13–18) is unit-checked statically in
  `tests/test_image_ci.py`, which parses `.github/workflows/image.yml` and
  fails on a missing or non-conforming workflow — no workflow run needed
  [rules 13–16: DONE: #2] [rules 17–18: DONE: #36].

## Planned Changes

| Ticket | Summary |
|---|---|
| ~~#1~~ | This file's own contract: Containerfile + `requirements.txt` (rules 1–12) |
| ~~#2~~ | Manifests consuming this image (`in-cluster-deployment.md`) + the post-merge CI that publishes it to GHCR (rules 13–16) |
| ~~#25~~ | Default cadence 6 h: CMD `--interval 21600` (rule 9), kept identical to the Deployment args and the `patrol.py` env default |
| ~~#36~~ | Release image tags: published `v*` releases retag the matching `sha-<SHA>` image — no rebuild — plus a `workflow_dispatch` manual retag path (rules 17–18; ADR 0010). Impl commit also syncs the image-CI entries in `how/project-structure.md`, `ARCHITECTURE.md`, and the `image.yml` pointer in `in-cluster-deployment.md`'s Configuration Surface |
| ~~#5~~ | `pyyaml` joins the pinned runtime set (rules 6, 12) — the wiki skeleton's frontmatter parser (`wiki-memory.md` rule 15) |
| #9 | `apprise` joins the pinned runtime set and `requests` drops from it (rules 6, 12) — multi-sink notifications (`reporting-notifications.md` rules 10a–10c) |
