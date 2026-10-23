# 0019. An empty `updated_wiki` is a failed report, not a no-op signal

Date: 2026-10-22 · Status: proposed

## Context

Two real-cluster occurrences (#51): a local patrol delivered 8 findings but
omitted `updated_wiki` (the pydantic field's lenient default parsed it to
`""`); an in-cluster patrol returned a valid `PatrolReport` with zero
findings and an empty wiki after a 59-tool-call exploration — 508k input
tokens, no write, no commit, no output line. The process could not
distinguish "the model chose not to update anything" from "the model failed
to emit what it meant to": both arrive as the same empty string, and both
were silent. Design space: contract hardening alone (the model is told to
always emit the wiki), observability alone (warn on the empty no-op),
retryable classification (treat empty-after-exploration as a failed attempt
in the existing attempt-2 machinery), or a schema-required field (make the
pydantic field required so omission fails parsing).

## Decision

All three soft mechanisms combined, and the hard one rejected: the report
contract states `updated_wiki` is required on every report (nothing-changed
is expressed by returning the wiki unchanged, never by omitting it); the
process classifies a parsed result with an empty `updated_wiki` as a failed
attempt — one retry with a corrective message naming the empty wiki; a
still-empty retry delivers its findings and notifications with one stderr
warning, wiki unchanged. The pydantic field keeps its lenient default: the
parser never enforces the requirement, because a schema-required field would
turn "8 findings + omitted wiki" into a parse failure and cost the patrol its
findings when the retry also failed.

## Consequences

The silent no-op becomes visible and countable; the good half of a partial
report (findings) always survives. One extra model call happens only on the
failure path. The "no update intended" signal ceases to exist — a patrol with
nothing to change returns the wiki unchanged, which still writes and commits
(the stamp advances `patrol_count`), keeping the staleness clock and git
history honest. The classification keys on the wiki field alone; zero
findings never triggers it (a clean cluster is legal).
