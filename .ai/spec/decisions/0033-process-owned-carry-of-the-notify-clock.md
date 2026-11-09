# 0033. Process-owned carry of the notify clock

Date: 2026-11-09 · Status: proposed

## Context

ADR 0026 made notification dedup state-change keyed and added the periodic
digest (`reporting-notifications.md` rule 4c) with a process-owned per-entry
clock, `last_notified: YYYY-MM-DD` (`wiki-memory.md` rule 12c), stamped by
`stamp_last_notified` (rule 4d) for the entries a delivered notification named.
The cadence was meant to be weekly; the v0.3.1 soak showed it firing **every
other patrol** (issue #93).

The soak's git history names the mechanism: patrol N's digest stamps the
entries (`last_notified: N`), patrol N+1's model rewrite drops every pair
(`last_notified: 0`), and patrol N+2 reads an unstamped old wiki, falls back to
`first_seen` (months old), and digests the same entries again. On the soaked
cluster: 18 entries, 18 carrying `verified_patrol`, 18 carrying `severity`, 0
carrying `last_notified`.

`last_notified` is optional metadata the model is never told about — the report
contract teaches `verified_patrol` ("copy or drop it freely; the patroller
re-stamps it") and `severity`, but not this pair — and rule 4d specified only
"stamp the ids", never "carry the old value forward" for entries present in both
wikis. So the clock survived exactly one write.

Design space:

1. **Carry the clock in the process**, the way the staleness clock is carried:
   before the stamp, restore `last_notified` from the old on-disk wiki onto every
   entry present in both wikis.
2. **Teach the contract to preserve it**: add the pair to `REPORT_CONTRACT` as
   process-owned. Weaker on its own — the model already drops optional
   process-owned pairs, and #67's evidence says teaching harder does not
   reliably hold; at best a complement.
3. **Reduce digest noise separately** (digest only warn/crit, longer default):
   tuning would paper over the cadence defect, and the defect is the clock.

## Decision

The notify clock is **carried**, not re-stamped from nothing. A new pure
function, `carry_last_notified(new_wiki, old_wiki) -> str`, runs on every
model-authored accepted write **after** the anomaly-log compaction and
**before** `stamp_last_notified` (rule 4d, `wiki-memory.md` rule 16): each
metadata-section entry of the new wiki takes the old wiki's `last_notified`
value when that value parses as `YYYY-MM-DD` (replacing a model-written pair in
place, appending one when absent), and has any model-written pair **removed**
when the old wiki carries no parseable clock for that id or does not carry the
id at all. The model's copy is therefore never durable — the pair is process-
owned exactly like `verified_patrol` (rule 20), which the expiry pass likewise
carries for entries it does not re-stamp.

Teaching the pair to the model (direction 2) is not the fix: `REPORT_CONTRACT`
is unchanged, and the model may keep dropping the pair. The process, not the
model, owns the clock's durability.

## Consequences

A rewrite that omits `last_notified` can no longer reset the digest cadence:
after a digest at patrol N, patrol N+1's rewrite carries the stamp forward and
patrol N+2 stays quiet. The fixes are pure, byte-preserving, and orthogonal to
the schema — `wiki_version` stays 1, validation still does not judge the pair,
and the keep-old-wiki / empty-wiki / minimal-write legs run no carry (they
write no model-authored content; the minimal write re-uses the old wiki
verbatim, clocks included).

The clock is now as durable as `verified_patrol` and shares its limitation:
because it is date-granular, sub-daily patrols cannot advance it within a day —
a weekly cadence at a 6 h interval is already several patrols, so this only
bites at daily-or-faster *digest* thresholds, which rule 4c's threshold is not.
The `first_seen` fallback for a pre-#35 entry still yields one migration nudge.
