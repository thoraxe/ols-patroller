# Safety Model

The layered read-only enforcement architecture. This is the part of the hacky PoC
held to production standards: documented, unit-checked, and verified end-to-end.

## Behavioral Rules

1. The patrol **never mutates the observed cluster**. Observation is the only cluster
   interaction; nothing in the codebase writes to the cluster.
2. **Layer 1 — tool guard:** the only cluster tool is `kubectl_readonly` with its
   verb whitelist, Secret-access ban (all Secret access — names and metadata
   included, not just values) [DONE: #33], raw-path (`--raw`) refusal,
   local-file/credential-flag refusal [DONE: #33], and endpoint-override
   (`--server`/`-s`) refusal [PLANNED: #34] (`kubectl-guard.md`).
3. **Layer 2 — engine tool surface:** the agent is built with `create_deep_agent`
   and **no backend**, so it has no shell and no filesystem tools; deepagents'
   default `FilesystemMiddleware` (ls/write_file/edit_file/delete/glob/grep/execute)
   is replaced with a read-file-only middleware; the default general-purpose `task`
   subagent is disabled. Effective tool surface is exactly
   `[read_file, kubectl_readonly]`.
4. **Layer 3 — RBAC boundary [DONE: #2]:** when deployed in-cluster, the pod
   ServiceAccount's RBAC (binding to the built-in `cluster-reader` role — verified on
   live OCP 4.21: 94 rules, zero Secret access, read-equivalent verbs only) is the
   server-side enforcement boundary. The tool guard is defense-in-depth, not the
   boundary itself. Manifest contract: `in-cluster-deployment.md` (decision record
   `decisions/0006-cluster-reader-binding.md`).
5. Remediation is **delegated, never local**: a warn/crit finding with a known
   remediation becomes a governed AgenticRun (sandbox + approval + derived RBAC +
   audit) [PLANNED: #12]. The patrol itself stays read-only forever — it is the eyes
   and memory; the AgenticRun is the hands.
6. The read-only layers (1–3) are test-enforced in-repo: the guard is
   unit-checked (`tests/test_kubectl_guard.py`) and the engine tool surface plus
   both termination paths are verified end-to-end by a scripted fake-model e2e
   (`tests/test_patrol_deepagents.py`) [DONE: #4]. Layer 4 is enforced
   cluster-side by the operator-applied policy, so its done-when is the
   acceptance procedure of `egress-control.md` rule 10, not a pytest check.
6a. **Guard unit tests** pin `kubectl-guard.md` behavior directly against
    `kubectl_readonly` — no engine, no model: non-whitelisted and mutation verbs
    are refused with the allowed-verb list (rule 3); unparseable and empty
    commands are refused (rule 4); Secret access is refused in full — value-bearing
    reads across every output format, names/metadata reads (`get secrets`, `get
    secret <name> -o name`, `describe secret`), resource forms after flags, and
    the deliberately over-broad substring match on unrelated names/fields (rules
    5–6) and the local-file/credential-flag ban (rule 6b) [DONE: #33]; a
    missing CLI binary (rule 9), a timed-out command
    (rule 8), the
    stderr header and empty-output marker (rule 10), the `OUT_CAP` truncation
    marker with its #20 recovery hint (rule 11) [DONE: #20], the CLI
    resolution order (rule 12), and dry-mode fixture
    substitution with the no-fixture message (rule 13); the raw-path refusal
    joins these checks — every `--raw` flag form refused across verbs, bare-`raw`
    names allowed, the refusal ahead of subprocess execution and dry-mode
    fixture lookup (rule 6a) [DONE: #11]. The #33 Secret-access and
    local-file/credential-flag matrices live in `tests/test_secret_access.py` —
    direct guard calls plus the system-prompt and report-contract pins
    [DONE: #33]. The endpoint-override refusal (rule 6c) joins them in
    `tests/test_egress_control.py`: every `--server`/`-s` spelling refused
    ahead of execution and in dry mode, `--server-print` and plain non-secret
    reads still accepted [PLANNED: #34].
6b. **Fake-model e2e** drives complete patrols through the real engine — real
    `ChatOpenAI` + openai SDK + `create_deep_agent` + guard + shape pass + wiki
    git tail — with only the HTTP endpoint scripted and the cluster answered by
    dry-cluster fixtures. It asserts:
    - every agent-turn request offers exactly the tools `[read_file,
      kubectl_readonly]`, and `execute`/`task` calls are rejected as invalid
      tools whose error lists that exact surface;
    - guard refusals (a mutation verb, a Secret access attempt — exercised with a
      names/metadata form, not only a value form) reach the model as `REFUSED:`
      tool results in the next request, so the model can recover [DONE: #33];
    - natural-termination and budget-cut patrols both flow through the same
      shape pass: the reporting request binds only the `PatrolReport` schema
      with forced tool choice, carries the report contract, and differs only
      in the completion vs cut-off prefix; a budget-cut conversation marks
      never-executed commands as such;
    - the wiki wiring deferred from #3 (`wiki-memory.md` rule 4f): repo ensured
      at startup via `main()`, one commit per wiki-changing patrol, no commit
      on a byte-identical rewrite [byte-identical case superseded by #5's
      stamp — every accepted write advances `patrol_count` and changes the
      file: DONE: #5], push only when a remote is configured, and a failed
      reporting pass leaves the wiki untouched;
    - the wiki-skeleton wiring of #5 (`wiki-memory.md` rules 15–19): an invalid
      wiki triggers exactly one repair pass whose prompt carries the validation
      errors and the rejected wiki, the repaired wiki is written with stamped
      frontmatter, and a twice-invalid wiki leaves the old wiki byte-for-byte
      intact with the patrol's findings still delivered [DONE: #5].
6c. The e2e is hermetic and deterministic: no network, no cluster, no
    `PATROL_*` environment, no real model endpoint — the scripted endpoint is
    an in-process OpenAI-compatible fake behind the `make_model` seam, and the
    wiki, its repo, and any remote are test-local temporaries. It runs as part
    of the default test invocation and finishes in seconds.
7. **Layer 4 — network egress allowlist** [PLANNED: #34]: in-cluster, the pod's
   egress is default-deny, allowing only cluster DNS, the kube-apiserver, and
   the destinations derived from the patrol's own egress configuration
   (`PATROL_BASE_URL`, `PATROL_NOTIFY_URLS`, `PATROL_WIKI_REMOTE`) — a policy
   rendered from that configuration and applied by the operator
   (`egress-control.md` rules 1–10, ADR 0030). It is the layer that holds when
   a dependency is compromised or the egress configuration itself is tampered
   with: layers 1–3 judge commands and identities, layer 4 bounds destinations.
   The one in-agent egress channel — `kubectl --server` — is closed in layer 1
   (guard rule 6c).

## Constraints

- Integration with OLS-side machinery (AgenticRuns) is API-surface-only (`oc create
  agenticrun`), no OLS code imports — see `constraints.md`.
- Weakening any layer (narrowing the guard, re-enabling filesystem tools, granting
  write RBAC) requires an ADR in `decisions/`.
- Layer 4 is **opt-in operator wiring**, not a shipped default: no committed
  manifest includes the allowlist, and enabling it is a render-and-apply
  procedure (`egress-control.md` rules 8–8c, ADR 0030). Making it part of the
  base apply is a separate decision, not a spec detail.
- The e2e pins observable wire behavior — the tool surface offered to the model,
  invalid-tool rejection, guard refusals as the model sees them, shape-pass
  mechanics — not deepagents/langchain internals. It is the tripwire that catches
  engine drift on upgrades (`decisions/0009-wire-level-fake-endpoint-e2e.md`).

## Planned Changes

| Ticket | Summary |
|---|---|
| #34 | Layer 4 — the network egress allowlist (rule 7): default-deny pod egress allowing only cluster DNS, the kube-apiserver, and the destinations derived from the patrol's own egress configuration, rendered by the offline `--egress-policy` mode and applied by the operator (`egress-control.md`, ADR 0030). Layer 1 gains the endpoint-override refusal (rule 6a's list; `kubectl-guard.md` rule 6c) |
| ~~#2~~ | In-cluster manifests: SA + `cluster-reader` ClusterRoleBinding + RWO PVC at `/wiki` + single-replica non-root Deployment — makes Layer 3 real (`in-cluster-deployment.md`; ADR 0006) |
| #12 | Remediation handoff: dispatch governed AgenticRuns for actionable findings; harvest their result CRs back into the wiki |
| ~~#11~~ | Guard hardening: refuse `--raw` proxy/stats endpoints (`kubectl-guard.md` rule 6a) |
| ~~#33~~ | ~~Secret hardening: ban all Kubernetes Secret access (names and metadata included, not only values) in the guard (`kubectl-guard.md` rules 5–6), close the local-file/credential-flag bypass (rule 6b: `-f`/`--filename`, kubeconfig/token/cert flags, template-file formats), and teach the ban in the system prompt (`patrol-engine.md` rule 2a) and report contract (`reporting-notifications.md` rule 5); broader sensitive-data filtering stays future scope (ADR 0028)~~ |
| ~~#4~~ | Commit guard unit tests + fake-model e2e into the repo |
| ~~#5~~ | The e2e gains the wiki-skeleton wiring (rule 6b): repair-pass flow, stamped writes, keep-old-wiki on double failure |
