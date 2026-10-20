# 0014. Stale-entry expiry is process-enforced, counted in accepted writes

Date: 2026-10-20 · Status: proposed (issue #7)

## Context

#7 closes the refinement loop: a watch-list entry the model stops re-verifying
must retire — stale memory is worse than no memory for an agent whose wiki is
hypotheses (the OKF/AIX `stale_after` motivation). "Not re-verified within N
patrols" needs a patrol-granular clock, but `last_verified` is date-granular
(`YYYY-MM-DD`) and the wiki records no per-patrol dates; and models are
unreliable counters (ADR 0011's rationale), so trusting the model to expire its
own entries re-creates the stamped-counters problem one level down. The
alternatives all fail differently: duration math (date vs now) hard-codes a
cadence assumption into memory semantics — the same N means different things at
6 h and 12 h intervals, and `--once` has no cadence at all; git-history
archaeology (one dated commit per patrol) could map dates to patrols but breaks
file-only mode (rule 4b) and makes the write path depend on history rather than
the document.

## Decision

Expiry is enforced by the patroller, deterministically, at write time: a pure
post-validation pass maintains a process-stamped `verified_patrol` counter on
every metadata entry — reset when, and only when, the entry's `last_verified`
date advances (or its id is new), carried otherwise — and flips watching
Watch-list entries whose counter is `PATROL_WIKI_STALE_AFTER` (default 5,
`0` disables the flip) writes behind, moving them to the Anomaly log with an
`auto-expired` annotation. The clock counts accepted wiki writes — the same
unit as frontmatter `patrol_count` — never patrol starts.

## Consequences

- The clock lives in the document: inspectable, debuggable, portable across git
  and file-only modes; no hidden state, no history reads at write time.
- Verification is defined operationally — advancing `last_verified` (the report
  contract ties it to actually re-verifying, e.g. running the #6 recheck). A
  model that advances dates without verifying resets its own clock,
  undetectable in-process; the rechecks make such claims auditable, and #8's
  stats will surface verification rates.
- Date granularity under-counts same-day re-verification at sub-daily cadences
  (a re-verified entry's date cannot advance within one day). Bounded: age
  grows at most writes-per-day minus one between resets; N is chosen with
  cadence in mind (default 5 ≈ 30 h at the default 6 h cadence, ≈ 2.5 days at
  12 h).
- Renaming an id, or re-adding a previously expired id after dropping its
  history entry, starts a fresh clock — persistence is keyed on the id the
  model maintains, and the refinement loop already depends on stable ids.
  Re-opening an old entry as watching without re-verifying it expires it
  again: verification is the only reset.
- The flip never deletes history (expired items move to the Anomaly log) and
  never becomes a finding or notification — wiki housekeeping, logged to the
  console; #9's digest is the human surface for long-standing entries. Only
  Watch-list watching entries flip: statuses and placements the model chose
  deliberately are the model's to manage — the flip is a backstop, not an
  override.
- #8's stats read expired entries (status, dates, clock) straight from the
  Anomaly log; a future per-entry `stale_after` override would ride the
  metadata line's additional-pairs forward compatibility, but is deliberately
  not in this contract — one global threshold first.
