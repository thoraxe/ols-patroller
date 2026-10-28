# 0022. The minimal-write fallback advances the patrol clock

Date: 2026-10-28 · Status: proposed

## Context

The #51 empty-wiki classification made a still-empty `updated_wiki`
terminal by design: one named retry, then findings-only delivery with a
stderr warning, wiki unchanged — correct for a single patrol. The #60
real-cluster soak showed what happens when it recurs: 2 of 3 patrols
wrote nothing, the live wiki froze for 11 days (8 patrols, zero writes),
and the staleness clock (#7), `patrol_count`, and `last_patrol` all froze
with it — memory that can never be re-verified never expires either,
because rule 20's clock counts accepted writes and there are none. #63
removed the dominant cause (the reporting call exhausting its completion
budget on reasoning), but the safety net for whatever still fails needs a
decision: is a write with no model content an *accepted write*? Rule 17
says every accepted write advances `patrol_count`; rule 4d's byte-clause
is already superseded by the stamp, but an empty-content advance was
never decided. Design space: (a) keep the clock frozen (status quo —
honest about content, dishonest about time, and it silently disables
#7's expiry); (b) advance the clock by writing the old wiki with stamped
frontmatter (content stale, time true); (c) advance the clock without
writing (a counter bump with no artifact — splits `patrol_count` from
the git history, rule 4d's one-write-one-commit invariant).

## Decision

(b): when the wiki-repair pass also fails, the patrol runs the standard
accepted-write tail (expire → compact → stamp → write → commit → push)
on the old on-disk wiki verbatim — an explicit empty-content leg of
"accepted write". The clock is the patroller's own bookkeeping (models
are unreliable counters, ADR 0014's rationale), and a clock that stops
when the model stumbles is a clock that lies about both time *and*
verifiability: `verified_patrol` ages relative to `patrol_count`
precisely so entries the model can no longer verify expire (rule 21)
instead of living forever. A legacy wiki with no parseable frontmatter
keeps the keep-old-wiki outcome — no frontmatter, no clock to advance.

## Consequences

Repeated reporting failure now degrades to honest staleness instead of
frozen staleness: `patrol_count` and `last_patrol` stay truthful, #7's
expiry keeps working, and each fallback commits (the git history records
the no-content patrols — audit-visible, one console line each). A
pre-#7 old wiki takes the rule-20 migration grace once on the first
fallback, exactly like an upgrade. The wiki-repair pass in front of the
fallback (rule 30) is the model's chance to keep content advancing; the
fallback is the process's guarantee that time does. Considered and
deferred: extending the net to the rule-18 repair pass's own wiki-less
leg (different failure family — a non-empty first wiki that failed
structurally — and that report already consumed two model-retry rounds);
an operator-facing "patrols since last model-written wiki" counter
(#60's observability option) — derivable from git history when needed,
not specced.
