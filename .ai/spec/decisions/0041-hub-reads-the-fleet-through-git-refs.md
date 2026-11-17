# 0041. The hub reads the fleet through git refs, and carries a bounded projection of it

Date: 2026-11-16 · Status: proposed (issue #13)

Numbered 0041 because the parallel #119 contract takes ADR 0040.

## Context

ADR 0039 settled the transport: one remote wiki repo, one branch per cluster,
`cluster-wiki.md` at the same path on every branch. A spoke pushes its own
branch and never reads another. The hub is the reader (#13). It patrols its own
cluster and it also reads the fleet.

Three questions had no answer in 0039.

1. **How does the hub reach the wikis?** It can fetch the remote's branches and
   read each tip. It can clone or check out branches. It can ask a spoke for its
   wiki over an API.
2. **How much of each spoke wiki reaches the model?** One hostile case: twenty
   spokes, each near the 20,000-character soft budget, is 400,000 characters on
   every exploration turn.
3. **What may the hub do with a fleet observation?** It can record it in its own
   wiki. It can write it back to the spoke. It can run a command against the
   spoke to confirm it.

## Decision

**Read the git refs, in place.** The hub fetches the remote and reads
`cluster-wiki.md` at each branch tip (`git show <branch>:cluster-wiki.md`). It
never checks out a branch, never adds a worktree, and never merges. A worktree
per branch stays an operator convenience (ADR 0039). There is no spoke API, so
there is no second service to run and no spoke to modify.

**Carry a bounded projection, not the document.** Per spoke the hub keeps the
frontmatter block. It also keeps the Watch-list and Anomaly-log entry metadata
lines. It drops the free prose. The projection carries at most `FLEET_WIKI_CHARS`
(2000) characters, with a marker line for the rest. The block is capped at
`FLEET_MAX_WIKIS` (5) spokes, with a `not carried:` trailer. The carried
projections, their separators and markers, and the header stay under 13,000
characters. The trailer grows with the number of spokes left out.

The projection keeps the entry metadata lines because they are the fleet
signal: an id, a status, a severity, a confidence, and the cited source. Those
lines compare across clusters. The prose stays out because the wiki is a document
(`constraints.md` rule 3). Re-reading every spoke's prose every patrol also
repeats the cost #85 and #97 removed.

**The hub's memory is its own wiki.** A fleet observation enters the hub's
wiki as an ordinary entry. The `source` pair names the spoke, for example
`source: fleet cluster/alpha`. Or the note goes into `Open questions`. The hub
writes nothing to a spoke branch.

A fleet Watch-list entry carries a recheck the hub's own cluster answers. The
hub cannot run a command against another cluster. Without a local recheck, the
entry could never be re-verified and the expiry clock would retire it.

## Consequences

The hub needs no credential for any other cluster. Its RBAC stays the built-in
`cluster-reader` on its own cluster (ADR 0006). Its egress stays the derived set
of ADR 0030: the wiki remote's host is already in it.

No spoke changes. The spoke's prompt, wiki format, branch layout, push, and
expiry are untouched, so a spoke cannot tell whether a hub reads its branch. The
whole diff is hub-side.

The fleet view is lossy and bounded, and it says so. The `not carried:` trailer
and the `_fleet-truncated:` marker name what the model did not see. The hub
recovers elided spoke prose across patrols, not within one. Its own wiki
accumulates the fleet state it cares about. See the compounding economy of
`patrol-engine.md` rule 3e.

The read is not a write and never becomes one. A fetch failure, a branch without
a wiki file, and a legacy non-UTF-8 blob all degrade to a smaller fleet block.
None of them fails the patrol (the local-first stance of `wiki-memory.md` rule
4e).

A hub and a spoke run the same image and the same manifests. The hub is an
opt-in patch (`in-cluster-deployment.md` rule 18), so the committed deployment
stays a spoke's.

Follow-ups. A per-branch view for humans is the wiki viewer (#26), not the hub.
Fleet topology rendering is #87. Runbook references are #41, a stretch item of
the same milestone; this ADR does not cover them.
