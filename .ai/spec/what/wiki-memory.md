# Wiki Memory (`cluster-wiki.md`)

The agent's persistent memory: a single markdown file the agent reads **and rewrites**
every patrol. The wiki is the project's differentiator — a living, self-correcting
knowledge base of the observed cluster.

## Behavioral Rules

### Storage & Lifecycle

1. The wiki is a single markdown file; default path `cluster-wiki.md` next to
   `patrol.py`, overridable with `--wiki`.
2. On first run (file absent) it is seeded from `WIKI_SEED`, which pre-declares the
   section structure and states the file will be rewritten each patrol.
3. At patrol start the wiki is read in full and injected into the prompt as the
   agent's own prior notes — explicitly framed as possibly WRONG, STALE, or
   INCOMPLETE.
4. After a successful report, the wiki is wholly replaced by
   `report.updated_wiki` (only if non-empty); if the reporting pass fails, the wiki
   is left untouched.

### Format Contract

5. Canonical sections (from seed and report contract): **Topology**, **Baselines**,
   **Watch-list**, **Open questions**, **Anomaly log**.
6. Entries are tagged inline with `(source, timestamp, confidence)` — citing what was
   observed, when, and how sure the agent is.
7. The watch-list is the refinement loop: patrol N writes the rules patrol N+1
   follows. Real runs show per-entry persistence tracking ("Unfixed through 5th
   patrol", "unchanged 18:44") carried forward across patrols.

### Epistemics

8. The wiki is **hypotheses**; the live cluster is **truth**. Each patrol re-validates
   wiki claims against reality and corrects or expires anything stale — never the
   other way around.
9. The wiki is the audit trail for a non-deterministic agent: since patrols are not
   reproducible, the wiki (plus logs) is the record of what was believed and when.

## Configuration Surface

- `--wiki` — path to the wiki file (default `cluster-wiki.md` beside `patrol.py`)

## Constraints

- **The wiki stays a document, not a database.** Planned structure (skeleton schema,
  per-entry metadata) must keep free prose *inside* entries — over-structuring kills
  the natural-language hypotheses. This is a load-bearing design constraint of #5.
- Storage remains plain markdown files (git-backed per #3), not a vector store or
  memory service; an MCP memory facade returns only if a concrete trigger fires
  (#16 decision record).
- Single writer (one patrol process), whole-file read/write, low frequency — the
  access pattern that made git the chosen backing store.

## Planned Changes

| Ticket | Summary |
|---|---|
| #3 | Git-backed wiki: `/wiki` becomes a git repo, one patrol one commit, atomic write (temp + rename), optional remote push that never blocks the patrol loop |
| #5 | Skeleton schema + validation-on-write: YAML frontmatter (`wiki_version`, `cluster_id`, `patrol_count`, `last_patrol`), fixed section set, per-entry metadata (`id`, `status`, `first_seen`, `last_verified`, `source`, `confidence`), one repair pass on invalid write else keep old wiki |
| #6 | Per-entry `recheck:` commands — each watch-list entry carries the kubectl invocation that re-verifies it (self-linting wiki) |
| #7 | `stale_after`: auto-expire watch-list entries not re-verified within N patrols (flip to `expired`, keep in anomaly log) |
| #8 | `--wiki-stats`: four wiki-health numbers — verification rate, resolution ratio, confidence spread, churn/paraphrase-drift |
