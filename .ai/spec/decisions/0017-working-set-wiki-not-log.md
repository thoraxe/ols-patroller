# 0017. The wiki is a working set; git history is the archive

Date: 2026-10-21 · Status: proposed (issue #40)

## Context

Nothing mechanically limits the wiki's growth. The Anomaly log is append-only
by nature — #7's expiry flips land there, resolutions accumulate — while the
whole wiki is injected into every patrol's prompt and re-emitted whole by the
shape pass under `max_tokens` 16,000. Today's only ceiling is that de-facto
cap: an over-long wiki fails the reporting pass rather than being trimmed.
Meanwhile the wiki repo (#3) already retains every accepted write, one commit
per patrol — a complete archive nobody needs at patrol time. The Watch-list
converges on its own (#7's refinement loop), but resolved/expired history
piles up in the Anomaly log with no bound, and prose sections can bloat too.

## Decision

Git history is the archive; the wiki is the working set (#40,
`wiki-memory.md` rules 28–29). Two budgets, both process-owned: (1) a
mechanical keep budget on the Anomaly log — every `watching` entry plus the
last `WIKI_ANOMALY_KEEP` (20) resolved/expired entries in document order
survive each accepted write, earlier ones pruned by the patroller (whole
items, recoverable from the repo); (2) a soft whole-wiki character budget
(`WIKI_CHAR_BUDGET`, 20,000) that warns — write path, repair pass,
`--wiki-stats` — and teaches via the report contract, but never gates
validation.

## Consequences

A wiki that would grow unboundedly plateaus at a bounded working set, with
the overshoot measurable in `--wiki-stats` (rules without measures drift).
The audit trail spans wiki + git history (rule 9): pruning deletes from the
working set, never from the archive. Hard-gating length was rejected — it
would burn the one repair pass on formatting instead of hypotheses and push
against #5's document constraint; model-judged compaction was rejected for
the same reason as model-judged expiry (ADR 0014: models are unreliable at
self-limiting bookkeeping); per-section budgets and truncation were rejected
as over-structuring / structure-destroying. The first accepted write after
adoption may prune a burst of legacy entries at once (one console line each,
once). Watching entries and the Watch-list are never pruned — live memory
stays whole; only closed history compacts. #9's digest remains the human
surface for long-standing live entries.
