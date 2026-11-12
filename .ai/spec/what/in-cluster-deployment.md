# In-Cluster Deployment (manifests)

The kustomize manifest set that runs the container image (`container-image.md`)
on the cluster it watches, as a single-replica Deployment with a read-only
identity and a persistent wiki volume. This is what makes `safety-model.md`
layer 3 (RBAC boundary) real, and what the CI-published image
(`container-image.md` rules 13–16) is deployed by.

## Behavioral Rules

### Layout & Apply

1. The manifests live in `manifests/` at the repo root as a kustomize root:
   `oc apply -k manifests/` applies the whole set. The directory's complete
   contents are `kustomization.yaml` (listing exactly six resources:
   `namespace.yaml`, `serviceaccount.yaml`, `clusterrolebinding.yaml`,
   `pvc.yaml`, `secret.yaml`, `deployment.yaml`), the committed
   `secret.yaml.example` template — a template, not a resource (rule 14) —
   the committed `deploy-key-patch.yaml.example` template — likewise a
   template, not a resource (rule 16a) — the committed
   `pvc-patch.yaml.example` template — likewise a template, not a resource
   (rule 5a) [DONE: #68] — the committed
   `egress-networkpolicy.yaml.example` template — likewise a template, not a
   resource (`egress-control.md` rule 8) [DONE: #34] — and, once the
   operator follows
   rule 14, the gitignored `secret.yaml` itself, plus, once the operator
   follows rule 16c, the gitignored `deploy-key-patch.yaml` copy and the
   matching `patches:` entry in the operator's `kustomization.yaml`, plus,
   once the operator follows rule 5a, the gitignored `pvc-patch.yaml` copy
   and its matching `patches:` entry [DONE: #68], plus, once the operator
   follows `egress-control.md` rule 8, the gitignored
   `egress-networkpolicy.yaml` policy artifact — applied out-of-band with
   `oc apply -f`, never a resource of this kustomization [DONE: #34];
   nothing else [DONE: #2] [example templates + opt-in copy: DONE: #39].
2. Every namespaced object carries the literal `metadata.namespace:
   ols-patroller`, the `ClusterRoleBinding`'s subject namespace is the same,
   and the `kustomization.yaml` sets **no** `namespace:` field — the kustomize
   namespace transformer does not rewrite `ClusterRoleBinding` subjects, so
   hardcoded literals (consistency enforced by the static tests) are the
   safe form. Changing the deployment namespace is an edit across the files,
   not one field [DONE: #2].

### Identity & RBAC (safety layer 3)

3. A single `ServiceAccount` named `ols-patroller` in the namespace; the
   Deployment's pod template sets `serviceAccountName: ols-patroller`. The SA
   token automounts (k8s default) — the image's `oc` authenticates via the
   pod's in-cluster service account and `KUBERNETES_SERVICE_HOST`; no
   kubeconfig is mounted or built (verified in the #1 container run) [DONE:
   #2].
4. A single `ClusterRoleBinding` named `ols-patroller` binds that SA to the
   **built-in** `ClusterRole` `cluster-reader` (`roleRef`: apiGroup
   `rbac.authorization.k8s.io`, kind `ClusterRole`, name `cluster-reader`; one
   subject: the SA). It is the only RBAC object in the manifests — no
   `ClusterRole`, `Role`, or `RoleBinding` is defined anywhere in the repo: the
   built-in role is the entire grant (ADR
   `decisions/0006-cluster-reader-binding.md`). This binding is
   `safety-model.md` layer 3 — the server-side enforcement boundary; the
   `kubectl_readonly` guard is defense-in-depth on top [DONE: #2].

### Persistence (the wiki volume)

5. A `PersistentVolumeClaim` named `ols-patroller-wiki`: `accessModes`
   `[ReadWriteOnce]`, storage request `1Gi`, **no** `storageClassName` (the
   cluster's default storage class applies — most portable for a dev cluster;
   override by patch when the default class is wrong). The Deployment mounts
   it at `/wiki` (volume name `wiki`, `claimName` matching the PVC) — the wiki
   directory is the PVC mount (`wiki-memory.md` rule 4a) [DONE: #2].
5a. **The storage-class override patch** [DONE: #68]. When the cluster
   has no default storage class (check `oc get sc`: a class is default only
   when annotated `storageclass.kubernetes.io/is-default-class: "true"`),
   the base PVC never binds, and any class applied server-side becomes
   immutable once the claim is bound — a live class the manifest doesn't
   declare makes every `oc apply -k manifests/` fail on the PVC (the other
   objects still apply; the exit code and the noise still break scripted
   use). The override is the deploy-key pattern (rules 16a–16c, ADR 0012)
   applied to the volume: a committed `manifests/pvc-patch.yaml.example`
   template (a strategic-merge patch over the base PVC setting exactly
   `spec.storageClassName`, nothing else) that the operator copies to a
   gitignored `manifests/pvc-patch.yaml`, edits to their class name, wires
   with a `patches:` entry in their local `kustomization.yaml`, and
   re-applies. Setting the class at first-deployment time is the same patch
   (the class must be set before the claim binds). README's in-cluster
   deployment section documents the procedure end-to-end under a
   `### No default storage class?` subsection (the rule-16d standard: an
   operator can enable it from the README plus the committed template
   alone).
6. **Volume writability under the SCC.** The pod's `securityContext` sets
   `runAsNonRoot: true` and sets **no** `runAsUser`, `runAsGroup`, or
   `fsGroup`. Under the cluster-default restricted SCC (`restricted-v2`), the
   pod runs non-root (the image's `USER 1001:0` stands under
   `MustRunAsNonRoot`) and admission assigns an `fsGroup` from the namespace's
   group range (MustRunAs default); the kubelet applies that GID to the RWO
   volume (group ownership, setgid, group-write on the volume root) and adds
   it to the container's supplemental groups. That group-write is what makes
   `/wiki` writable — any mount shadows the image's `/wiki` prep, and a fresh
   volume is root-owned without it (verified in the #1 container run: a
   `755` root-owned bind mount → `PermissionError` creating the wiki). No
   init-container permission fixup: chmod on a root-owned volume requires
   root, i.e. the `anyuid` SCC — a privilege escalation the read-only posture
   forbids (ADR `decisions/0007-scc-fsgroup-writable-wiki.md`) [DONE: #2].

### The Deployment

7. The Deployment is named `ols-patroller` — exactly one replica
   (`replicas: 1`) with `strategy: Recreate`. The default
   `RollingUpdate` at one replica is maxSurge 1 / maxUnavailable 0: a surge
   pod can never mount the RWO volume until the old pod dies, so the rollout
   stalls; `Recreate` also guarantees no two-writer window on the single-writer
   wiki (`wiki-memory.md` Constraints) [DONE: #2].
8. The container image is the literal name `ols-patroller` (no registry, no
   tag — a stable key for the kustomize `images` transformer); the
   kustomization's `images:` entry maps it to the CI-published
   `ghcr.io/thoraxe/ols-patroller:<current-release-tag>`, where the default
   tag is the **current release** — the newest published `vX.Y.Z` release tag
   (`container-image.md` rules 17 and 19), never the rolling `:main` tag.
   Applying the committed manifests therefore deploys a known release, not the
   latest merge. During a release cut (the pin commit has merged but the
   release has not been published/retagged yet) the manifest already names the
   incoming tag but the tag is not pullable yet — apply only after the release's
   retag has completed (rule 17 step 6, `container-image.md` rule 19). The same entry is the single override point for clusters that
   must pull from elsewhere (private registry, the cluster's internal
   registry) and for deliberately testing the latest merge (`:main`, rule 8b);
   an override is local and never changes the committed release default
   [DONE: #2] [release-pinned default: DONE: #102].
8a. **Mutable tag, `imagePullPolicy: Always`** [DONE: #61]. The patrol
   container sets `imagePullPolicy: Always` while the deployed tag is a
   mutable tag — the release tag the manifests pin by default (a `vX.Y.Z`
   tag whose registry manifest is written by CI's release retag,
   `container-image.md` rules 17–19) or an operator's local `:main`/`sha-…`
   override (rule 8b). A mutable tag under the k8s-default
   `IfNotPresent` makes every `rollout restart` a silent no-op whenever
   the node has the tag cached: the rollout succeeds, the pod runs
   healthy, and the cluster keeps the stale image — exactly how a fixed
   cluster keeps running a broken build (real-run evidence, #61: a
   rollout after merging three fixes came up on the pre-fix image; the
   deployment had to be pinned to a `sha-<full SHA>` tag by hand, and the
   live Deployment then no longer matched the committed manifest).
   `Always` costs one registry round-trip per restart and makes the
   documented release path (rule 17 step 6) actually roll. The invariant
   is pinned by the static suite (`test_manifests.py`): whenever the
   kustomization `images:` tag is mutable — not a `sha-…` pin and not a
   digest — the container must set `imagePullPolicy: Always`. Pinning
   the tag to `sha-<full SHA>` (or a digest) is the other legal form
   (immutable, reproducible); an operator who pins may drop the policy
   back to the default.
8b. **Local image override** [DONE: #102]. Overriding the image is a
   supported, local operation and never changes the committed default: the
   operator edits the `images:` entry in their working tree (uncommitted),
   uses a kustomize overlay, or runs
   `oc set image deploy/ols-patroller ols-patroller=<ref>`. The supported
   "test the latest merge" override is `:main` (or a specific `sha-<SHA>`
   pin); the supported "another registry" override is any pullable ref.
   The committed `newTag` remains a `vX.Y.Z` release tag (rule 8) — a change
   to the release default is a release-pin commit (`container-image.md` rule
   19), not a local override. The override works with the same pull policy:
   a mutable override tag follows rule 8a, an immutable one may drop it.
   README's in-cluster deployment section documents the procedure (the
   rule-16d standard: README plus the committed manifests suffice): it MUST
   carry the override heading `Test the latest merge`, the `newTag: main`
   form, the `oc -n ols-patroller set image deploy/ols-patroller` form, a
   `sha-<SHA>` form, and the words `another registry`; and the README
   (anywhere — the superseded sentence lives in the security section) MUST NOT
   state that the committed manifests deploy `:main` by default (the
   superseded claim). The static suite pins the committed default's **exact current
   release tag** (not just its shape) and those README tokens.
9. The container args are exactly `--loop --interval 21600 --wiki
   /wiki/cluster-wiki.md` (identical to the image's default CMD; stated
   explicitly so the manifests are self-describing) [DONE: #2] [DONE:
   #25 — cadence 900 s → 6 h, the resident-SRE posture; the fast demo loop
   stays available via an explicit `--interval`].
10. **Env wiring.** `PATROL_API_KEY`, `PATROL_BASE_URL`, `PATROL_MODEL` (and
    `PATROL_NOTIFY_URLS` / `PATROL_WIKI_REMOTE` when present in the Secret)
    [DONE: #9 — the optional notification key is `PATROL_NOTIFY_URLS`]
    arrive **only** via `envFrom` → `secretRef` on the Secret named
    `ols-patroller` — no committed manifest carries a literal value for any of
    them (mirrors `container-image.md` rule 10). `PATROL_CLUSTER_ID` is set as
    a plain literal `env` entry (not secret material): a placeholder the
    operator edits to the real cluster name for wiki commit identity
    (`wiki-memory.md`) [DONE: #2]. The value must be a slug. The slug rule is
    `wiki-memory.md` rule 4a1. The Deployment never leaves this value at `local`
    [PLANNED: #89 — the slug rule and the cluster branch].
10a. `PYTHONUNBUFFERED=1` is a literal `env` entry (rule 10's non-secret
    pattern). Python block-buffers stdout when it is not a TTY — in a
    container stdout is a pipe — so without it the patrol's stdout lines
    (findings block, notifications) can sit in the stdio buffer across
    patrols while `oc logs` stays empty even though the patrol is healthy
    (observed in the rule-17 acceptance run); the env entry makes stdout
    unbuffered so pod output appears as it prints [DONE: #24].
10b. The wiki repo is git-safe for **any** in-container invocation via the
    literal env trio `GIT_CONFIG_COUNT=1`, `GIT_CONFIG_KEY_0=safe.directory`,
    `GIT_CONFIG_VALUE_0=/wiki`. The PVC mount point `/wiki` is root-owned
    (the kubelet creates it; rule 6's SCC-supplied fsGroup is what makes it
    writable), so git's CVE-2022-24765 ownership check rejects the repo for
    every non-root caller — including the human `oc exec … git -C /wiki
    log` that rule 17 step 4's procedure documents. The patrol itself already
    passes `-c safe.directory` per command (`wiki-memory.md` rule 4d); this
    trio covers every other caller. Env, not an image-baked
    `/etc/gitconfig`: `/wiki` is pinned at this deployment surface, not in
    the image [DONE: #28].
11. **Security context.** Pod: `runAsNonRoot: true`, `seccompProfile:
    RuntimeDefault`, nothing else (rule 6 forbids the identity fields).
    Container: `allowPrivilegeEscalation: false`, `capabilities: drop:
    [ALL]`. The pod must admit under the cluster-default restricted SCC with
    **no SCC grant**: no root, no privilege escalation, no added capabilities,
    no host namespaces, no hostPath volumes, no `privileged` [DONE: #2].
12. Resources: requests `cpu: 100m` / `memory: 256Mi`, limits `cpu: "1"` /
    `memory: 512Mi` (the patrol is a Python process mostly waiting on the
    model endpoint; the values are defaults — the contract is that requests
    and limits are both set for CPU and memory) [DONE: #2].
13. The Deployment and its pod template carry the label `app: ols-patroller`;
    the selector's `matchLabels` matches it (this is what the acceptance
    procedure's `delete pod -l app=ols-patroller` keys on) [DONE: #2].

### The Secret

14. `manifests/secret.yaml` is **gitignored** — real credentials are never
    committed — and the kustomization lists it, so `oc apply -k` fails loudly
    until the operator creates it. The committed template
    `manifests/secret.yaml.example` is a Secret named `ols-patroller` whose
    `stringData` holds the three required keys (`PATROL_API_KEY`,
    `PATROL_BASE_URL`, `PATROL_MODEL`) set to obvious placeholder values, with
    the optional keys (`PATROL_NOTIFY_URLS`, `PATROL_WIKI_REMOTE`) present as
    commented-out lines — the `PATROL_WIKI_REMOTE` placeholder shows the
    SSH-remote form and points at the deploy-key wiring (rules 16a–16c)
    [DONE: #39] [DONE: #9 — the optional notification key is
    `PATROL_NOTIFY_URLS`, one apprise URL per line in the Secret; replaces the
    removed `SLACK_WEBHOOK_URL` (a Slack incoming webhook is expressed as its
    apprise `slack://` URL)]. Procedure: `cp secret.yaml.example secret.yaml`,
    fill in, apply [DONE: #2].
15. No committed manifest contains a real or plausible value for any secret
    env key: those key names appear in committed manifests only inside the
    `.example` templates (`secret.yaml.example` placeholder values,
    `deploy-key-patch.yaml.example` comments), never with real or plausible
    values [DONE: #2] [template scope: DONE: #39].

### Optional remote-push wiring (deploy key)

16. The base manifests never reference a deploy-key secret, an SSH key mount,
    or `GIT_SSH_COMMAND`, and the committed `kustomization.yaml` carries no
    `patches:` entry — `oc apply -k` stays self-contained (a referenced-
    but-missing Secret blocks pod creation). Remote-push wiring
    (`wiki-memory.md` rule 4e) is opt-in, never required for a patrol
    (local-first): a committed example patch (rule 16a) plus the documented
    procedure (rules 16c–16d) [DONE: #2] [example + procedure:
    DONE: #39].
16a. `manifests/deploy-key-patch.yaml.example` is a committed template (the
    same pattern as rule 14's `secret.yaml.example`) holding a
    strategic-merge patch over the base Deployment: `apiVersion: apps/v1`,
    kind `Deployment`, name and namespace `ols-patroller`, carrying only
    `spec.template.spec` with the base container's merge key
    (`ols-patroller`) and **no** `image:` — the patch is additive and can
    never drift the image key. It adds exactly: a `deploy-key` volume —
    `secretName: ols-patroller-deploy-key`, `defaultMode: 0440`; its
    read-only mount at `/wiki-deploy-key` on the patrol container; and the
    one literal env entry `GIT_SSH_COMMAND: ssh -i /wiki-deploy-key/id_ed25519
    -o StrictHostKeyChecking=accept-new`. `GIT_SSH_COMMAND` is not secret
    material (rule 10's literal ban covers only the Secret-sourced keys); it
    is consumed by git's ssh during the push — `patrol.py` passes the process
    env through to git unchanged, so no patrol code changes. The `0440` is
    load-bearing under the SCC (stated in the template's comments): secret
    volume files mount `root:root` with the volume's `defaultMode`, and the
    pod runs as an arbitrary UID whose primary GID is 0 (the image's
    `USER 1001:0` stands under the restricted SCC), so group-read `0440` is
    exactly what makes the key readable — `0400` would be root-only,
    `0644` needlessly world-readable — and OpenSSH's too-open check fires
    only for files owned by the calling uid, so the root-owned key passes
    it [DONE: #39].
16b. The same template documents the fully pinned variant as commented
    lines: a `known_hosts` entry added to the deploy-key Secret and the
    `GIT_SSH_COMMAND` form with `StrictHostKeyChecking=yes -o
    UserKnownHostsFile=/wiki-deploy-key/known_hosts`. TOFU (`accept-new`)
    is the committed default posture; pinning is the hardened alternative
    [DONE: #39].
16c. **The opt-in procedure** (ADR
    `decisions/0012-opt-in-deploy-key-wiring.md`):
    1. generate an ed25519 key pair for the wiki remote;
    2. register the public key with the git host as a **write** deploy key
       on the wiki repo;
    3. create the Secret server-side only — `oc -n ols-patroller create
       secret generic ols-patroller-deploy-key --from-file=id_ed25519=<path>`
       — never as a committed or gitignored manifest (the patch references
       it by name; a missing Secret then blocks pod creation visibly,
       fail-loudly, rather than silently skipping replication);
    4. `cp manifests/deploy-key-patch.yaml.example
       manifests/deploy-key-patch.yaml` (gitignored like `secret.yaml`)
       and add one entry to `kustomization.yaml`: `patches: [{path:
       deploy-key-patch.yaml}]`;
    5. set `PATROL_WIKI_REMOTE` to the SSH remote (e.g.
       `git@github.com:you/cluster-wiki.git`) in the patrol Secret;
    6. `oc apply -k manifests/` [DONE: #39].
16d. `README.md`'s in-cluster deployment section (a `## Deploy in-cluster`
    heading with a `### Remote wiki replication (optional)` subsection)
    documents the procedure end-to-end and self-sufficiently — an operator
    can enable replication from the README plus the committed templates
    alone, with no reference to `.ai/spec/` [DONE: #39].

### Acceptance (done-when)

17. End-to-end acceptance is the procedure below, performed on a dev cluster
    with `oc` (and the CI-published image; documented **in this rule**, not in
    the pytest suite — same pattern as `container-image.md` rule 12):
    1. the image is pullable: after CI's first publish, flip the GHCR package
       visibility to public once (repo is public), or override the image ref
       (rule 8) to a registry the cluster pulls;
    2. `cp manifests/secret.yaml.example manifests/secret.yaml`, fill it in,
       `oc apply -k manifests/`;
    3. RBAC check: `oc auth can-i get pods -A
       --as=system:serviceaccount:ols-patroller:ols-patroller` → yes; the same
       with `delete pod` → no;
    4. a patrol completes: pod logs show findings and notifications, and the
       wiki commit lands (`oc -n ols-patroller exec deploy/ols-patroller --
       git -C /wiki log --oneline` grows);
    5. persistence across restarts: `oc -n ols-patroller delete pod -l
       app=ols-patroller` — the replacement pod's next patrol reads the
       existing wiki and the commit numbering continues (patrol N increments
       across the restart);
    6. updating to a new release: after the release has been published and
       its retag job has completed (`container-image.md` rules 17/19 — the
       pin commit merging alone is not enough; the `vX.Y.Z` image tag does not
       exist until the retag), `oc apply -k manifests/` changes the pod
       template's image tag and therefore triggers the rollout on its own.
       For a same-tag refresh (a re-tagged release, or a local `:main`
       override, rule 8b), the template does not change — add
       `oc -n ols-patroller rollout restart deployment/ols-patroller`
       [DONE: #61 — the container pulls `Always` (rule 8a), so a restart
       never reuses the node's cached tag] [DONE: #102];
    7. optional, with the deploy-key wiring enabled (rule 16c): the next
       patrol's push lands — the remote's copy of the wiki advances one
       commit. Verify in the git host's web view (the patrol pushes to the
       raw `PATROL_WIKI_REMOTE` URL — no `origin` remote is configured in
       the wiki repo — so there is no remote-tracking ref to `git log` in
       the pod; `git ls-remote <remote-url>` from anywhere shows the remote
       head) [DONE: #39].

## Configuration Surface

- `manifests/` — the kustomize root (files per rule 1); namespace and object
  names are per-file literals kept consistent by tests (rule 2)
- kustomization `images:` entry — the image override point (rule 8); the
  committed default is the current release tag, a local override is rule 8b
- container `imagePullPolicy` — `Always` while the deployed tag is mutable
  (rule 8a); may be dropped when the tag is pinned to `sha-…` or a digest
  [DONE: #61]
- `manifests/pvc-patch.yaml.example` — committed template for the optional
  storage-class override patch (rule 5a); the operator's gitignored
  `manifests/pvc-patch.yaml` copy + `patches:` entry in their local
  `kustomization.yaml` is the opt-in [DONE: #68]
- `PATROL_CLUSTER_ID` — literal env in the Deployment, edit to the real
  cluster name (rule 10). It is a slug (`wiki-memory.md` rule 4a1)
  [PLANNED: #89]
- `PYTHONUNBUFFERED` — literal env `1`; unbuffered stdout so pod logs show
  patrol output as it prints (rule 10a)
- `GIT_CONFIG_COUNT` / `GIT_CONFIG_KEY_0` / `GIT_CONFIG_VALUE_0` — literal
  env trio setting `safe.directory=/wiki` for in-container git (rule 10b)
- Secret keys — required `PATROL_API_KEY`, `PATROL_BASE_URL`,
  `PATROL_MODEL`; optional `PATROL_NOTIFY_URLS` (apprise URL form,
  `reporting-notifications.md` rule 10a) [DONE: #9], `PATROL_WIKI_REMOTE` (rule 14)
- `manifests/deploy-key-patch.yaml.example` — committed template for the
  optional remote-push patch; the operator's gitignored
  `deploy-key-patch.yaml` copy + the `patches:` entry in their
  `kustomization.yaml` are the opt-in (rules 16a, 16c) [DONE: #39]
- `manifests/egress-networkpolicy.yaml.example` — committed template for the
  optional default-deny egress allowlist; the operator's gitignored
  `egress-networkpolicy.yaml` is rendered with `--egress-policy` (or copied
  from the template) and applied with `oc apply -f`, never as a kustomization
  resource [DONE: #34] (`egress-control.md`)
- `GIT_SSH_COMMAND` — literal env added by the opt-in patch; consumed by
  git's ssh, not by `patrol.py` (rule 16a) [DONE: #39]
- `.github/workflows/image.yml` — the CI that publishes the deployed image
  and retags published `v*` releases onto the matching `sha-<SHA>` image
  (`container-image.md` rules 13–19)

## Constraints

- The RBAC grant is exactly the built-in `cluster-reader` binding (rule 4,
  ADR 0006) — nothing wider, and nothing narrower without a superseding ADR.
- `/wiki` writability depends on the SCC-assigned fsGroup (rule 6, ADR 0007):
  a namespace whose SCC strategy does not assign an fsGroup, or a storage
  class that ignores it (e.g. older CephFS with `FSGroupPolicy: None`),
  leaves the volume root-owned — the first patrol then fails loudly
  (`PermissionError` creating the wiki), a visible signal rather than silent
  degradation.
- Single replica forever: the wiki is single-writer (`wiki-memory.md`
  Constraints); HA / leader election is out of scope.
- Pulling `ghcr.io/thoraxe/ols-patroller` requires the GHCR package public
  (one-time flip) or an image pull secret patched in — the base manifests
  assume public and set no `imagePullSecrets`.
- Remote-push wiring stays opt-in (rule 16, ADR 0012): the base manifests
  never reference it; enabling it is a copy-and-edit procedure. The push
  itself remains best-effort and local-first (`wiki-memory.md` rule 4e) —
  the wiring changes where commits replicate, never whether a patrol
  succeeds [DONE: #39].
- PoC grade applies (`constraints.md` rule 6): no probes (no HTTP surface; the
  loop self-heals via Deployment restarts), no monitoring/telemetry wiring, no
  priority class. The human surface in-cluster is pod logs + notifications.
  Network egress control is **no longer exempt**: it is the opt-in derived
  default-deny allowlist of `egress-control.md` (rules 1–10, ADR 0030),
  applied out-of-band and never part of the committed base (rule 1)
  [DONE: #34 — this bullet previously read “no NetworkPolicy”; #34
  supersedes that sentence].
- The deployment adds no patrol behavior: same `patrol.py`, same guard, same
  env contract — wiring only. No new **patrol** configuration exists; the only
  change to `system-overview.md`'s Configuration Surface is a pointer to this
  deployment surface.

## Planned Changes

| Ticket | Summary |
|---|---|
| #89 | `PATROL_CLUSTER_ID` must be a slug, because it is the cluster-branch ref component (rule 10's amendment; behavior is `wiki-memory.md` rules 4a1/4f1, ADR 0039). The manifest comment names the rule. The `/wiki` mount, `--wiki /wiki/cluster-wiki.md`, the PVC, and the one-document constraint do not change. The impl commit flips the rule-10 marker once implemented and syncs `README.md`'s operator procedure |
| ~~#102~~ | The manifests default to the **current release tag**, not `:main` (rule 8's rewrite + new rule 8b's local override + rule 8a's mutable-tag wording + rule 17 step 6's release-update path; contract behavior is `container-image.md` rules 17/19, ADR 0035): `oc apply -k manifests/` deploys the newest published `vX.Y.Z` release, and testing the latest merge or another registry is an explicit local override (uncommitted `images:` edit, overlay, or `oc set image`). Release cutting pins before tagging (`container-image.md` rule 19), and a published tag is never moved. README documents the override and the release order; the static suite pins the default's exact current release tag (flipping the old `newTag == main` assertion) and the README procedures. The v0.3.2 release predates the rule; its pin lands on `main` in the implementation commit, without moving the tag. Impl commit also syncs `AGENTS.md`'s test list and the stale `manifests/deployment.yaml`/`image.yml` rule-range comments |
| ~~#34~~ | ~~The optional egress allowlist artifact (rule 1's directory amendment + the Configuration Surface entry): a committed `manifests/egress-networkpolicy.yaml.example` template, the gitignored operator `egress-networkpolicy.yaml` rendered by `--egress-policy` and applied with `oc apply -f`, and the superseded “no NetworkPolicy” constraint. Contract: `egress-control.md` (ADR 0030)~~ |
| ~~#68~~ | ~~The storage-class override patch (rule 5a + the rule 1 directory amendment): a committed `manifests/pvc-patch.yaml.example` strategic-merge template setting exactly `spec.storageClassName`, the gitignored operator copy + `patches:` opt-in (the deploy-key pattern), and the README procedure. Impl commit also syncs `AGENTS.md`'s test list~~
| ~~#61~~ | ~~`imagePullPolicy: Always` on the patrol container while the kustomization tag is rolling (rule 8a), the static-suite invariant (mutable tag → Always; `sha-…`/digest pin is the other legal form), and rule 17 step 6's image-pull premise stated. Impl commit also syncs `AGENTS.md`'s test list~~
| ~~#2~~ | This file's contract: manifests (rules 1–17) + the CI workflow that publishes the deployed image (`container-image.md` rules 13–16) |
| ~~#24~~ | `PYTHONUNBUFFERED=1` literal env — unbuffered stdout for pod-log visibility (rule 10a) |
| ~~#25~~ | Default cadence 6 h: args `--interval 21600` (rule 9), with the image CMD (`container-image.md` rule 9) and the `patrol.py` env default kept identical |
| ~~#28~~ | `GIT_CONFIG_*` safe.directory trio — `oc exec` git access to `/wiki` works un-flagged (rule 10b) |
| ~~#39~~ | Deploy-key wiring: committed `deploy-key-patch.yaml.example` (rules 16a–16b), gitignored copy + `patches:` opt-in (rule 16c), README in-cluster deployment section (rule 16d) |
