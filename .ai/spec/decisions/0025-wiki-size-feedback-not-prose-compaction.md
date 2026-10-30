# 0025. Wiki budget feedback goes to the model, not through prose compaction

Date: 2026-10-30 · Status: proposed

## Context

#40 made the wiki a working set: the Anomaly log has a mechanical keep
budget (`WIKI_ANOMALY_KEEP`), and a soft whole-wiki character budget
(`WIKI_CHAR_BUDGET`) warns on overshoot without gating validation. The
v0.2.3 in-cluster soak (#67) shows the soft half does not work: patrols 8
and 9 logged `wiki over length budget: 27296 chars vs 20000 budget`, every
recent patrol did, and the size kept drifting up. The warning machinery is
process-side — the model rewrites the wiki, but it never sees its own
character count; the contract says "keep within 20,000 characters" and
asks the model to estimate a number it cannot observe. Growth is not
neutral: the wiki is injected whole into the next patrol's prompt, and the
shape pass must re-emit it whole under `REPORT_MAX_TOKENS`, so every
over-budget character raises cost and proximity to the model gateway's
request ceiling (#66).

Three responses were on the table:

1. **Teach harder** — name the consequence chain in `REPORT_CONTRACT`
   (over-budget → slower reporting pass → gateway ceiling → next patrol
   re-reads wasted prose).
2. **Show the number** — give the model its actual character count and
   the budget inside the reporting prompt, closing the feedback loop.
3. **Process-side compaction** — a deterministic summarize-and-prune pass
   for prose sections, like `compact_anomaly_log` but for free prose.

## Decision

Do (1) and (2), reject (3) for now.

- Every report-pass invocation — the report pass and the validation-repair
  pass — carries the deterministic `wiki_size_note` line after
  `REPORT_CONTRACT` (`wiki-memory.md` rule 29a): the wiki-in-play's
  character count, the budget, the floored percentage, and either the
  headroom or the exact over-budget excess plus a named prune target. The
  model gets the one input it was missing, at the moment it decides the
  rewrite. The contract names the stakes and requires the returned wiki to
  be smaller than the current one when the size line says over budget
  (`reporting-notifications.md` rule 5 amendment). The wiki-repair pass
  carries no note — its ask is a minimal merge that preserves the old
  wiki, and a prune instruction would contradict it.
- Process-side prose compaction is rejected. "Deterministic" compaction
  of free prose is not available without a model: there is no
  markdown-native rule that can decide which hypotheses are load-bearing,
  and a model-driven compaction pass would add a full structured call on
  every over-budget patrol (the cost #10 is trying to control) while
  risking deletion of exactly the natural-language hypotheses the wiki
  exists to keep (constraint 3, ADR 0011). The process already has the
  mechanical bound it can enforce without judgment — the Anomaly-log keep
  budget — and the soft budget deliberately never gates (a hard gate would
  burn repair passes on formatting; rule 29, ADR 0017).

The split is the same one ADR 0016 draws for sweep efficiency: teach and
surface, do not grow process machinery where judgment is required.

## Consequences

The model can now act on the budget: the number is in the prompt, adjacent
to the instruction, and the write-path warning keeps telling the human
when it still fails. No new configuration surface, no new model call, no
hard gate — the change is one pure formatting function plus contract text.
The feedback is per-invocation and honest even on a fresh deployment
(the seed's size is reported), and the validation-repair pass reports the
rejected wiki it is fixing rather than the on-disk one.

Deferred: if the soak after #67 still shows monotonic growth, the next
options are a process-triggered **budget-repair pass** (one extra
structured call when the accepted wiki is over budget, asking for a prune
to the target) or a per-section bound. Both need their own spec and ADR;
neither is pre-approved here. The operator-facing drift signal stays
`--wiki-stats` (the `size:` line) and the write-path warning.
