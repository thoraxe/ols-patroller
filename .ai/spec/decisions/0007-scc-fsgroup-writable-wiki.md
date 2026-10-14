# 0007. /wiki writability via the SCC-assigned fsGroup

Date: 2026-10-14 · Status: proposed

## Context

The #1 container verification proved that any mount at `/wiki` shadows the
image's `/wiki` prep (`chgrp 0` + `chmod g=u`): a fresh volume is root-owned
without group write, and the pod runs non-root (image `USER 1001:0`). Locally
this bit as a `PermissionError` creating `cluster-wiki.md` on a `755`
root-owned bind mount. Options for making the volume writable:

- (a) an explicit `fsGroup` in the pod spec — not portable: the value must
  fall inside the namespace's SCC-allowed range, which a repo manifest cannot
  know;
- (b) an init container that chmods the mount — needs UID 0, i.e. the `anyuid`
  SCC, a privilege escalation the read-only posture forbids;
- (c) rely on the cluster-default restricted SCC (`restricted-v2`): its
  MustRunAs fsGroup/supplemental-groups strategies assign a namespace-range GID
  when the pod leaves the fields unset; the kubelet chowns the RWO volume root
  to that GID with setgid + group-write, and the GID rides in the container's
  supplemental groups.

## Decision

The Deployment sets no `runAsUser`/`runAsGroup`/`fsGroup`; writability comes
entirely from the SCC-assigned fsGroup; no init-container fixup.

## Consequences

No privileged SCC anywhere in the deployment. Works on any default-SCC
namespace with an fsGroup-honoring storage class (gp3-csi, ceph-rbd; older
CephFS with `FSGroupPolicy: None` does not — documented as a constraint in
`in-cluster-deployment.md`). A namespace whose SCC stops assigning an fsGroup
fails loudly on the first patrol (`PermissionError`), a visible signal rather
than silent data loss. The assigned GID is deterministic (first value of the
namespace range).
