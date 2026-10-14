# Container Image (in-cluster runtime)

The ubi9-minimal image that runs `patrol.py` in-cluster, built from the repo-root
`Containerfile` (#1). The image is what makes the patroller deployable; the
Kubernetes wiring around it (ServiceAccount, cluster-reader RBAC, PVC, Deployment)
is #2 and makes `safety-model.md` layer 3 real. Merges to `main` also publish the
image to GHCR from CI (rules 13–16, #2) — that published image is what the
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
   `langchain-openai`, `pydantic`, `requests`). Transitive dependencies are
   resolved by pip at build time and are not listed; dev-only tools (e.g.
   pytest) never appear [DONE: #1].
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
   command is `patrol.py --loop --interval 900 --wiki /wiki/cluster-wiki.md` —
   `ENTRYPOINT` invokes the script via the image's `python3.12`; runtime wiring
   (Deployment args, Secret-sourced env) belongs to the #2 manifests and may
   override the default command [DONE: #1].
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
    - `python3.12 -c "import deepagents, langchain_openai, pydantic, requests"`
      succeeds inside the container (build-time dependency install);
    - `podman run --rm <image>` with no `PATROL_*` env exits non-zero with the
      standard missing-env error (`system-overview.md` rule 6).
    A patrol inside the container against a real dev cluster — through the #2
    manifests — is #2's done-when, not this spec's.

### Continuous Build & Publication (CI)

13. Merges to `main` publish the image: the GitHub Actions workflow
    `.github/workflows/image.yml` triggers **only** on `push` to `main` — no
    `pull_request` trigger, no other branches or tags (post-merge artifact
    pipeline, not a merge gate; PR-time builds are deliberately excluded — CI
    stays free on draft/spec work). No path filter: every merge republishes
    `:main`, keeping the tag equal to "latest main" (accepted slack)
    [DONE: #2].
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

## Planned Changes

| Ticket | Summary |
|---|---|
| ~~#1~~ | This file's own contract: Containerfile + `requirements.txt` (rules 1–12) |
| ~~#2~~ | Manifests consuming this image (`in-cluster-deployment.md`) + the post-merge CI that publishes it to GHCR (rules 13–16) |
