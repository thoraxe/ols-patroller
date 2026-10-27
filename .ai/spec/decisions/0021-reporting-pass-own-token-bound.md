# 0021. The reporting pass gets its own completion-token bound

Date: 2026-10-26 · Status: proposed

## Context

Real-cluster validation of v0.2.1 (#63): `make_model` applied one
`max_tokens=16000` to every call shape — the same class of bug #49 fixed for
timeouts, one layer down. The shape pass must emit the entire wiki (~15 KB of
markdown) in one structured response, and on a reasoning model the reasoning
tokens come out of the same `max_tokens` allowance. Measured on the live
cluster (43.5k-token report prompt): both attempts ended at
`finish_reason=length` with all 16000 completion tokens spent reasoning,
`reasoning_tokens == 16000`, zero content — `structured.ainvoke()` returned
`None`, no wiki write, for ~14 consecutive patrols (the wiki frozen since
2026-10-15). The same run with the reporting call's budget raised to 64k
completed at ~20k completion tokens (a ~15.6k-char wiki, then a repair pass at
~10k) and committed — the first successful write in 11 days.

Design space: (1) raise the global bound — wasteful for exploration turns,
whose outputs are small; (2) shrink what the report must emit (section-diff
wiki updates) — deferred to #10's cost-control track, since the successful
run shows the current contract fits once the budget is right; (3) split the
bound per call shape, mirroring #49.

## Decision

Per-call-shape completion-token bounds, hard-coded (not
env-configurable): exploration turns keep `REQUEST_MAX_TOKENS` (16000); the
structured reporting call and its repair pass get `REPORT_MAX_TOKENS`
(64000). Like the timeout, `max_tokens` is a client-level property in
`langchain-openai`, so it parametrizes the same `make_model` seam (default
the exploration bound) — the reporting model instance carries both
reporting bounds.

## Consequences

A reporting pass that truncates at 64k (a wiki far beyond the soft
`WIKI_CHAR_BUDGET` of 20k chars, ~4× today's successful emissions) is now a
wiki-content problem, not a budget problem — and stays visible either way:
the companion change classifies the empty-result failure reason from the raw
response (`finish_reason`/usage), so `truncated at max_tokens` is diagnosable
from the console without instrumentation (#63, rule 7c). Exploration turns
keep their small budget — a runaway turn still fails cheaply. If deployments
need different numbers, that knob belongs to #10 (cost control), not here.
See ADR 0018 for the timeout half of the same per-call-shape split.
