# Wiki Memory (`cluster-wiki.md`)

The agent's persistent memory: a single markdown file the agent reads **and rewrites**
every patrol. The wiki is the project's differentiator — a living, self-correcting
knowledge base of the observed cluster.

## Behavioral Rules

### Storage & Lifecycle

1. The wiki is a single markdown file in a dedicated **wiki directory**; default
   path `wiki/cluster-wiki.md` next to `patrol.py`, overridable with `--wiki`
   [DONE: #3 — the default moves from `cluster-wiki.md` beside `patrol.py` so
   the wiki directory can be the patroller's own git repository, rule 4a].
2. On first run (file absent) it is seeded from `WIKI_SEED`, which pre-declares the
   section structure and states the file will be rewritten each patrol.
3. At patrol start the wiki is read in full and injected into the prompt as the
   agent's own prior notes — explicitly framed as possibly WRONG, STALE, or
   INCOMPLETE.
4. After a successful report, the wiki is wholly replaced by
   `report.updated_wiki` (only if non-empty); if the reporting pass fails, the wiki
   is left untouched. [DONE: #5 — the replacement is additionally conditioned
   on skeleton validity: validate → one repair pass → else keep the old wiki
   (rules 15–19).]

### Git History

4a. The **wiki repo**: the wiki directory (the parent directory of the wiki file)
    is a git repository owned by the patroller. The wiki repo is **local storage**
    — in-cluster it lives on the `/wiki` PVC mount (#2) — and no remote is
    required for any patrol behavior; the optional push (rule 4e) is replication
    only. If the wiki directory contains no repository, the patroller initializes
    one at startup, before the first patrol (default branch `main`), creating the
    directory if absent. The wiki directory must be dedicated to the wiki: if it
    already contains a git repository of any origin, the patroller uses that
    repository as the wiki repo and commits into it [DONE: #3].
4b. If git is not on `PATH`, or repository initialization fails, the patroller
    logs a warning and runs the process **file-only** — no init, no commit, no
    push; the wiki file itself keeps working. A missing or broken git never fails
    or blocks a patrol [DONE: #3].
4c. Every wiki write is atomic: content is written to a temporary file in the
    wiki directory and renamed over the wiki file. A crash mid-write must never
    truncate or delete the existing wiki; if the rename fails, the temporary file
    is removed, the old wiki stands, and the error propagates to the caller. The
    commit history is the second net — a bad rewrite is always revertible
    [DONE: #3].
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
    [DONE: #3].
4e. The remote push is optional replication of the local wiki repo, never a
    dependency: it runs only if `PATROL_WIKI_REMOTE` is set, pushing the current
    branch to the same-named branch on that remote after each successful commit
    (deploy-key wiring is the deployment's concern, #2 — the patroller only runs
    `git push`). The push is best-effort and bounded: at most `PUSH_ATTEMPTS` (3)
    attempts, `PUSH_TIMEOUT` (30 s) per attempt, `PUSH_BACKOFF` (5 s) between
    attempts; any failure is logged and never fails the patrol or blocks the loop
    indefinitely — the local commit is the source of truth (local-first)
    [DONE: #3].
4f. The wiring of rules 4a–4e into the process is verified end-to-end by the
    fake-model e2e: the repo is ensured at startup via `main()` before the first
    patrol, the patrol tail runs write → commit → push in that order, a
    byte-identical rewrite creates no commit, the push runs only when
    `PATROL_WIKI_REMOTE` is set, and a failed reporting pass leaves the wiki
    untouched [DONE: #4]. [DONE: #5 — the tail becomes validate → repair once →
    stamp → write → commit → push (rule 16); the stamp advances `patrol_count` on
    every accepted write, so the byte-identical case is superseded (rule 17), and
    the e2e gains the repair and keep-old-wiki paths (rules 15–19).]
    [DONE: #7 — the tail becomes validate → repair once → deliver → expire
    → stamp → write → commit → push, and the e2e gains the expiry wiring: a
    stale watching entry flips into the Anomaly log on the written wiki, a
    re-verified entry's clock resets, and each flip logs a console line
    (rules 20–22).]

### Format Contract

5. Canonical sections (from seed and report contract): **Topology**, **Baselines**,
   **Watch-list**, **Open questions**, **Anomaly log**. With the skeleton schema the
   set is enforced on write — all five sections required, each exactly once, no
   others [DONE: #5 — rule 11].
6. Entries are tagged inline with `(source, timestamp, confidence)` — citing what was
   observed, when, and how sure the agent is. [DONE: #5 — in Watch-list and
   Anomaly log the tag is formalized as the entry metadata line (rule 12); in the
   prose sections it stays an unvalidated convention (rule 13).]
7. The watch-list is the refinement loop: patrol N writes the rules patrol N+1
   follows. Real runs show per-entry persistence tracking ("Unfixed through 5th
   patrol", "unchanged 18:44") carried forward across patrols. The recheck
   command (rule 12a) is that rule made executable: patrol N+1 runs the entry's
   recheck first when re-verifying it, and rewrites a recheck whose
   verification path has changed [DONE: #6]. [DONE: #7 — the loop's expire
   leg is process-enforced: an entry whose `last_verified` stops advancing for
   `PATROL_WIKI_STALE_AFTER` accepted writes is auto-expired by the patroller
   (rules 20–22).]

### Epistemics

8. The wiki is **hypotheses**; the live cluster is **truth**. Each patrol re-validates
   wiki claims against reality and corrects or expires anything stale — never the
   other way around.
9. The wiki is the audit trail for a non-deterministic agent: since patrols are not
   reproducible, the wiki (plus logs) is the record of what was believed and when.

### Skeleton Schema

The machine-checkable structure wrapped around the free prose [DONE: #5]. The
file's Constraints section is load-bearing here: structure frames entries, prose
lives inside them — the wiki stays a document, not a database. Only the two
hypothesis-tracking sections (Watch-list, Anomaly log — the refinement loop and
the history #7 expires into) carry per-entry structure; everything else is prose.

10. **Frontmatter.** The wiki file begins with a YAML frontmatter block — a `---`
    fence line, a YAML mapping, a closing `---` fence line — followed by the
    markdown body. The mapping has exactly four keys (closed set; additions are
    spec changes, never model improvisation):
    - `wiki_version` — integer, must equal `WIKI_VERSION` (1). The skeleton
      schema version; bumping it is a deliberate spec migration, never a patrol
      decision.
    - `cluster_id` — non-empty string: the cluster this wiki describes.
    - `patrol_count` — positive integer: how many patrols have written this wiki.
    - `last_patrol` — ISO-8601 datetime (a YAML string scalar parseable by
      `datetime.fromisoformat`, or a YAML-parsed timestamp): the start timestamp
      of the patrol that last wrote the wiki.
    Presence and types of all four keys are validated; the **values** of
    `cluster_id`, `patrol_count`, and `last_patrol` are process-owned — the
    patroller stamps them authoritatively before writing (rule 17), so the
    model's values are format-checked but never trusted (models are unreliable
    counters).
11. **Sections.** The body contains each canonical section exactly once as a
    level-2 heading whose line is exactly `## <Name>` (canonical casing, no
    trailing text; trailing whitespace tolerated): Topology, Baselines,
    Watch-list, Open questions, Anomaly log. No other level-2 headings — an
    invented section is a validation failure, merged away by the repair pass.
    Section order is free; level-1 and level-3+ headings are unconstrained prose
    (a `### Watch-list` does not satisfy the section requirement); an empty
    section is legal — no items, or a non-list placeholder line such as
    `_(empty)_`. Lines inside fenced code blocks (```-delimited) are prose:
    the heading and entry scanners skip them.
12. **Entry metadata line.** In the **Watch-list** and **Anomaly log** sections,
    every top-level markdown list item (a `- ` or `* ` line at column 0) is an
    **entry** whose first line is a metadata line carrying nothing else:
    `- id: <ID> · status: <STATUS> · first_seen: <DATE> · last_verified: <DATE> · source: <TEXT> · confidence: <CONF>`
    - The separator is exactly ` · ` (space, U+00B7 middle dot, space) and the
      six keys appear in this fixed order.
    - `id` — non-empty, unique across the whole wiki (both metadata sections).
    - `status` — one of `watching`, `resolved`, `expired`.
    - `first_seen`, `last_verified` — `YYYY-MM-DD` (zero-padded, valid calendar
      date).
    - `source` — non-empty text citing what was observed (typically the command).
    - `confidence` — one of `high`, `medium`, `low`.
    - Additional `key: value` pairs may follow `confidence` on the metadata line
      (forward-compatible: #6's `recheck`, specified as rule 12a); the rest of
      the line after the six required keys must consist solely of such
      ` · `-separated pairs — any other trailing text is a validation failure.
      Unknown keys are not judged — `recheck` is the first additional key the
      validator judges (rule 12a) [DONE: #6]. A `verified_patrol` pair
      (#7, rule 20) stays unjudged: it is process-stamped and overwritten
      wholesale, so the model may copy, drop, or mangle it freely
      [DONE: #7].
    - Free prose is the indented continuation of the list item (sub-bullets
      allowed) — the natural-language hypothesis. Prose is never validated and
      never appears on the metadata line itself: a trailing text after the
      fields is a validation failure.
12a. **Recheck command** [DONE: #6]. A `recheck` pair on an entry metadata
    line carries the `kubectl_readonly` invocation that re-verifies the entry —
    the citation made regenerable: OKF/AIX rule 2 ("never rewrite the evidence —
    cite the source") translated for regenerable evidence, where the citation
    is the command that regenerates it. Entries become self-linting — a patrol,
    a human, or a future lint-only mode can re-verify any watch-list claim
    without re-deriving the investigation. Structure is added exactly where
    that consumer needs it (rule 13's principle): one more machine-checked
    field on the metadata line; the hypothesis prose is untouched.
    - **Required in Watch-list, optional in Anomaly log.** Every Watch-list
      entry's metadata line carries exactly one `recheck: <command>` pair; a
      Watch-list entry without one is a validation failure (an entry that
      cannot be re-verified is not a watchable claim). In the Anomaly log a
      `recheck` is allowed but not required — history entries whose
      verification loop has ended; a recheck carried along by #7's expiry flip
      stays valid. A duplicate `recheck` pair on one entry (either section) is
      a validation failure.
    - **Tool-argument form.** The value is the exact `kubectl_readonly`
      argument string — everything after the CLI name (`kubectl-guard.md`
      rule 1) — with no leading `kubectl` or `oc` token; a prefixed form is a
      validation failure whose error names the fix. The prefix check is its
      own check, distinguished from and running before the guard-grammar
      check (a bare verb refusal would not name the fix). The value must be
      non-empty and shlex-parseable.
    - **Guard-bounded.** The value must be a command the guard would accept,
      not refuse: the guard's pre-execution refusal grammar (`kubectl-guard.md`
      rules 2–6a — parseability, verb whitelist, secret-value protection,
      raw-path refusal) is the single source of truth for what a recheck may
      cite, checked by the same logic the guard itself uses and never by
      executing the command (the validator stays pure, rule 15). A recheck the
      guard would refuse is a validation failure: the wiki's citations are
      bounded by the same read-only channel as the patrol itself, so memory
      can never teach or require a command outside layer 1 (ADR 0013).
    - **Metadata-line pair, not prose.** Only a `recheck` pair on the metadata
      line satisfies the Watch-list requirement; a `recheck:`-looking line in
      the entry's indented prose is unvalidated prose (rule 12) and does not
      count.
    - Each recheck error names the section, the entry id, and the reason —
      missing, duplicate, prefixed, or refused (carrying the guard's refusal
      reason) — so the repair pass (rule 18) can fix it in one round.
13. **Prose sections.** Topology, Baselines, and Open questions carry no entry
    metadata: list items there are free prose (rule 6's inline
    `(source, timestamp, confidence)` tag remains the convention, unvalidated).
    This split is the document constraint made concrete: structure exists only
    where a consumer needs it (#7 expiry, #8 stats read the metadata sections);
    everywhere else the wiki stays prose.
14. **Seed.** `WIKI_SEED` demonstrates the skeleton: frontmatter with the four
    keys — `patrol_count: 0` and `last_patrol: null` as pre-first-patrol
    placeholders, `cluster_id: local` (the configured default; the stamp makes
    the written value correct whatever the seed showed) — and the five sections
    with placeholder contents. The seed is prompt context only (rule 2): never
    written to disk, never validated.

### Validation-on-Write

The write path gains a structural gate [DONE: #5]: the wiki the model produced
is checked before it may replace memory, gets exactly one model-facing repair
attempt, and then the old wiki wins. Persistence problems degrade, never block.

15. **Validator.** `validate_wiki(content) -> list[str]` returns human-readable
    validation errors (empty list = valid). It checks exactly rules 10–12 and,
    for the `recheck` pair, rule 12a [DONE: #6] — frontmatter block present
    at the top, parseable YAML mapping (PyYAML, `pyyaml>=6`, joins the runtime
    dependency set), closed key set, types, `wiki_version == WIKI_VERSION`;
    the five canonical sections exactly once each and no other level-2
    headings; every top-level list item in Watch-list and Anomaly log a
    well-formed metadata line with valid vocabularies, date formats, and
    unique ids — and nothing else. The recheck check (rule 12a) joins this
    closed set: exactly one guard-acceptable `recheck` pair on every
    Watch-list entry, at most one on an Anomaly-log entry, judged with the
    guard's refusal grammar through shared code — never by executing the
    command [DONE: #6]. Prose, section order, heading levels other
    than 2, prose-section list items, and the values of the process-stamped
    fields are not judged [DONE: #7 — the per-entry `verified_patrol`
    clock joins the process-stamped set (rule 20): still never judged,
    always overwritten]. Each error names the element that failed (the
    missing section, the offending key, the entry missing its recheck).
    Validation is pure: no I/O, no model calls.
16. **Write path.** After the shape pass, a non-empty `updated_wiki` is validated
    before anything is written:
    - **valid** → expire (rules 20–22) → stamp (rule 17) → atomic write →
      commit → push (rules 4c–4e) [DONE: #7 — the expiry pass runs after
      the delivery decision resolves, immediately before the stamp];
    - **invalid** → one repair pass (rule 18). If the repaired report's wiki
      validates, that report is the patrol's final report (expire → stamp →
      write → commit → push). If the repair is absent, fails, or its wiki is
      still invalid → keep the old wiki, log a warning to **stderr** (the wiki
      update was rejected; the previous wiki stands), write/commit/push nothing.
    Findings printing and notification run after the validation/repair decision
    resolves — before the physical write — so exactly one report's findings and
    notifications are delivered: the final one (the repaired report on repair
    success; the first report otherwise: a wiki-format failure never costs the
    patrol's observations). An empty `updated_wiki` (no update intended) skips
    validation and repair entirely (rule 4 unchanged).
17. **Stamping.** After validation and before the atomic write, the patroller
    rewrites the three process-owned frontmatter fields — preserving
    `wiki_version` and the body byte-for-byte:
    - `patrol_count` = previous + 1, where previous is the on-disk wiki's
      frontmatter `patrol_count` if a positive integer, else 0 (absent file,
      pre-#5 legacy wiki, or unparseable frontmatter all count as 0: the first
      skeleton-compliant write stamps 1);
    - `last_patrol` = this patrol's start timestamp (the banner timestamp);
    - `cluster_id` = the configured cluster id (`PATROL_CLUSTER_ID`, default
      `local`).
    The stamp is deterministic post-processing, so the written wiki always
    carries correct counters however the model filled them. Because
    `patrol_count` advances on every accepted write, every accepted write
    changes the file: the byte-identical clause of rule 4d cannot fire for a
    skeleton wiki — an accepted write always commits in git mode (empty
    `updated_wiki` and failed reporting still create no commit). Rule 4d's
    commit-message counter and `patrol_count` are independent counters with
    different bases (git history vs frontmatter); on a legacy wiki with prior
    commits they diverge (a "patrol 6" commit message alongside
    `patrol_count: 1`) — accepted, each stays true to its own rule.
18. **Repair pass.** The repair pass is a second invocation of the shape pass —
    the same tool-free structured-output call, same `PatrolReport` binding —
    whose user message states that the previous wiki failed validation and
    carries the error list and the rejected wiki, asking for a corrected
    `PatrolReport`. At most **one** repair pass per patrol (one model-call round
    beyond the shape pass, which keeps its own schema-retry). A repaired report
    whose wiki validates replaces the first report wholesale (findings,
    notifications, wiki).
19. **Never block on the wiki.** A rejected wiki never fails the patrol: the
    old wiki stands, the patrol's findings and notifications still deliver, and
    the next patrol re-reads the old wiki and tries again. Mirrors rule 4
    (failed reporting pass) and rule 4b (unusable git): the wiki is memory, not
    the product.

### Stale-entry Expiry

The refinement loop's expire leg, made real [DONE: #7]: a watch-list entry
the model stops re-verifying is retired by the **process**, deterministically
— stale memory is worse than no memory for an agent whose wiki is hypotheses
(the OKF/AIX `stale_after` motivation, issue #7). The patroller owns the
staleness clock the way it owns `patrol_count` (rule 10's rationale: models
are unreliable counters), so the flip is never the model's job to remember
and never a validation concern — `verified_patrol` is not judged (rule 15).
ADR `decisions/0014-process-enforced-stale-entry-expiry.md` records the
decision and the alternatives (model-judged expiry, duration math, git
archaeology).

20. **Staleness clock (`verified_patrol`).** Every entry in Watch-list and
    Anomaly log carries a process-owned `verified_patrol: <int>` pair among
    the additional pairs of its metadata line (rule 12): the frontmatter
    `patrol_count` value of the accepted write that last verified the entry.
    The expiry pass (rule 22) maintains it on every accepted write; the
    model's value is never trusted — an existing `verified_patrol` pair is
    overwritten in place, an absent one appended as the line's final pair,
    and nothing else on the line is touched. For each entry of the wiki about
    to be written, with P the counter rule 17 is about to stamp (the on-disk
    wiki's `patrol_count` + 1; a legacy or unparseable frontmatter counts 0):
    - the id is absent from the old on-disk wiki's metadata sections → P (a
      new entry is verified by the patrol that wrote it);
    - `last_verified` is strictly later than the same id's date in the old
      wiki (both validated `YYYY-MM-DD`, so string order is date order) → P:
      **advancing `last_verified` is the one verification signal**;
    - otherwise the old wiki's `verified_patrol` carries unchanged;
    - the old wiki has no parseable `verified_patrol` for the id (pre-#7 wiki,
      absent or garbled pair, unparseable old entry) → P: migration grace, so
      an upgrade never mass-expires.
    The clock is keyed by entry id across both metadata sections and counts
    **accepted writes, not patrol starts** — a patrol that writes nothing
    (empty update, failed report, rejected wiki) advances no clock, exactly
    as it advances no `patrol_count`. A status flip or a section move by the
    model does not reset the clock; only verification does — re-opening an
    old entry as watching without re-verifying it expires it again. Because
    the signal is date-granular, same-day re-verification at sub-daily
    cadences does not reset the clock (the date cannot advance within a
    day): a bounded under-count, at most writes-per-day minus one between
    resets — N (rule 21) is chosen with cadence in mind (the default 5 at
    the default 6 h cadence gives ~30 h of grace; at 12 h, ~2.5 days).
21. **Expiry flip.** With N = `PATROL_WIKI_STALE_AFTER` (Configuration
    Surface; default 5, non-negative integer; `0` disables the flip — the
    rule-20 clock is still stamped, so re-enabling resumes with true
    history), a **Watch-list** entry with `status: watching` whose age
    `P − verified_patrol` has reached N is auto-expired by the process:
    - `status` flips to `expired` on the metadata line — the only metadata
      value the process rewrites besides `verified_patrol`;
    - the whole list item (metadata line + indented continuation lines)
      moves **verbatim** to the end of the Anomaly log section — history
      kept, not silently retained, not deleted (rule 9's audit trail); a
      `recheck` pair carried along stays valid (rule 12a: optional in the
      Anomaly log). The Anomaly log's existing content — including a
      placeholder line — is untouched; if the Watch-list empties, the
      section stands itemless (rule 11 allows no items; the process never
      adds prose). Pathological-but-valid shape: a wiki whose Anomaly log
      ends inside an unclosed code fence (only possible when it is the last
      section) still appends the item at the section's end, inside the
      fence — validity and history are preserved; machine-readability of
      that one item is not (rule 11 makes fenced lines prose, and the
      process never edits fence lines);
    - one indented annotation line is appended as the moved item's last
      continuation line, exactly:
      `  _auto-expired at patrol {P}: unverified for {age} patrols (stale_after {N})_`
      with age = P − verified_patrol — the flip explains itself in the
      document;
    - the flipped wiki must itself pass `validate_wiki` — the flip preserves
      everything rule 15 checked (status vocabulary, id uniqueness,
      Anomaly-log recheck optionality, section structure).
    Only Watch-list watching entries flip: `resolved`/`expired` entries,
    Anomaly-log entries (watching ones included — a deliberate model
    placement), and entries the model itself retired are the model's to
    manage; the flip is the refinement loop's backstop, not an override of
    the model's own status choices. Each flip prints one console line
    (stdout, alongside rule 4d's wiki-commit line) naming the entry id and
    its unverified-patrol count; expiry never becomes a finding or a
    notification — it is wiki housekeeping, not a cluster observation (#9's
    digest is the human surface for long-standing entries).
22. **The expiry pass.** A pure, deterministic function —
    `expire_stale_entries(content, old_wiki, patrol_count, stale_after)`,
    returning the rewritten content plus the list of `(entry id, age)` pairs
    flipped, in Watch-list item order; no I/O, no model calls, the same
    fence-aware section/entry scanning as the validator (rule 15). It runs in the write path after the
    validation/repair decision and the delivery, immediately before the
    stamp (rule 16): its inputs are the validated wiki and the old on-disk
    wiki (the clock's carry source — the same basis rule 17 uses for
    `patrol_count`), and its output is what is stamped, written, committed,
    and pushed. It runs only on accepted writes (rule 20's clock semantics);
    empty updates and rejected wikis leave wiki and clocks untouched. The
    rewrite is minimal: aside from the `verified_patrol` pairs, the flipped
    entries' status values and location, and the annotation lines, the body
    is preserved byte-for-byte — prose, section order, item markers, and the
    frontmatter (the stamp owns the frontmatter, rule 17). The fake-model
    e2e covers the wiring (rule 4f).

## Configuration Surface

- `--wiki` — path to the wiki file (default `wiki/cluster-wiki.md` beside
  `patrol.py` [DONE: #3; was `cluster-wiki.md`])
- `PATROL_CLUSTER_ID` — cluster identity used in wiki commit emails
  (`patrol@<cluster-id>`); default `local` (the in-cluster Deployment, #2, sets
  the real cluster name) [DONE: #3]
- `PATROL_WIKI_REMOTE` — if set, each wiki commit is pushed to this git remote
  (best-effort and bounded, rule 4e; local commit is the source of truth)
  [DONE: #3]
- `WIKI_VERSION` — hard-coded skeleton schema version (1), checked against the
  wiki frontmatter's `wiki_version` (rule 10); not env-configurable
  [DONE: #5]
- `PATROL_WIKI_STALE_AFTER` — accepted-write threshold for stale-entry
  expiry (rule 21): a Watch-list entry whose `last_verified` has not advanced
  for this many accepted writes is flipped to `expired` and moved to the
  Anomaly log. Non-negative integer, default 5; `0` disables the flip (the
  `verified_patrol` clock is still stamped); read once at startup, and an
  unparseable or negative value fails startup with a config error
  [DONE: #7]

## Constraints

- **The wiki stays a document, not a database.** Planned structure (skeleton schema,
  per-entry metadata) must keep free prose *inside* entries — over-structuring kills
  the natural-language hypotheses. This is a load-bearing design constraint of #5
  (made concrete by rules 10–13 and ADR 0011).
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
  someone cares about means patrol commits land in its git history [DONE: #3].

## Planned Changes

| Ticket | Summary |
|---|---|
| ~~#3~~ | ~~Git-backed wiki: wiki directory is a git repo (init at startup, file-only fallback), atomic write (temp + rename), one commit per wiki-changing patrol (`patrol N: X findings, Y warn`), optional `PATROL_WIKI_REMOTE` push — bounded, best-effort, local-first~~ |
| ~~#5~~ | Skeleton schema + validation-on-write (rules 10–19): YAML frontmatter (`wiki_version` == 1, `cluster_id`, `patrol_count`, `last_patrol` — the last three process-stamped on write), fixed section set enforced exactly-once, entry metadata line (`id`, `status` watching/resolved/expired, `first_seen`, `last_verified`, `source`, `confidence` high/medium/low) in Watch-list + Anomaly log, `validate_wiki` gate with one shape-pass repair retry else keep-old-wiki; PyYAML joins the runtime deps (ADR 0011). Impl commit also syncs `ARCHITECTURE.md`'s wiki prose ("Planned next" paragraph) and `AGENTS.md`'s test list |
| ~~#6~~ | ~~Per-entry `recheck:` commands — every Watch-list entry carries exactly one guard-acceptable `recheck:` pair (rule 12a): the `kubectl_readonly` argument string that re-verifies it, tool-argument form (no `kubectl`/`oc` prefix), judged by the guard's refusal grammar through shared code; optional in the Anomaly log; the report contract asks for it and the patrol prompt consumes it (ADR 0013). Impl commit also syncs `ARCHITECTURE.md`'s wiki prose (the planned-next line) and `AGENTS.md`'s test list~~ |
| ~~#7~~ | ~~`stale_after`: auto-expire watch-list entries not re-verified within N patrols (flip to `expired`, keep in anomaly log) — process-stamped `verified_patrol` clock + expiry flip (rules 20–22, ADR 0014); impl commit also syncs `ARCHITECTURE.md`'s wiki prose (the planned-next line), `AGENTS.md`'s test list, and `README.md`'s config table (`PATROL_WIKI_STALE_AFTER`)~~ |
| #8 | `--wiki-stats`: four wiki-health numbers — verification rate, resolution ratio, confidence spread, churn/paraphrase-drift |
