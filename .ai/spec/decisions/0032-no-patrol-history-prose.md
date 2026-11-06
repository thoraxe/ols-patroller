# 0032. Ban patrol-history prose by teaching, not by stripping

Date: 2026-11-06 · Status: proposed

## Context

The live wiki (#85 evidence, cluster `ols-demo-2`) is 27,773 characters
against the 20,000 `WIKI_CHAR_BUDGET` — 39% over, and growing. The
largest avoidable driver is model-authored prose that duplicates what git
already stores: the frontmatter plus intro (1,479 characters) carry a
per-patrol **HEADLINE** line and a hand-maintained
`History: #30 Nov-5 01:38, #29 Nov-4 19:16, … #1–#3 Oct-14` list. That
prose is already stale and self-contradicting — the intro reads
"Last: #31 2026-11-05T07:47Z" while the frontmatter reads
`patrol_count: 36`, `last_patrol: 2026-11-06T15:12`. The model is
maintaining a worse copy of `git log`.

The specs already point this way: rule 9 designates the wiki plus its git
history as the audit trail, and rule 28 prunes the working set because
"git history is the archive" (#40, ADR 0017). A prose history in the
document is a second, lossy archive. #67 then shipped the model-facing
size note (`wiki_size_note`, rule 29a, ADR 0025) precisely so the model
could see its own growth and prune — the note is deployed, and the wiki
kept growing. The note alone is not working.

Three responses were on the table:

1. **Teach** — forbid the class of prose in `REPORT_CONTRACT` and in the
   patrol prompt, leaving the existing budget/size-note pressure to
   measure what remains.
2. **Strip** — a process-side pass that removes history-looking prose
   from an accepted wiki, the way `compact_anomaly_log` removes closed
   entries.
3. **Validate** — make `validate_wiki` reject patrol-history prose, so
   the write gate forces the model to drop it.

## Decision

Do (1). Reject (2) and (3).

- The ban is taught in the two places the model reads: `REPORT_CONTRACT`'s
  working-set item (`reporting-notifications.md` rule 5's #85 clauses) and
  the system prompt's hard-rules / patrol-order block
  (`patrol-engine.md` rule 3f). Scope, rationale, and consequences are
  `wiki-memory.md` rule 29b. The wiki-repair pass's minimal merge drops
  history prose instead of preserving it (rules 30 / 7d) — a merge that
  preserved a HEADLINE log would re-teach the model exactly what the ban
  forbids. Rule 5's #51 copy-verbatim sentence is narrowed by the same
  exception in the same clause (`except that patrol-history prose is never
  carried forward`), because a no-change patrol over a history-bearing wiki
  would otherwise be told both to copy it and to drop it; content
  completeness is untouched, and the empty-wiki corrective repeats the
  narrowed wording so no model-facing surface carries both asks.
- (2) is rejected for ADR 0025's reason plus a scope reason. A
  deterministic rewrite of free prose is not available without a model:
  there is no markdown-native rule that identifies "patrol history" in
  arbitrary prose without false positives on load-bearing hypotheses, and
  a model-driven strip pass costs an extra structured call on exactly the
  patrols already near the gateway ceiling (#66). It also treats the
  symptom: prose the model never writes needs no cleanup pass.
- (3) is rejected because the wiki stays a document (`constraints.md`
  rule 3) and `validate_wiki` judges structure only (rule 15): a prose
  check would need the same heuristic classification as (2), and a hard
  gate would burn repair passes on formatting instead of hypotheses — the
  exact reason rule 29's length budget is soft (ADR 0017).

The split is ADR 0016's and ADR 0025's: teach and measure where judgment
is required, do not grow process machinery for it.

**Difference from ADR 0025.** 0025 rejected a *mechanical rewrite of prose
the model had already written*. 0032 forbids *a class of prose the model
must not write in the first place*. They are complementary, not
contradictory: 0025's feedback loop (the size note) is the measure that
makes the ban enforceable by observation; 0032 removes the most
avoidable source of the growth 0025 could only report.

## Consequences

No schema change and no write-path change: `wiki_version` stays 1,
`validate_wiki` learns no prose rule, and the wiki is still replaced
wholesale from `report.updated_wiki`. The change is contract text plus
prompt text plus tests pinning both, so a context-clear implementer adds
strings and nothing else. Per-entry hypothesis state — rule 7's
persistence tracking, the metadata line's `first_seen`/`last_verified`/
`status` — is deliberately **not** history by this decision: it is
current-entry state the refinement loop reads, and a rule that swept it up
would break #6/#7/#35.

Acceptance is manual (repo #85): after N patrols on a fixed cluster the
intro and section sizes stay bounded and the wiki returns under 20,000
characters without mechanical pruning. That acceptance depends on #88's
reporting-pass payload work, which the release milestone gates on — a
smaller wiki reduces the payload the pass reads and re-emits, but it is a
contributing fix, not a substitute. The Anomaly log's 16,402 characters
are the single largest section and are not this decision's class of prose:
its byte bound is #86's.

Sequence note: this record is 0032 because 0031 belongs to the spec PR
for #88, drafted in parallel from the same `main`; the decision index's
rows union-merge at implementation time.
