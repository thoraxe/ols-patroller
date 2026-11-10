# 0036. Retune the reporting completion bound to 28000

Date: 2026-11-10 · Status: proposed

> **Deferral resolved (#106).** Design-space option 4 — the empty-wiki reason
> naming bound truncation — is specced on its own as
> `what/reporting-notifications.md` rule 7h. Nothing in this record's decision
> changes: the option stays rejected *for this change*, for the reason given
> below (a value change must not carry a classification change through one gate),
> and this record's bound (28000) and its acceptance soak stand as written.

## Context

ADR 0034 set `REPORT_MAX_TOKENS` to 24000 and made the first reporting attempt
take the retry's cheap shape (one `SHAPE_TOOL_KEEP` cap of 500 characters plus a
pinned `REPORT_CONTRACT` concision ask). Its own closing condition was explicit:
"If a 24k bound still measures tight after #86's anomaly-log byte bound lands,
retuning it is another spec change with a fresh soak."

The v0.3.2 soak (#101) supplies that measurement, from the **completion** side,
and it retunes the bound while #86 is still open — deliberately, because the
clause's #86 condition does not govern this failure. #86 bounds the wiki's
Anomaly-log **input** bytes: it is the wiki-size lever ADR 0034 named as the
other half of the cost, and it stays open on its own schedule. #101's failure is
not an input-size failure — the wiki was 19,215 characters, *under* the rule-29
20,000-char soft budget. On `ols-demo-2` (`glm-5-3`) the **first** reporting
attempt's rule-7f line read:

```
reporting pass attempt 1 failed (empty wiki, took 289 s,
                                 in 16435 out 24000 tokens, bound 24000 tokens)
```

`out` equals the bound exactly: the completion budget was exhausted, not the
gateway clock (289 s, nowhere near ~900 s). The on-disk wiki was ~4.8k tokens,
so the budget went to reasoning/truncation rather than to emitting the wiki. The
rule-7b retry then produced a 6-finding report in ~230 s **on the same 24000
bound** — so a valid report fits *under* 24000, and the truncating attempt's
overshoot is reasoning variance, not a too-small bound for the artifact.

Two throughput measurements now disagree by more than 2×:

| Measurement | Payload shape | Rate |
|---|---|---|
| #101 attempt 1 (truncated) | #97 cheap shape | 24000 tokens / 289 s ≈ **83 output tok/s** |
| #97 retry (completed, ~10k tokens) | #97 cheap shape | ~10000 / ~260 s ≈ **35–40 output tok/s** |

At 83 tok/s, 32000 would be ~385 s — comfortable. At 35–40 tok/s, 32000 is
~800–914 s — the gateway window itself, which is the #88/#97 failure mode
returning. The data does not let the value be derived from a single rate, so it
has to come from which failure is cheaper.

Design space (the issue names 28k and 32k):

1. **32000** — the largest headroom, justified by the ~83 tok/s measurement; it
   recreates the wall-clock race if the slow rate is the real one.
2. **28000** — 4000 tokens (17%) of headroom over the bound that was actually
   exhausted, inside the window even at the slow rate.
3. **Leave 24000 and rely on the retry** — the status quo: attempt 1 truncates
   on any patrol that lands in the reasoning tail, and attempt 2 could also
   exhaust the bound, at which point rule 33's total-failure leg fires and the
   wiki is only minimally written. The #101 evidence is one variance step from
   that outcome, so the status quo is not a decision to keep.
4. **Bundle the observability narrowing** (name truncation when a parsed report
   carries an empty `updated_wiki` and the completion count reached the bound)
   into the same change — rejected: it changes rule 7b/7c classification, and a
   value change must not carry a classification change through one gate. It is
   deferred to its own spec item.

## Decision

Option 2: `REPORT_MAX_TOKENS` 24000 → **28000** (rule 8b), with #97's cheap
shape untouched — one `SHAPE_TOOL_KEEP` cap of 500 characters, the rule-5
concision ask, and rule 7g's same-cap rebuild. Rule 8a's `REPORT_TIMEOUT`
(900 s) is unchanged, as is rule 7f's `bound B tokens` field, which follows the
constant (`bound 28000 tokens` after implementation).

The two constraints the number must satisfy, and how 28000 meets them:

- **Above the observed truncation.** The failing attempt needed more than 24000;
  a valid report fits under 24000 (the retry proved it on that bound). 28000 is
  4000 tokens above the exhaustion point, and still 8000 above #63's ~20k
  observed completion — the reasoning-starvation floor that #63's fix depends
  on stays intact.
- **Inside the gateway's ~900 s window at the slowest observed rate.** ~700–800 s
  at 35–40 output tok/s. A token truncation is the cheaper failure: rule 7b
  recovers it in ~230 s, while a 504 burns the whole ~900 s window *and* the
  retry. So the value is set by the slow rate, not the fast one.

28000 is a hypothesis, not a measurement, and it ships with its own soak: 3–5
real patrols recording the existing rule-7f fields (attempt-1 outcome, `in`/`out`
tokens, `bound`). If attempt-1 truncations persist, the next rung is 32000 —
another spec change with a fresh soak, never a hotfix (ADR 0034's rule, which
this record applies rather than repeals).

## Consequences

- The first attempt now has 17% more completion budget than the run that
  exhausted it, so a patrol landing in the reasoning tail has a real chance of
  landing in one attempt instead of paying rule 7b's retry (~230 s plus a second
  input prefill).
- Worst-case wall time per reporting attempt rises from ~700 s to ~800 s at the
  slow rate — still inside the window, but the margin is thinner than 24000's.
  That is the accepted trade: the bound is insurance against reasoning variance,
  and the gateway's wall clock, not the token bound, is the hard cap.
- Nothing else moves: no new configuration surface, no schema change, no
  prompt/teaching change, no guard change, and no change to rule 33's
  total-failure net or rule 7g's retry. The suite pins the constant, the two
  inequality guards (headroom over the exhausted bound, and the window at the
  slow rate), and the flipped `bound 28000 tokens` log-line pins; the retune's
  acceptance evidence — the soak — is not a test, exactly as #97's was.
- The optional #101 observability narrowing stays open as its own item: a parsed
  report with an empty `updated_wiki` whose completion count reached the bound
  would read `empty wiki` today, and naming the truncation is a rule-7b/7c
  classification change.
