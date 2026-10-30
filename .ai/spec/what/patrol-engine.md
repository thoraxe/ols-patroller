# Patrol Engine

The agentic patrol loop: how one patrol explores, what tools it may use, how it
terminates, and what context it is given. Implementation wiring (deepagents) is in
`how/agent-engine.md`; this file is the behavioral contract.

## Behavioral Rules

### Role & Prompt

1. The system prompt frames each patrol as a **routine resident-SRE round**, not an
   alert response: proactively "doing your rounds" instead of waiting for alarms.
2. The prompt's hard rules: observe only; the wiki is prior notes that may be WRONG,
   STALE, or INCOMPLETE (live cluster is the single source of truth; never trust the
   wiki over observation); do not re-investigate anything covered by in-flight
   AgenticRuns.
2a. **Secret access is forbidden** [PLANNED: #33]. The prompt's hard rules add
    exactly: Kubernetes Secrets are OFF-LIMITS in full — never read them, not
    even names or metadata. The guard refuses any command containing `secret`;
    inspect workload metadata (`get pods`, `describe deployment`, `get events`)
    instead. This is teaching aligned with the guard (`kubectl-guard.md` rules
    5–6), not a substitute for it.
3. Patrol order mandated by the prompt: (1) map topology (operators, key workloads,
   namespaces, wiring) with the layered sweep (rules 3a–3e) [DONE: #20 — was
   "a broad sweep first"], then drill into anything suspicious; (2) compare
   against the wiki for drift — Watch-list entries carry `recheck:`
   commands (`wiki-memory.md` rule 12a), the read-only invocations that re-verify
   them: run an entry's recheck first when re-checking it, and rewrite a recheck
   whose verification path has changed [DONE: #6]; (3) decide what a human
   should actually care about (don't cry wolf); (4) produce an updated wiki and
   refined watch-list.
3a. **Layered sweep** [DONE: #20]. The prompt teaches mapping the cluster
    (and any broad query) as a layered sweep, not flat enumeration — each layer
    is one bounded query, and the model drills into what looks off:
    1. `get clusteroperators` — cluster-level health
    2. `get nodes` — capacity/scheduling
    3. `get namespaces` — footprint
    4. `get deployments -A` / `get daemonsets -A` / `get statefulsets -A` —
       workload topology: one row per workload instead of N rows per replica,
       and the READY column flags rollout problems for free
    5. problem pods only (rules 3b–3c)
    6. `get events -A --field-selector=type=Warning` — warnings, not the event
       firehose

    Rationale: `get pods -A` on a thousands-of-pods cluster is thousands of
    rows — it truncates at `OUT_CAP` mid-table (the model sees an unknown
    fraction of the list, then burns turns re-querying), while the layered
    sweep answers the same questions in hundreds of rows. Targeted drilldowns
    (`describe`, single-object `get`) are always allowed on top of the sweep.
3b. **Problem-pod selection** [DONE: #20]. The prompt teaches selecting
    problem pods with per-phase equality selectors
    (`--field-selector=status.phase=Pending`, `--field-selector=status.phase=Failed`)
    and warns against inequality selectors (`status.phase!=Running`): `!=` is
    not reliable on `oc` — equality per problem phase is the portable form.
3c. **Crash-loop and restart sweep** [DONE: #20]. CrashLoopBackOff is
    invisible to phase selectors — pod `status.phase` stays `Running` while
    containers crash-loop (the table's STATUS column shows a container
    *waiting reason*). The prompt teaches sweeping waiting reasons and restart
    counts client-side with this jsonpath template:

        get pods -A -o jsonpath='{range .items[*]}{.metadata.namespace}/{.metadata.name}{" "}{.status.containerStatuses[*].state.waiting.reason}{" restarts="}{.status.containerStatuses[*].restartCount}{"\n"}{end}'

    jsonpath/custom-columns are client-side formatters: the model-facing
    output stays bounded on big clusters (`OUT_CAP` applies after formatting),
    and the sweep yields absolute values instead of the table's relative
    timestamps (`40d`) — what the Watch-list's persistence tracking (restart
    deltas, "unchanged 18:44") needs.
3d. **Field selection over full dumps** [DONE: #20]. The prompt teaches:
    full `-o json` / `-o yaml` is reserved for single-object drilldowns
    (`get node worker-1 -o yaml`) — never full dumps of lists, which cost
    ~5–10× the table tokens for the same objects (key names repeated per
    object). For list-shaped needs: `--field-selector`, `-o jsonpath` /
    custom-columns, and `--no-headers` for name-only lists. All of these are
    already guard-permitted for non-secret resources — the teaching closes
    the gap, not the guard (ADR 0016).
3e. **Subsequent patrols lean on the wiki** [DONE: #20]. The layered sweep
    is the dry-wiki (first-patrol) map. On a populated wiki the prompt teaches
    leaning on memory instead of re-deriving: run Watch-list rechecks first,
    verify Baselines rather than re-mapping topology, and reserve a full sweep
    for an area the wiki is stale, contradicted, or silent about — never
    re-derive a map the wiki already holds. Real-run evidence: patrol #7's
    "restart sweep matches the 18:02 baseline exactly" — the compounding-wiki
    economy #5–#8 built.
4. When done exploring, the model stops calling tools and writes a plain-text final
   summary (findings with severity and evidence, wiki corrections, what the next
   patrol should watch) — a separate reporting step formats the structured report.

### Tool Surface

5. The model's effective tool surface is exactly `[read_file, kubectl_readonly]`.
   Shell, filesystem writes, `execute`, and the default `task` subagent are removed;
   `execute`/`task` calls are rejected as invalid tools (asserted by the in-repo
   fake-model e2e [DONE: #4]).
6. The patrol is single-threaded: no subagents are spawned.

### Exploration & Termination

7. The model explores with **auto tool choice** — no forced tool calls on any turn.
8. Hard budget: `max_steps` LLM turns per patrol (default 30, `PATROL_MAX_STEPS` /
   `--max-steps`); a soft target ("~N turns") is communicated in the user prompt —
   the target value is its own constant, not the budget (rule 8a).
8a. **Soft turn target, decoupled from the hard budget** [DONE: #10]. The
    user prompt's turn target is the hard-coded constant `SOFT_TARGET_TURNS`
    (20), independent of the hard budget (`max_steps`, rule 8). The prompt
    states both, distinctly: a soft-target sentence — "aim to wrap up within
    ~20 turns (soft target) — once you have covered the main areas or hit
    diminishing returns, stop exploring and write your final summary" — and a
    separate hard-budget sentence, "The hard budget is {max_steps} turns —
    beyond it the patrol is cut off."
    Rationale (real-run evidence): GLM keys off the *stated target*, not the
    budget — patrol #7 self-terminated at 34/36 turns with "~30 turns"
    stated, i.e. it treated the target as the budget plus drift; a target
    equal to the hard budget leaves no margin, and a target mechanically
    derived from the budget (`max_steps` minus N%) cannot be tuned alone.
    Decoupling makes the target retunable without touching the budget — a
    spec change either way, like the other hard-coded engine constants (ADR
    0015's convention).
8b. **Token soft-stop** [DONE: #10]. Exploration is additionally bounded
    by cumulative input tokens: `PATROL_TOKEN_BUDGET` (default 500,000;
    `PATROL_TOKEN_BUDGET` env / `--token-budget`, mirroring `--max-steps`;
    `0` disables the stop). The count is process-owned — the sum of
    `usage_metadata.input_tokens` over the patrol's `AIMessage`s, maintained
    as the agent stream iterates; never model-trusted, never prompt-borne.
    When the running total first exceeds the budget, the process stops
    iterating the stream and ends the patrol exactly like a step-budget cut
    (rule 9): `budget_cut` semantics — the same reporting pass runs over what
    was observed, dangling tool calls are closed with the not-executed
    salvage (the step-budget cut's ToolMessage text, "ERROR: step budget
    exhausted; command not executed.", reused verbatim — the cut reason
    lives in the console warning, not the salvage), and a console warning
    names the consumed tokens and the budget.
    The stop ends *exploration*, never the patrol — the report always lands.
    Rationale (real-run evidence): patrols ranged 216K–1M input tokens; the
    variance is cumulative (patrol #6 burned turns fighting `OUT_CAP`
    truncation on node stats dumps), so the bound is cumulative too — per-turn
    caps bound the wrong thing, and a warn-only alarm does not stop in-flight
    spend (ADR 0027).
9. A patrol ends either naturally (the model's plain-text summary) or by exhausting
   the step budget — or by the token soft-stop (rule 8b, [DONE: #10]), which
   shares the budget-cut path; **all paths flow through the same reporting pass** —
   there is no salvage special-case (asserted by the in-repo fake-model e2e [DONE:
   #4]).
10. A budget-cut report covers only what was actually observed; tool calls that never
    executed are marked "not executed" in the conversation fed to the reporting pass.

### Context Inputs

11. Each patrol's user prompt contains: current timestamp, dry-cluster note (if
    enabled), the output of an in-flight AgenticRuns check, and the full wiki.
12. The in-flight check runs `get agenticruns -A` through the same read-only guard
    before exploration, as best-effort dedup context — if the CRD is absent the
    command errors and the patrol proceeds anyway.

### Dry-Cluster Mode

13. With `--dry-cluster`, commands are answered from canned fixtures keyed by
    substring of the command (first match wins). The fixture set models the
    taught sweep (rules 3a–3c) so `--dry-cluster` exercises the patterns the
    prompt teaches [DONE: #20 — before #20 the fixtures were only `version`,
    `get clusteroperators`, `get nodes`, `get namespaces`, `get pods -A`, and
    `get agenticruns`]:
    - `version`, `get clusteroperators`, `get nodes`, `get namespaces`,
      `get agenticruns` — unchanged.
    - The workload-object tables (rule 3a layer 4): `get deployments -A`,
      `get daemonsets -A`, `get statefulsets -A` — one row per workload; the
      demo deployment's `1/2` READY flags the crash-looping workload (its two
      replicas are the flat pods table's two demo-app pods, one of them
      down), the payoff the rule promises.
    - The phase sweeps (rule 3b): `status.phase=Pending`, `status.phase=Failed`
      — both answer *no resources found*: the demo's problem pods are
      crash-looping (phase `Running`), which is rule 3c's lesson live — the
      phase sweeps come up empty and only the reason/restart sweep finds the
      problem.
    - The crash-loop sweep (rule 3c): `containerStatuses` — one
      `namespace/name <waiting reason> restarts=N` line per pod, healthy pods
      included (empty reason, `restarts=0`), consistent with the flat pods
      table: same pods, same counts — two views of one demo cluster.
    - The flat `get pods -A` table — still answerable: the guard permits flat
      enumeration and the demo data stays honest (ordering per rule 13a).
    - Warning events (rule 3a layer 6): `type=Warning` — a warnings-only
      events table naming the demo's two problems (the crash-loop `BackOff`,
      the NotReady worker's `NodeNotReady`).
13a. **Sweep-fixture keying and order** [DONE: #20].
    - Sweep keys precede the flat `get pods -A` key in the table: every
      problem-pod sweep command contains the substring `get pods -A`, so with
      first-match-wins a sweep key placed after it would never fire.
    - The sweep keys are the distinctive selector values
      (`status.phase=Pending`, not `--field-selector=status.phase=Pending`) so
      both flag forms (`--field-selector=x` and `--field-selector x`) match.
    - The events fixture is keyed on `type=Warning`, not `get events`: the
      unfiltered event firehose is rule 3a layer 6's anti-pattern, and dry
      mode deliberately does not serve it — a flat `get events` query gets
      rule 14's no-fixture message.
14. An unmatched dry-cluster command returns a "no fixture" message that lists the
    available fixtures and instructs the model to treat the resource as absent and
    not retry command variations.
15. Dry-cluster mode injects a DRY-CLUSTER note into the prompt restricting the
    patrol to fixture commands alone.

## Configuration Surface

- `PATROL_MAX_STEPS` / `--max-steps` — LLM-turn budget (default 30)
- `PATROL_TOKEN_BUDGET` / `--token-budget` — cumulative exploration input-token
  budget (default 500,000; `0` disables) [DONE: #10]
- `--dry-cluster` — fixture mode, no cluster needed
- `SOFT_TARGET_TURNS` — the prompt's soft turn target, 20 (hard-coded, not
  env-configurable — retuning it is a spec change, not a config knob; ADR 0027)
  [DONE: #10]
- Engine constants (not configurable): temperature 0.3, completion-token
  bounds — `REQUEST_MAX_TOKENS` 16000 per exploration turn,
  `REPORT_MAX_TOKENS` 64000 per structured reporting/repair call
  (`reporting-notifications.md` rule 8b) [DONE: #63 — was a flat
  `max_tokens` 16000 on the whole client], request
  bounds — `REQUEST_TIMEOUT` 300 s per exploration turn, `REPORT_TIMEOUT` 900 s
  per structured reporting/repair call (`reporting-notifications.md` rule 8a)
  [DONE: #49 — was a flat 300 s on the whole client]

## Constraints

- The engine is upstream `deepagents` (`create_deep_agent`), the same library the OLS
  agentic sandbox uses, depended on directly — no OLS code is imported
  (see `constraints.md`).
- An in-agent response format must not be used: forcing `tool_choice="any"` on every
  turn removes the model's natural stopping signal (under it, GLM never
  self-terminated — every real patrol ran to the budget). Typed output comes from the
  separate reporting pass (see `decisions/0002-shape-pass-not-tool-strategy.md`).

## Planned Changes

| Ticket | Summary |
|---|---|
| ~~#6~~ | ~~Prompt consumes recheck commands: run an entry's `recheck` first when re-verifying it, keep it current when the verification path changes (`wiki-memory.md` rule 12a)~~ |
| ~~#10~~ | ~~Cost control: token soft-stop (rule 8b), soft turn target decoupled from the hard budget (rule 8a), head+tail truncation replacing head-only `OUT_CAP` capping (`kubectl-guard.md` rule 11) — scoping settled whole-cluster by ADR 0027 (the wiki economy of rule 3e already is the scoping mechanism; per-namespace rounds rejected). Impl commit also syncs `AGENTS.md`'s test list~~ |
| ~~#49~~ | ~~Per-call-shape request bounds: `REQUEST_TIMEOUT` 300 s exploration / `REPORT_TIMEOUT` 900 s structured reporting — the engine-constants line above; the behavioral rule is `reporting-notifications.md` rule 8a~~ |
| ~~#20~~ | ~~Efficient cluster sweep: the layered sweep + field-selection teaching in the system prompt (rules 3a–3e), dry-cluster fixtures modeling the taught sweep (rules 13–13a), and the guard truncation marker's recovery hint (`kubectl-guard.md` rule 11) — prompt guidance and fixture data only: no new config surface, no guard refusal changes (ADR 0016). Impl commit also syncs `AGENTS.md`'s test list~~ |
| #33 | Prompt teaching for the guard's Secret-access ban (rule 2a): Secrets are off-limits in full, the guard refuses any command containing `secret`, recover via workload metadata |
| #14 | CRD-based dispatcher (`PatrolSchedule`) replacing the `--loop` process |
| #15 | Pluggable investigation engine — evaluate HolmesGPT as the RCA phase; keep the current loop swappable |
| #12 | Remediation handoff: warn/crit findings with known remediation dispatch governed AgenticRuns |
