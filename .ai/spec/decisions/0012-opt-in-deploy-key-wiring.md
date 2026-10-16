# 0012. Opt-in deploy-key wiring via a committed example patch

Date: 2026-10-16 · Status: proposed

Numbered 0012, not 0011: the in-flight #5 spec PR (#38) already takes 0011
for the wiki-skeleton ADR; this record steps past the number to avoid a
collision.

## Context

The wiki's optional off-cluster replication (`wiki-memory.md` rule 4e,
`PATROL_WIKI_REMOTE`) needs credentials when the remote is an SSH git host —
`git push` must authenticate. Credentials are deployment surface (the image
bakes no secrets, `container-image.md` rule 10), and the base manifests must
stay self-contained: `oc apply -k` has to work with zero git wiring, because a
referenced-but-missing Secret blocks pod creation
(`in-cluster-deployment.md` rule 16, from #2). Until #39 the wiring existed
only as prose inside that rule — an operator asking "how do I push my wiki to
GitHub" had nothing to copy.

## Decision

Opt-in copy-in, mirroring the established `secret.yaml.example` procedure
(rule 14):

- a **committed template** `manifests/deploy-key-patch.yaml.example` — a
  strategic-merge patch over the base Deployment that adds a `deploy-key`
  secret volume (`ols-patroller-deploy-key`, `defaultMode: 0440`) mounted
  read-only at `/wiki-deploy-key`, and the literal env `GIT_SSH_COMMAND`
  (TOFU `StrictHostKeyChecking=accept-new`; a fully pinned `known_hosts`
  variant is documented as commented lines);
- the **private key lives server-side only**: `oc create secret generic
  ols-patroller-deploy-key --from-file=id_ed25519=…` — never a committed or
  gitignored manifest, so the base kustomization never references it;
- the operator **copies the template in** (`deploy-key-patch.yaml`,
  gitignored like `secret.yaml`) and adds one `patches:` entry to their
  `kustomization.yaml`;
- the README's in-cluster deployment section carries the full procedure;
- **README, not the GitHub wiki**, as the doc surface: the repo's GitHub wiki
  is disabled and the README already owns the operator-facing role
  (`system-overview.md` rule 16's doc-surface consistency); a second doc
  home would fragment it.

The `0440` mode is load-bearing under the SCC: secret volume files mount
`root:root` with the volume's `defaultMode`, and the pod runs as an arbitrary
UID whose primary GID is 0 (the image's `USER 1001:0` stands under the
restricted SCC), so group-read is exactly what makes the key readable.
OpenSSH's too-open check fires only for files owned by the calling uid, so
the root-owned key passes it — `0400` would be root-only (unreadable by the
pod), `0644` needlessly world-readable.

## Alternatives

- **Bake an optional mount into the base manifests** — rejected: any
  reference to a possibly-missing Secret breaks the self-contained apply
  (rule 16's reason for being).
- **A kustomize overlay directory** (`overlays/with-remote/`) — deferred: a
  second kustomize root to maintain for one patch; the copy-in matches the
  `secret.yaml` procedure operators already follow. An overlay becomes worth
  it when there is more than one variant to carry.
- **`oc patch` on the live Deployment** — rejected: not declarative; the
  running wiring would drift from what `oc apply -k` rebuilds.
- **HTTPS + token auth** instead of SSH — workable, but the token then sits
  in the git remote URL that the patroller writes into the wiki repo's git
  config; a deploy key keeps the secret out of any file the patroller
  touches. Not forbidden, just not the documented path.

## Consequences

Enabling replication is a documented copy-and-edit procedure; the base
manifests stay clean and `oc apply -k` keeps working with no git wiring at
all. A missing deploy-key Secret (patch applied, Secret forgotten) blocks pod
creation visibly — consistent with fail-loudly — while a failing push never
blocks a patrol (local-first, rule 4e). No patrol behavior changes:
`GIT_SSH_COMMAND` is inherited by git's ssh through `_run_git`'s env
passthrough; `patrol.py` is untouched. One asymmetry is inherent: the opt-in
edits the tracked `kustomization.yaml` — unlike the gitignored
`secret.yaml` / `deploy-key-patch.yaml` copies — so the base's no-`patches:`
guard is scoped to the committed blob (HEAD), keeping an opted-in operator's
local suite green while still pinning what this repo ships.
