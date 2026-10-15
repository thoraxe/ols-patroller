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
3. Patrol order mandated by the prompt: (1) map topology (operators, key workloads,
   namespaces, wiring) with a broad sweep first, then drill into anything suspicious;
   (2) compare against the wiki for drift; (3) decide what a human should actually
   care about (don't cry wolf); (4) produce an updated wiki and refined watch-list.
4. When done exploring, the model stops calling tools and writes a plain-text final
   summary (findings with severity and evidence, wiki corrections, what the next
   patrol should watch) — a separate reporting step formats the structured report.

### Tool Surface

5. The model's effective tool surface is exactly `[read_file, kubectl_readonly]`.
   Shell, filesystem writes, `execute`, and the default `task` subagent are removed;
   `execute`/`task` calls are rejected as invalid tools (asserted by the in-repo
   fake-model e2e [PLANNED: #4]).
6. The patrol is single-threaded: no subagents are spawned.

### Exploration & Termination

7. The model explores with **auto tool choice** — no forced tool calls on any turn.
8. Hard budget: `max_steps` LLM turns per patrol (default 30, `PATROL_MAX_STEPS` /
   `--max-steps`); a soft target ("~N turns") is communicated in the user prompt.
9. A patrol ends either naturally (the model's plain-text summary) or by exhausting
   the step budget; **both paths flow through the same reporting pass** — there is no
   salvage special-case (asserted by the in-repo fake-model e2e [PLANNED: #4]).
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
    substring of the command (first match wins); only `version`, `get
    clusteroperators`, `get nodes`, `get namespaces`, `get pods -A`, and `get
    agenticruns` have fixtures.
14. An unmatched dry-cluster command returns a "no fixture" message that lists the
    available fixtures and instructs the model to treat the resource as absent and
    not retry command variations.
15. Dry-cluster mode injects a DRY-CLUSTER note into the prompt restricting the
    patrol to fixture commands alone.

## Configuration Surface

- `PATROL_MAX_STEPS` / `--max-steps` — LLM-turn budget (default 30)
- `--dry-cluster` — fixture mode, no cluster needed
- Engine constants (not configurable): temperature 0.3, `max_tokens` 16000

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
| #10 | Cost control: scoping (cluster vs per-namespace rounds), token soft-stop, termination tuning, smarter output truncation than hard OUT_CAP |
| #14 | CRD-based dispatcher (`PatrolSchedule`) replacing the `--loop` process |
| #15 | Pluggable investigation engine — evaluate HolmesGPT as the RCA phase; keep the current loop swappable |
| #12 | Remediation handoff: warn/crit findings with known remediation dispatch governed AgenticRuns |
