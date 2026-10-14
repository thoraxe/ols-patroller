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
   and, once the operator follows rule 14, the gitignored `secret.yaml`
   itself; nothing else [PLANNED: #2].
2. Every namespaced object carries the literal `metadata.namespace:
   ols-patroller`, the `ClusterRoleBinding`'s subject namespace is the same,
   and the `kustomization.yaml` sets **no** `namespace:` field — the kustomize
   namespace transformer does not rewrite `ClusterRoleBinding` subjects, so
   hardcoded literals (consistency enforced by the static tests) are the
   safe form. Changing the deployment namespace is an edit across the files,
   not one field [PLANNED: #2].

### Identity & RBAC (safety layer 3)

3. A single `ServiceAccount` named `ols-patroller` in the namespace; the
   Deployment's pod template sets `serviceAccountName: ols-patroller`. The SA
   token automounts (k8s default) — the image's `oc` authenticates via the
   pod's in-cluster service account and `KUBERNETES_SERVICE_HOST`; no
   kubeconfig is mounted or built (verified in the #1 container run) [PLANNED:
   #2].
4. A single `ClusterRoleBinding` named `ols-patroller` binds that SA to the
   **built-in** `ClusterRole` `cluster-reader` (`roleRef`: apiGroup
   `rbac.authorization.k8s.io`, kind `ClusterRole`, name `cluster-reader`; one
   subject: the SA). It is the only RBAC object in the manifests — no
   `ClusterRole`, `Role`, or `RoleBinding` is defined anywhere in the repo: the
   built-in role is the entire grant (ADR
   `decisions/0006-cluster-reader-binding.md`). This binding is
   `safety-model.md` layer 3 — the server-side enforcement boundary; the
   `kubectl_readonly` guard is defense-in-depth on top [PLANNED: #2].

### Persistence (the wiki volume)

5. A `PersistentVolumeClaim` named `ols-patroller-wiki`: `accessModes`
   `[ReadWriteOnce]`, storage request `1Gi`, **no** `storageClassName` (the
   cluster's default storage class applies — most portable for a dev cluster;
   override by patch when the default class is wrong). The Deployment mounts
   it at `/wiki` (volume name `wiki`, `claimName` matching the PVC) — the wiki
   directory is the PVC mount (`wiki-memory.md` rule 4a) [PLANNED: #2].
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
   forbids (ADR `decisions/0007-scc-fsgroup-writable-wiki.md`) [PLANNED: #2].

### The Deployment

7. The Deployment is named `ols-patroller` — exactly one replica
   (`replicas: 1`) with `strategy: Recreate`. The default
   `RollingUpdate` at one replica is maxSurge 1 / maxUnavailable 0: a surge
   pod can never mount the RWO volume until the old pod dies, so the rollout
   stalls; `Recreate` also guarantees no two-writer window on the single-writer
   wiki (`wiki-memory.md` Constraints) [PLANNED: #2].
8. The container image is the literal name `ols-patroller` (no registry, no
   tag — a stable key for the kustomize `images` transformer); the
   kustomization's `images:` entry maps it to the CI-published
   `ghcr.io/thoraxe/ols-patroller:main` (`container-image.md` rules 13–16).
   That entry is the single override point for clusters that must pull from
   elsewhere (private registry, the cluster's internal registry) [PLANNED:
   #2].
9. The container args are exactly `--loop --interval 900 --wiki
   /wiki/cluster-wiki.md` (identical to the image's default CMD; stated
   explicitly so the manifests are self-describing) [PLANNED: #2].
10. **Env wiring.** `PATROL_API_KEY`, `PATROL_BASE_URL`, `PATROL_MODEL` (and
    `SLACK_WEBHOOK_URL` / `PATROL_WIKI_REMOTE` when present in the Secret)
    arrive **only** via `envFrom` → `secretRef` on the Secret named
    `ols-patroller` — no committed manifest carries a literal value for any of
    them (mirrors `container-image.md` rule 10). `PATROL_CLUSTER_ID` is set as
    a plain literal `env` entry (not secret material): a placeholder the
    operator edits to the real cluster name for wiki commit identity
    (`wiki-memory.md`), never left at the `local` default [PLANNED: #2].
11. **Security context.** Pod: `runAsNonRoot: true`, `seccompProfile:
    RuntimeDefault`, nothing else (rule 6 forbids the identity fields).
    Container: `allowPrivilegeEscalation: false`, `capabilities: drop:
    [ALL]`. The pod must admit under the cluster-default restricted SCC with
    **no SCC grant**: no root, no privilege escalation, no added capabilities,
    no host namespaces, no hostPath volumes, no `privileged` [PLANNED: #2].
12. Resources: requests `cpu: 100m` / `memory: 256Mi`, limits `cpu: "1"` /
    `memory: 512Mi` (the patrol is a Python process mostly waiting on the
    model endpoint; the values are defaults — the contract is that requests
    and limits are both set for CPU and memory) [PLANNED: #2].
13. The Deployment and its pod template carry the label `app: ols-patroller`;
    the selector's `matchLabels` matches it (this is what the acceptance
    procedure's `delete pod -l app=ols-patroller` keys on) [PLANNED: #2].

### The Secret

14. `manifests/secret.yaml` is **gitignored** — real credentials are never
    committed — and the kustomization lists it, so `oc apply -k` fails loudly
    until the operator creates it. The committed template
    `manifests/secret.yaml.example` is a Secret named `ols-patroller` whose
    `stringData` holds the three required keys (`PATROL_API_KEY`,
    `PATROL_BASE_URL`, `PATROL_MODEL`) set to obvious placeholder values, with
    the optional keys (`SLACK_WEBHOOK_URL`, `PATROL_WIKI_REMOTE`) present as
    commented-out lines. Procedure: `cp secret.yaml.example secret.yaml`,
    fill in, apply [PLANNED: #2].
15. No committed manifest contains a real or plausible value for any secret
    env key: those key names appear only in `secret.yaml.example`, only with
    placeholder values [PLANNED: #2].

### Optional remote-push wiring (deploy key)

16. The base manifests never reference a deploy-key secret, an SSH key mount,
    or `GIT_SSH_COMMAND` — `oc apply -k` stays self-contained (a referenced-
    but-missing Secret blocks pod creation). Remote-push wiring
    (`wiki-memory.md` rule 4e) is a documented optional patch, never required
    for a patrol (local-first): create a Secret holding the SSH private key,
    patch the Deployment to mount it read-only (e.g. at `/wiki-deploy-key`)
    and set `GIT_SSH_COMMAND: ssh -i /wiki-deploy-key/<key> -o
    StrictHostKeyChecking=accept-new`, and set `PATROL_WIKI_REMOTE` in the
    patrol Secret [PLANNED: #2].

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
    6. updating after a merge: `oc apply -k manifests/ && oc -n ols-patroller
       rollout restart deployment/ols-patroller` (the rolling `:main` tag —
       an unchanged template alone does not restart the running pod).

## Configuration Surface

- `manifests/` — the kustomize root (files per rule 1); namespace and object
  names are per-file literals kept consistent by tests (rule 2)
- kustomization `images:` entry — the image override point (rule 8)
- `PATROL_CLUSTER_ID` — literal env in the Deployment, edit to the real
  cluster name (rule 10)
- Secret keys — required `PATROL_API_KEY`, `PATROL_BASE_URL`,
  `PATROL_MODEL`; optional `SLACK_WEBHOOK_URL`, `PATROL_WIKI_REMOTE` (rule 14)
- `.github/workflows/image.yml` — the CI that publishes the deployed image
  (`container-image.md` rules 13–16)

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
- PoC grade applies (`constraints.md` rule 6): no NetworkPolicy, no probes
  (no HTTP surface; the loop self-heals via Deployment restarts), no
  monitoring/telemetry wiring, no priority class. The human surface
  in-cluster is pod logs + notifications.
- The deployment adds no patrol behavior: same `patrol.py`, same guard, same
  env contract — wiring only. No new **patrol** configuration exists; the only
  change to `system-overview.md`'s Configuration Surface is a pointer to this
  deployment surface.

## Planned Changes

| Ticket | Summary |
|---|---|
| #2 | This file's contract: manifests (rules 1–17) + the CI workflow that publishes the deployed image (`container-image.md` rules 13–16) |
