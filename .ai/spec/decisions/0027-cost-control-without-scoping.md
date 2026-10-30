# 0027. Cost control without scoping: a token soft-stop, a decoupled target, head+tail

Date: 2026-11-06 · Status: proposed

## Context

Issue #10 (v0.3, operational hardening): bound patrol cost without blinding
the patrol. Real-run evidence motivated it: patrols ranged 216K–1M input
tokens (patrol 7 vs 6); patrol #6 burned turns fighting `OUT_CAP` truncation
on node proxy stats dumps (the variance's main driver); GLM self-terminated
at 34/36 turns, apparently keying off the "~30 turns" soft target in the
prompt — the *stated target*, not the hard budget, was driving termination.

Four design spaces were open: scoping (whole-cluster vs per-namespace
rounds), a token budget, termination tuning, and smarter output truncation
than hard `OUT_CAP`.

## Decision

### Scoping: whole-cluster rounds, the wiki economy is the scoping mechanism

Per-namespace patrol rounds are **rejected**. The layered sweep's
wiki-economy (`patrol-engine.md` rule 3e) already delivers what scoping
sought: subsequent patrols run Watch-list rechecks and verify Baselines
instead of re-mapping topology (cheap, frequent verification), reserving
full sweeps for areas the wiki is stale or silent about (rare, expensive
deep dives) — "topology sweep is cheap/frequent and deep dives are rare"
emerges from memory, not from a schedule. Per-namespace rounds would
additionally fragment the single whole-cluster wiki (#5's compounding
memory is the product; a namespace-sharded wiki loses cross-namespace
topology and duplicates the Baselines) and multiply the fixed per-patrol
cost (report pass, wiki write, notifications) across shards. If a future
cluster genuinely exceeds what one whole-cluster round can cover, the
escape hatch is #14's `PatrolSchedule` dispatch, not this patrol's shape.
No contract change.

### Token budget: a cumulative input-token soft-stop through the budget-cut path

`PATROL_TOKEN_BUDGET` (default 500,000; env + `--token-budget`; `0`
disables) bounds the *sum* of exploration input tokens, process-counted
from `usage_metadata`. On crossing, exploration stops with exactly the
step-budget cut's semantics (`patrol-engine.md` rule 8b): the same
reporting pass over what was observed, dangling tool calls closed, a
console warning. Rejected alternatives: **warn-only** (an alarm does not
stop in-flight spend — patrol #6's 1M-token run would have completed
anyway, just noisier); **per-turn input caps** (the variance is cumulative,
not per-turn — every individual turn of patrol #6 was reasonable; the sum
was not); **client-side `max_tokens` tricks** (that bounds *output* tokens,
already split per call shape by #63, and cannot bound input at all).

The stop is *soft* in the patrol's sense: it ends exploration, never the
patrol — the report always lands, exactly like a step budget cut.

### Termination: the soft target decoupled from the hard budget

`SOFT_TARGET_TURNS` (20, hard-coded) replaces the prompt's
`~{max_steps} turns` phrasing (`patrol-engine.md` rule 8a). GLM treats the
stated target as the operative budget (self-terminated at 34/36 with "~30"
stated); a target equal to the hard budget leaves no margin and cannot be
tuned without moving the budget. Twenty against a default budget of thirty
gives the model a comfortable wrap-up zone while the cut stays the
backstop. Hard-coded, not env-configurable — retuning it is a spec change,
the same convention as the other engine constants (ADR 0015's precedent).

### Output cap: deterministic head+tail, not summarization

Truncated tool output keeps the first `OUT_HEAD` (9,000) and last
`OUT_TAIL` (5,000) chars around a middle-elision marker naming the true
total (`kubectl-guard.md` rule 11). kubectl tables carry headers and oldest
rows at the head; logs and events carry recency at the tail — head-only
truncation keeps one informative end and destroys the other, and the model
burns turns re-querying what it cannot see. Rejected alternative: **model
summarization of long outputs** — it would add a model call per oversized
tool result (the very spend the cap exists to bound), be
non-deterministic, and move output shaping out of the guard's pure,
unit-testable path. Head+tail is deterministic, free, and keeps the #20
recovery hint's contract intact.

## Consequences

- The token soft-stop is a second cut reason alongside the step budget;
  both flow through the same reporting pass, so no new failure mode
  exists — a token-cut patrol reports from observations so far.
- The 500,000 default sits between the observed good runs (~216K) and the
  pathological one (~1M): normal patrols never feel it; runaway ones stop
  at half the worst case. Retuning is a spec change.
- The head+tail marker format supersedes the #20 marker (which named only
  the total); the recovery hint is unchanged. Dry-mode fixture capping is
  unchanged (head-only, marker-free — demo data, not cluster output).
- Scoping stays whole-cluster; if per-namespace patrols are ever wanted,
  they need their own issue and a wiki-sharding story that does not exist
  today.
