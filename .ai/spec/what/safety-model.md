# Safety Model

The layered read-only enforcement architecture. This is the part of the hacky PoC
held to production standards: documented, unit-checked, and verified end-to-end.

## Behavioral Rules

1. The patrol **never mutates the observed cluster**. Observation is the only cluster
   interaction; nothing in the codebase writes to the cluster.
2. **Layer 1 — tool guard:** the only cluster tool is `kubectl_readonly` with its
   verb whitelist and secret-value refusal (`kubectl-guard.md`).
3. **Layer 2 — engine tool surface:** the agent is built with `create_deep_agent`
   and **no backend**, so it has no shell and no filesystem tools; deepagents'
   default `FilesystemMiddleware` (ls/write_file/edit_file/delete/glob/grep/execute)
   is replaced with a read-file-only middleware; the default general-purpose `task`
   subagent is disabled. Effective tool surface is exactly
   `[read_file, kubectl_readonly]`.
4. **Layer 3 — RBAC boundary [PLANNED: #2]:** when deployed in-cluster, the pod
   ServiceAccount's RBAC (binding to the built-in `cluster-reader` role — verified on
   live OCP 4.21: 94 rules, zero Secret access, read-equivalent verbs only) is the
   server-side enforcement boundary. The tool guard is defense-in-depth, not the
   boundary itself. Manifest contract: `in-cluster-deployment.md` (decision record
   `decisions/0006-cluster-reader-binding.md`).
5. Remediation is **delegated, never local**: a warn/crit finding with a known
   remediation becomes a governed AgenticRun (sandbox + approval + derived RBAC +
   audit) [PLANNED: #12]. The patrol itself stays read-only forever — it is the eyes
   and memory; the AgenticRun is the hands.
6. Guard behavior is unit-checked: mutation verbs and secret-value reads are refused;
   e2e asserts `execute`/`task` are rejected as invalid tools. Those tests are slated
   to land in-repo [PLANNED: #4].

## Constraints

- Integration with OLS-side machinery (AgenticRuns) is API-surface-only (`oc create
  agenticrun`), no OLS code imports — see `constraints.md`.
- Weakening any layer (narrowing the guard, re-enabling filesystem tools, granting
  write RBAC) requires an ADR in `decisions/`.

## Planned Changes

| Ticket | Summary |
|---|---|
| #2 | In-cluster manifests: SA + `cluster-reader` ClusterRoleBinding + RWO PVC at `/wiki` + single-replica non-root Deployment — makes Layer 3 real (`in-cluster-deployment.md`; ADR 0006) |
| #12 | Remediation handoff: dispatch governed AgenticRuns for actionable findings; harvest their result CRs back into the wiki |
| #11 | Guard hardening: refuse `--raw` proxy/stats endpoints |
| #4 | Commit guard unit tests + fake-model e2e into the repo |
