# 0024. Total reporting failure joins the minimal-write net

Date: 2026-10-29 · Status: proposed

## Context

ADR 0022 made the *still-empty* reporting outcome (a parsed report whose
`updated_wiki` is empty after its retry) degrade to a minimal write: the
old wiki stamped and committed, the clock advanced. The v0.2.3 soak (#66)
exposed the third failure family the net still misses: the shape pass
returning `None` — no attempt parsed, both attempts killed by the model
gateway's ~900 s request ceiling (504 at ~902 s, twice in one patrol;
once more in the next). `report is None` loses everything: no findings,
no notifications, no wiki write — and the pressure is monotonic, because
the wiki grows every successful patrol and the shape pass's input grows
with it. One such patrol is survivable; a chronically 504ing gateway is
the frozen-wiki failure mode again, now silent: the loop sleeps 6 h
between blind patrols and no line anywhere says the patroller is blind.

Design space for the `None` leg: (a) status quo — warn and write
nothing; (b) a #60-style wiki-repair pass first, then the fallback; (c)
the rule-31 fallback directly, plus a failure notification. (b) is
incoherent here: the wiki-repair pass exists to merge *observed findings*
into the old wiki, and a total failure has no observed report to draw
them from — the call would re-send the very conversation the gateway is
choking on, asking for a merge of nothing. Note also that raising
`REPORT_TIMEOUT` (#49) cannot help: the *gateway* caps the request at
~900 s; only shrinking what the pass carries (rule 7e) can.

## Decision

(c): a total-failure patrol warns (rule 7's line), delivers one failure
notification through the standard apprise path (`notify_type="FAILURE"`
— a blind patrol is always wolf-worthy), and runs the rule-31
minimal-write fallback directly — the wiki-repair pass is skipped on
this leg. The clock-advance rationale is ADR 0022's, unchanged: honest
staleness beats frozen staleness, and `verified_patrol` ages relative to
`patrol_count` precisely so unverifiable memory expires rather than
freezing. The input-shrink half of #66 (rule 7e's compacted
conversation) is the attack on the root cause — this ADR is the net
under whatever still falls through.

## Consequences

A chronically failing reporting pass now produces visible degradation
instead of silence: one FAILURE notification per blind patrol (an
operator action signal, not log archaeology), one no-content commit per
blind patrol (audit-visible in the wiki repo's history), and a staleness
clock that keeps counting. Deferred: an operator-facing "patrols since
last model-written wiki" counter (still derivable from git history —
ADR 0022 already deferred it); loop-mode backoff on repeated total
failures (#10's cost-control family). The notification rides the #9
apprise sinks; with zero sinks configured it is console-only, same as
every notification.
