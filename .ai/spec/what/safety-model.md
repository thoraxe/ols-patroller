# Safety Model

The layered read-only enforcement architecture. This is the part of the hacky PoC
held to production standards: documented, unit-checked, and verified end-to-end.

## Behavioral Rules

1. The patrol **never mutates the observed cluster**. Observation is the only cluster
   interaction; nothing in the codebase writes to the cluster.
2. **Layer 1 — tool guard:** the only cluster tool is `kubectl_readonly` with its
   verb whitelist, secret-value refusal, and raw-path (`--raw`) refusal
   (`kubectl-guard.md`).
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
6. The read-only layers are test-enforced in-repo: the guard is unit-checked
   (`tests/test_kubectl_guard.py`) and the engine tool surface plus both termination
   paths are verified end-to-end by a scripted fake-model e2e
   (`tests/test_patrol_deepagents.py`) [DONE: #4].
6a. **Guard unit tests** pin `kubectl-guard.md` behavior directly against
    `kubectl_readonly` — no engine, no model: non-whitelisted and mutation verbs
    are refused with the allowed-verb list (rule 3); unparseable and empty
    commands are refused (rule 4); secret-value reads are refused across every
    output format, including the deliberately over-broad jsonpath match, while
    `get secrets` (names/metadata) and `describe secret` stay allowed (rules
    5–6); a missing CLI binary (rule 9), a timed-out command (rule 8), the
    stderr header and empty-output marker (rule 10), the `OUT_CAP` truncation
    marker (rule 11), the CLI resolution order (rule 12), and dry-mode fixture
    substitution with the no-fixture message (rule 13); the raw-path refusal
    joins these checks — every `--raw` flag form refused across verbs, bare-`raw`
    names allowed, the refusal ahead of subprocess execution and dry-mode
    fixture lookup (rule 6a) [DONE: #11].
6b. **Fake-model e2e** drives complete patrols through the real engine — real
    `ChatOpenAI` + openai SDK + `create_deep_agent` + guard + shape pass + wiki
    git tail — with only the HTTP endpoint scripted and the cluster answered by
    dry-cluster fixtures. It asserts:
    - every agent-turn request offers exactly the tools `[read_file,
      kubectl_readonly]`, and `execute`/`task` calls are rejected as invalid
      tools whose error lists that exact surface;
    - guard refusals (a mutation verb, a secret-value read) reach the model as
      `REFUSED:` tool results in the next request, so the model can recover;
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

## Constraints

- Integration with OLS-side machinery (AgenticRuns) is API-surface-only (`oc create
  agenticrun`), no OLS code imports — see `constraints.md`.
- Weakening any layer (narrowing the guard, re-enabling filesystem tools, granting
  write RBAC) requires an ADR in `decisions/`.
- The e2e pins observable wire behavior — the tool surface offered to the model,
  invalid-tool rejection, guard refusals as the model sees them, shape-pass
  mechanics — not deepagents/langchain internals. It is the tripwire that catches
  engine drift on upgrades (`decisions/0009-wire-level-fake-endpoint-e2e.md`).

## Planned Changes

| Ticket | Summary |
|---|---|
| ~~#2~~ | In-cluster manifests: SA + `cluster-reader` ClusterRoleBinding + RWO PVC at `/wiki` + single-replica non-root Deployment — makes Layer 3 real (`in-cluster-deployment.md`; ADR 0006) |
| #12 | Remediation handoff: dispatch governed AgenticRuns for actionable findings; harvest their result CRs back into the wiki |
| ~~#11~~ | Guard hardening: refuse `--raw` proxy/stats endpoints (`kubectl-guard.md` rule 6a) |
| ~~#4~~ | Commit guard unit tests + fake-model e2e into the repo |
| ~~#5~~ | The e2e gains the wiki-skeleton wiring (rule 6b): repair-pass flow, stamped writes, keep-old-wiki on double failure |
