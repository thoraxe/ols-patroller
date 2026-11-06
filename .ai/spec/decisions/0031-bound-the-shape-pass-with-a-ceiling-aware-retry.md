# 0031. Bound the shape pass with a ceiling-aware retry

Date: 2026-11-06 · Status: proposed

## Context

The reporting pass still failed at the model gateway's ~900 s wall clock after
#66 bounded its *input* (`SHAPE_TOOL_KEEP` 1500, rule 7e): 4 of the last 5
v0.3.0 patrols produced no report. The rule-33 total-failure net behaved
correctly (one FAILURE notification, then a minimal write), but each failed
patrol cost ~41 minutes and ~550k input tokens and lost every observation.
Measured on ols-demo-2 (`rits/zai-org/glm-5-3`, a 27,773-char wiki with a
16,402-char Anomaly log): both attempts died as `Error code: 504` at 902 s.

The arithmetic names the cause. #63 raised `REPORT_MAX_TOKENS` to 64000 to stop
reasoning exhaustion, and the run that justified it completed at ~20k
completion tokens. A 64k-bounded generation at the observed throughput
(64000 tokens / 902 s ≈ 71 output tok/s) needs ~900 s to reach its bound — the
gateway's ceiling, exactly. The bound is generous enough that a slow or
reasoning-heavy response can spend the whole *gateway* window, and the
identical retry then spends a second one (rule 7/7b's retry re-sent the same
request verbatim). Two more contributors are the wiki's size (#85/#86 own the
prose/history and anomaly-log payload) and exploration overshoot (547,804 vs
500,000 input tokens, checked per turn, #10).

Design space:

1. **Lower the reporting completion bound** so one generation finishes well
   inside the gateway window.
2. **Split the report into two calls** (findings/notifications, then the wiki
   rewrite) so each is small — rejected: it doubles the input prefill (the
   whole compacted conversation plus the contract, twice), changes delivery
   ordering and the single-`PatrolReport` schema surface, and #85/#86 shrink
   the payload independently — a v0.4.0-scoped restructure is not worth
   carrying in a v0.3.1 reliability patch.
3. **Do not retry the identical call** when the failure was the gateway
   ceiling: shrink the request and ask for brevity instead.
4. Instrument the attempt so the ceiling is measurable from pod logs (rule 7f
   already carries elapsed seconds; the token counts were missing).

## Decision

`REPORT_MAX_TOKENS` drops 64000 → 32000, hard-coded as before (rule 8b). 32000
caps generation at roughly half the wall time at the observed throughput
(~450 s), leaving headroom under the ceiling, and sits ~12k above #63's
observed 20k completion — the bound stays strictly above the point where
reasoning consumes the whole allowance, so ADR 0021's reasoning-exhaustion fix
stays in force (this record amends that **value**, not the per-call-shape
split).

A gateway-failure retry becomes ceiling-aware (rule 7g): when attempt 1 fails
with the rule-7f `gateway failure (…)` reason, attempt 2 rebuilds its base from
the raw conversation compacted at the new hard-coded `SHAPE_TOOL_KEEP_RETRY`
(500 characters instead of 1500), discards the failed attempt's message list,
and appends one user message — mode prefix, a pinned corrective asking for the
shortest valid report, `REPORT_CONTRACT` re-stated after it, the rule-29a size
note last. Every shape-pass mode shares the loop, so all three (report,
validation repair, wiki repair) shrink. Non-gateway failures (4xx, plain
exceptions, empty results, empty wikis) keep their existing correctives and the
1500-char compaction — an empty wiki is still answered by re-demanding the
wiki, never by asking for brevity.

The attempt-failure log line gains the attempt's token counts
(`in I out O tokens`, `?` when there is no raw response), so cost and
throughput are readable from pod logs in every failure mode.

## Consequences

The reporting pass's single call now has ~450 s of generation budget inside a
900 s gateway window, and its worst-case retry carries a third of the
tool-result payload plus an explicit brevity ask — the two costs the #88 soak
paid twice per patrol both shrink. A genuinely unproducible report still ends
in the rule-33 net (one FAILURE notification, one minimal write); nothing about
the total-failure contract changes.

A model that needs more than 32000 completion tokens for a valid report now
truncates at a bound it would previously have cleared — visible as
`empty result (truncated at max_tokens)` (rule 7c) with the new token counts
beside it, and answered by the ceiling-aware retry. The remaining lever is the
wiki's size, which #85 (prose/history payload) and #86 (anomaly-log bytes)
own; if a 32k bound still measures tight after those land, retuning it is
another spec change with a fresh soak, per ADR 0021's convention.
