# Wiki Memory (`cluster-wiki.md`)

The agent's persistent memory: a single markdown file the agent reads **and rewrites**
every patrol. The wiki is the project's differentiator — a living, self-correcting
knowledge base of the observed cluster.

## Behavioral Rules

### Storage & Lifecycle

1. The wiki is a single markdown file in a dedicated **wiki directory**; default
   path `wiki/cluster-wiki.md` next to `patrol.py`, overridable with `--wiki`
   [PLANNED: #3 — the default moves from `cluster-wiki.md` beside `patrol.py` so
   the wiki directory can be the patroller's own git repository, rule 4a].
2. On first run (file absent) it is seeded from `WIKI_SEED`, which pre-declares the
   section structure and states the file will be rewritten each patrol.
3. At patrol start the wiki is read in full and injected into the prompt as the
   agent's own prior notes — explicitly framed as possibly WRONG, STALE, or
   INCOMPLETE.
4. After a successful report, the wiki is wholly replaced by
   `report.updated_wiki` (only if non-empty); if the reporting pass fails, the wiki
   is left untouched.

### Git History

4a. The **wiki repo**: the wiki directory (the parent directory of the wiki file)
    is a git repository owned by the patroller. The wiki repo is **local storage**
    — in-cluster it lives on the `/wiki` PVC mount (#2) — and no remote is
    required for any patrol behavior; the optional push (rule 4e) is replication
    only. If the wiki directory contains no repository, the patroller initializes
    one at startup, before the first patrol (default branch `main`), creating the
    directory if absent. The wiki directory must be dedicated to the wiki: if it
    already contains a git repository of any origin, the patroller uses that
    repository as the wiki repo and commits into it [PLANNED: #3].
4b. If git is not on `PATH`, or repository initialization fails, the patroller
    logs a warning and runs the process **file-only** — no init, no commit, no
    push; the wiki file itself keeps working. A missing or broken git never fails
    or blocks a patrol [PLANNED: #3].
4c. Every wiki write is atomic: content is written to a temporary file in the
    wiki directory and renamed over the wiki file. A crash mid-write must never
    truncate or delete the existing wiki; if the rename fails, the temporary file
    is removed, the old wiki stands, and the error propagates to the caller. The
    commit history is the second net — a bad rewrite is always revertible
    [PLANNED: #3].
4d. One wiki-changing patrol, one commit. When a successful report's
    `updated_wiki` has replaced the wiki file, the patroller stages exactly the
    wiki file (never anything else in the wiki directory) and commits with:
    - identity `ols-patroller <patrol@CLUSTER_ID>` (`CLUSTER_ID` from
      `PATROL_CLUSTER_ID`, default `local`), passed per command as
      `-c user.name=… -c user.email=…` together with `safe.directory` for the
      wiki directory — git invocations must not depend on the invoking user's
      config or `HOME` (non-root, random-UID safe);
    - message `patrol N: X findings, Y warn`, where N is the number of commits
      already in the wiki repo plus one, X is the report's finding count, and Y is
      the count of findings of severity `warn` or `crit`.
    A patrol that does not change the wiki file on disk — empty `updated_wiki`,
    failed reporting pass, or byte-identical rewrite — creates no commit
    [PLANNED: #3].
4e. The remote push is optional replication of the local wiki repo, never a
    dependency: it runs only if `PATROL_WIKI_REMOTE` is set, pushing the current
    branch to the same-named branch on that remote after each successful commit
    (deploy-key wiring is the deployment's concern, #2 — the patroller only runs
    `git push`). The push is best-effort and bounded: at most `PUSH_ATTEMPTS` (3)
    attempts, `PUSH_TIMEOUT` (30 s) per attempt, `PUSH_BACKOFF` (5 s) between
    attempts; any failure is logged and never fails the patrol or blocks the loop
    indefinitely — the local commit is the source of truth (local-first)
    [PLANNED: #3].

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

- `--wiki` — path to the wiki file (default `wiki/cluster-wiki.md` beside
  `patrol.py` [PLANNED: #3; was `cluster-wiki.md`])
- `PATROL_CLUSTER_ID` — cluster identity used in wiki commit emails
  (`patrol@<cluster-id>`); default `local` (the in-cluster Deployment, #2, sets
  the real cluster name) [PLANNED: #3]
- `PATROL_WIKI_REMOTE` — if set, each wiki commit is pushed to this git remote
  (best-effort and bounded, rule 4e; local commit is the source of truth)
  [PLANNED: #3]

## Constraints

- **The wiki stays a document, not a database.** Planned structure (skeleton schema,
  per-entry metadata) must keep free prose *inside* entries — over-structuring kills
  the natural-language hypotheses. This is a load-bearing design constraint of #5.
- Storage remains plain markdown files (git-backed per #3), not a vector store or
  memory service; an MCP memory facade returns only if a concrete trigger fires
  (#16 decision record).
- Single writer (one patrol process), whole-file read/write, low frequency — the
  access pattern that made git the chosen backing store.
- **Git is process-level persistence, never an agent capability.** All git activity
  (init, commit, push) runs in the patrol process for durability; the agent's tool
  surface stays exactly `[read_file, kubectl_readonly]` — `safety-model.md` layer 2
  is unchanged by #3.
- **Dedicated wiki directory.** The wiki directory holds only wiki state (the wiki
  file, its git repo, transient temp files). Pointing `--wiki` into a directory
  someone cares about means patrol commits land in its git history [PLANNED: #3].

## Planned Changes

| Ticket | Summary |
|---|---|
| #3 | Git-backed wiki: wiki directory is a git repo (init at startup, file-only fallback), atomic write (temp + rename), one commit per wiki-changing patrol (`patrol N: X findings, Y warn`), optional `PATROL_WIKI_REMOTE` push — bounded, best-effort, local-first |
| #5 | Skeleton schema + validation-on-write: YAML frontmatter (`wiki_version`, `cluster_id`, `patrol_count`, `last_patrol`), fixed section set, per-entry metadata (`id`, `status`, `first_seen`, `last_verified`, `source`, `confidence`), one repair pass on invalid write else keep old wiki |
| #6 | Per-entry `recheck:` commands — each watch-list entry carries the kubectl invocation that re-verifies it (self-linting wiki) |
| #7 | `stale_after`: auto-expire watch-list entries not re-verified within N patrols (flip to `expired`, keep in anomaly log) |
| #8 | `--wiki-stats`: four wiki-health numbers — verification rate, resolution ratio, confidence spread, churn/paraphrase-drift |
