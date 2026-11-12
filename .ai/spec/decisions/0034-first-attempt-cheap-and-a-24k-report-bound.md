# 0034. Make the first reporting attempt the cheap one; report bound 24000

Date: 2026-11-09 · Status: proposed; bound retuned by 0036 (#101), cheap-shape values carried forward

> **Bound retuned.** The 24000 completion bound decided below is retuned to
> 28000 by ADR 0036 (#101), on this record's own condition — "If a 24k bound
> still measures tight after #86's anomaly-log size bound lands, retuning it is
> another spec change with a fresh soak". The #86 half of that condition does
> not gate this change: #86 bounds the wiki's Anomaly-log **input** bytes, while
> #101's truncation is completion-side (`out` equal to the bound on a wiki under
> the 20,000-char budget), so #86 stays open and remains the wiki-size lever.
> Everything else this record decided (the single 500-character
> `SHAPE_TOOL_KEEP` cap, the retired retry constant, the `REPORT_CONTRACT`
> concision ask, the `bound B tokens` log field) is unchanged.

## Context

ADR 0031 halved the reporting completion bound (64000 → 32000) and made the
gateway-failure retry ceiling-aware: attempt 2 rebuilt its base at a third of
the tool-result payload (`SHAPE_TOOL_KEEP_RETRY` 500 vs `SHAPE_TOOL_KEEP` 1500)
and asked for the most concise valid report. That was a recovery path, not a
default — attempt 1 still carried the full payload and a generous bound.

The #94 soak (post-#88 image, ols-demo-2, a >20k-char wiki) showed what that
costs: 3 of 5 patrols' **first** attempts died at the gateway's ~900 s wall
clock (`Error code: 504` at 902 s), and every one of them was saved by attempt
2, which finished a complete report in ~260 s. No patrol lost its wiki, but a
third of them burned a ~15-minute window and ~500k input tokens first.

The arithmetic is the same one 0031 used, one bound later. The retry wrote a
full report — the ~20.5k-char wiki plus its findings — in ~260 s, so its
completion was on the order of ~10k tokens — the whole report (the wiki is
~5k tokens at ≈4 characters per token, plus findings and reasoning) — and its
throughput ~35–40 output tok/s; at that rate a 32000-token bound
can consume ~800–900 s — the window itself. (The #97 issue quotes the same
attempt as "~2m40s" in one place and "~260 s" in its throughput derivation;
the two disagree, so this record uses ~260 s, the figure the ~10k-token report
and the 35–40 tok/s rate agree with. Rule 7f's failure line now carries the real
counts, so a soak can replace this estimate with measured numbers.) The soak's variable failure rate (3/5) says the
remainder is tail latency, not a deterministic overshoot, so the bound is
insurance rather than the fix.

The fix is what the request carries and what it asks for, because those are
what the gateway's wall clock bills. Two levers were in the successful retry:
`compact_messages` truncated **tool-result bodies** from 1500 to 500 characters
(an input-token cut — it never touches the wiki, which rides whole in the initial
user message), and the corrective asked for a smaller answer (an output-token
cut). The 27,773-char document the #88 wiki carried is the *other* half of the
cost and is **not** addressed here: the wiki's own levers are the rule-29a size
note, the character budget (`wiki-memory.md` rules 28–29), and #86's anomaly-log
size bound.

(The ADR sequence skips 0033 because it belongs to the concurrent #93 spec
branch — the digest clock carry — not to this record.)

Design space (issue #97's directions):

1. **Derive the bound from throughput**: lower `REPORT_MAX_TOKENS` toward ~24k
   (≈600–685 s at the observed throughput), keeping it strictly above #63's
   observed ~20k completion so reasoning starvation does not return.
2. **Make the first attempt cheap**: give the first call the retry's shape —
   500-character tool-result compaction plus the pinned brevity ask.
3. **Split the report** into two calls (findings/notifications, then the wiki
   rewrite): rejected by ADR 0031 for a patch release and still rejected here —
   it doubles the input prefill and changes the single-`PatrolReport` surface.
4. Diagnostics: name the bound in the failure log line so a tail is comparable
   across image versions.

## Decision

Both (1) and (2), because the soak's evidence assigns them different jobs —
(2) is the fix, (1) is the headroom:

- `SHAPE_TOOL_KEEP` drops 1500 → **500**, and `SHAPE_TOOL_KEEP_RETRY` is
  **retired**: one compaction cap governs every attempt of every mode (rule 7e).
  There is deliberately no second, smaller cap, because nothing smaller is
  measured and a cap no attempt sends is a lie in the code.
- A pinned **concision ask** joins `REPORT_CONTRACT` (rule 5), so every attempt
  of every mode carries it: the retry already re-states the contract, so the
  ask cannot drift from the attempt that follows it. It is an ask, never a gate
  — completeness requirements and the empty-wiki classification are untouched.
  Its wording scopes the ban to padding *around* the report and says the wiki
  keeps its own section and entry prose, because "no optional prose" read
  unqualified would be a licence to strip the wiki's free prose — the exact
  erosion this record's Consequences section disclaims.
- `REPORT_MAX_TOKENS` drops 32000 → **24000** (rule 8b): ≈600–685 s of
  generation at the observed throughput, inside the 900 s window, and ~4k above
  #63's observed ~20k completion — the condition #63's fix depends on is "above
  the observed completion", not "12k above".
- The attempt-failure log line gains `bound B tokens` (rule 7f), so a soak can
  tell which bound was in force without reading the image tag.

The rule-7g retry keeps its own path — discard the attempt-1 message list,
rebuild the base, re-state prefix + corrective + contract + size note — but it
no longer shrinks the payload, because attempt 1 now carries the same 500-char
shape. Its remaining value is honest and worth keeping: a gateway 504 is tail
latency (3/5, not 5/5), the rebuilt prompt cannot inherit a stale attempt-1
corrective, and the pinned corrective labels the failure. It stays the
last-resort net before rule 33's total-failure leg.

## Consequences

A normal patrol never enters the ~15-minute window with a 3× larger tool-result
payload than the one the recovery path proved sufficient; the worst case per
patrol drops from ~900 s + ~260 s (the failed full-payload attempt plus the
retry) to ~600 s + ~260 s (the bound-truncated attempt plus the same retry), and
a genuinely slow first attempt's retry is now a plain second roll of the dice at
the same cheap shape.

Costs, stated plainly:

- **Every** report now cites from 500-character tool excerpts instead of 1500.
  The report contract asks for short citations (findings carry `evidence` as a
  short excerpt), the full text stays in pod logs and the wiki's prose, and
  #88's recovery path already shipped reports of that fidelity — accepted.
- A model that needs more than 24000 completion tokens for a valid report now
  truncates at a bound it would previously have cleared. That surfaces as
  `empty result (truncated at max_tokens)` (rule 7c) beside the new `bound
  24000 tokens` field, and the ceiling-aware retry answers it; rule 33 remains
  the net. The floor condition (#63) is still satisfied, and the previous
  retune (ADR 0031) set the precedent that retuning is a fresh spec change with
  a soak, not a hotfix.
- The concision ask could erode wiki richness over time. It is scoped to *how*
  the report is said — one line per finding, short evidence, no padding around
  the report — and its pinned text explicitly hands the wiki back its own prose
  (`wiki-memory.md` rules 13, 29b); the wiki's own pressure stays the size note
  and budget (#67, rules 28–29).

Issue #97's remaining acceptance item is a real-cluster soak showing zero
attempt-1 504s **or** a recorded decision that the retry stays the intended path.
This record takes the second alternative as the fallback and states it now: the
retry is intended-path tail-latency insurance (a variable 504, not a deterministic
overshoot), and the spec's tests cannot and do not claim zero 504s. The v0.3.2
soak is tracked as release evidence: if attempt-1 ceiling failures persist, the
fallback decision is recorded in the release notes rather than silently reopening
the bound. The suite pins the constants, the cap, the contract wording, the log
field, and the retry's rebuild.
