# 0015. Wiki stats: an offline command; churn is prose-keyed over a bounded git window

Date: 2026-10-21 · Status: proposed (issue #8)

## Context

"Rules without measures drift" (the OKF/AIX `--stats` motivation quoted in
issue #8): the wiki imposes a discipline — verify what you watch (#7's
`verified_patrol` clock), resolve or expire deliberately, grade confidence
honestly — but nothing measures whether the discipline holds, and an
unmeasured discipline silently rots (the article's most embarrassing audit
number: 303/413 entries graded "high" — a grade everyone gets is not a grade).
Issue #8 asks for four numbers: verification rate, resolution ratio,
confidence spread, and churn (the photocopy-of-a-photocopy effect — entries
reworded every patrol with no status change). Each number needs a source, and
the sources are contested: the patrol itself is non-deterministic and costs
model calls (asking the model to grade its own memory reproduces neither the
number nor the failure); the wiki document is machine-checkable (ADR 0011);
its git history is a one-commit-per-accepted-write record (ADR 0003's commit
discipline, made one-per-accepted-write by ADR 0011's stamping). Churn
additionally must separate mechanical rewrites (process stamps, advancing
dates — the write path's own edits) from actual paraphrasing, and must not
grow unboundedly with history.

## Decision

The four numbers are computed by the patroller process in a dedicated offline
`--wiki-stats` mode that reads the wiki and its repo and exits: no model
calls, no cluster reads, no `PATROL_*` env, no writes (no repo
initialization). Three numbers parse the wiki document (`wiki_stats`):
verification rate is the share of Watch-list entries whose `verified_patrol`
equals the frontmatter `patrol_count`; resolution and confidence are status /
confidence vocabulary counts over both metadata sections, with a lopsided
warning when one confidence level holds ≥ 2/3 of ≥ 5 graded entries. Churn
(`wiki_churn` over `wiki_history`) compares, per entry id, the PROSE (the
indented continuation block, byte-exact) across a bounded window — the wiki
file's blobs at the last `WIKI_CHURN_WINDOW` (5) commits — flagging entries
present throughout whose prose changed in every adjacent revision-pair with
status unchanged; metadata-line and frontmatter changes never count as
rewording. The lopsided warning and the churn flags are printed hints on the
human surface, never gates.

## Consequences

- Stats run anywhere the wiki exists — no cluster, no credentials — making
  them a post-mortem and audit tool as well as a health check; patrols remain
  the only thing that costs tokens, and the fake-model e2e is not extended
  (the mode has no model in it).
- Verification rate trusts the process-stamped clock, not model claims: a
  model that advances `last_verified` without verifying inflates its own rate,
  undetectable in-process (ADR 0014's known gap) — the stat surfaces the
  clock, it cannot audit it; #6's rechecks make such claims auditable by a
  human or a future lint-only mode.
- Churn's prose-only comparison immunizes the stat against the write path's
  mechanical edits — `verified_patrol` stamping, `last_verified` advances,
  recheck rewrites, section moves — so the healthy case (re-verified, stable
  prose) is never drift; the cost is that paraphrase inside the metadata line
  (e.g. rewording `source:`) is invisible to churn — accepted, prose is the
  hypothesis text.
- The bounded window keeps history reads O(window) as history grows, and one
  commit ≈ one accepted write (ADR 0011), so the churn window and the
  staleness clock count the same unit (the default 5 mirrors
  `PATROL_WIKI_STALE_AFTER`); entries younger than the window are never
  flagged — drift detection lags by design, better under-reported than
  flapping. File-only wikis (rule 4b) report churn `n/a`.
- The lopsided constants (≥ 2/3 of ≥ 5) are judgment calls pinned in the
  spec and hard-coded like `OUT_CAP`; retuning them is a spec change, not a
  config knob.
