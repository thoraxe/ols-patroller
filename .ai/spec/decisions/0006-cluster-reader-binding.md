# 0006. Bind to the built-in cluster-reader, not a bespoke read-only role

Date: 2026-10-14 · Status: proposed

## Context

`safety-model.md` layer 3 needs a server-side boundary for the in-cluster pod
(#2). Options: (a) a bespoke `ClusterRole` enumerating read-only verbs per API
group; (b) the built-in OpenShift `cluster-reader` role — verified on live OCP
4.21: 94 rules, zero Secret access, read-equivalent verbs only even on the
SAR/review APIs. A bespoke role drifts from platform read semantics release
over release, needs maintenance per API group, and every edit is a chance to
accidentally grant too much or too little.

## Decision

Bind the patrol `ServiceAccount` to the built-in `cluster-reader` via a single
`ClusterRoleBinding`; the repo defines no `ClusterRole` of its own.

## Consequences

The boundary is maintained by the platform — correct read semantics across OCP
upgrades for free. The patrol can read almost everything an SRE's eyes need,
but cannot mutate and cannot read Secret values. If a future OCP release grows
`cluster-reader` a mutation or secret-read (platform regression), the guard
layers (`kubectl-guard.md`, engine tool surface) still refuse —
defense-in-depth earns its name. Narrowing below `cluster-reader` (e.g.
namespace-scoped patrols, #13) would need a superseding ADR.
