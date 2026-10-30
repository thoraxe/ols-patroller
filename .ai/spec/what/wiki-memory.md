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
   [DONE: #51 — an empty `updated_wiki` is no longer read as "no
   update intended": it is a classified reporting failure
   (`reporting-notifications.md` rule 7b) — retried once inside the
   reporting pass, then delivered findings-only with a stderr warning.
   The write path is unchanged (an empty wiki still writes nothing,
   commits nothing); what changes is that the no-op is never silent and
   never mistaken for a deliberate choice.]
   [DONE: #60 — "writes nothing" is no longer the empty-wiki path's
   last word: the wiki-repair pass and minimal-write fallback behind it
   (rules 30–32; `reporting-notifications.md` rule 7d) give the tail a
   recoverable next step; the fallback is the only path by which an
   empty-content write may land (rule 31, ADR 0022).]
   [DONE: #66 — "if the reporting pass fails, the wiki is left
   untouched" gains the same exception #60 gave the empty-wiki leg: a
   total failure (`shape_report` returned `None`) warns, notifies, and
   minimal-writes the old wiki (rule 33, ADR 0024).]

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
    [DONE: #3]. [DONE: #60 — the empty-wiki leg's supersession: the
    rule-31 minimal write is an accepted write and commits (ADR 0022);
    the stamp advances `patrol_count` on every accepted write, so the
    byte-identical case is long superseded too (rule 17).]
    [DONE: #66 — the failed-reporting-pass leg's supersession: a
    total-failure patrol runs the rule-33 minimal write and commits
    (ADR 0024); "creates no commit" now describes only the legacy
    no-frontmatter legs of rules 31/33.]
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
    [DONE: #40 — the tail becomes validate → repair once → deliver →
    expire → compact → stamp → write → commit → push, and the e2e gains
    the budget wiring: a resolved/expired Anomaly log over the keep budget
    is pruned on the written wiki with one console line per pruned entry,
    an over-budget wiki still writes with one stderr warning, and a repair
    pass carries the budget warning (rules 28–29).]
    [DONE: #66 — the e2e's failed-reporting-pass pin flips with rule 33:
    a total-failure patrol warns, sends the FAILURE notification, and
    minimal-writes (the stamped seed on a fresh deployment; the old wiki
    with the clock advanced on disk) — the wiki-repair pass skipped.]

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
   [DONE: #40 — the record spans the wiki plus its git history (rule 4a):
   the working-set budget (rule 28) prunes from the document what the
   archive retains, so the audit trail survives compaction.]

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
    The type prose on the three process-owned bullets above describes the
    **stamped** value (rule 17), not a gate requirement — the gate never
    judges those values (rule 10b).
    Presence of all four keys is validated, along with the closed key set
    and the `wiki_version` value; the **values** of `cluster_id`,
    `patrol_count`, and `last_patrol` are process-owned — the patroller
    stamps them authoritatively before writing (rule 17), so the model's
    values are never trusted (models are unreliable counters) — and never
    judged either (rule 10b).
10b. **The gate does not judge the owned values** [DONE: #62]. The
    write gate (rule 15) checks the presence of all four keys, the closed
    key set, and the `wiki_version` value alone; the **values** of
    `cluster_id`, `patrol_count`, and `last_patrol` are entirely unjudged —
    any YAML value passes. The stamp (rule 17) discards those values
    before writing, so gating on them checks a value the process never
    uses; and the model is *told* the counters are process-owned (the seed
    says so verbatim, rule 14), so a model that faithfully echoes the
    seed's `patrol_count: 0` / `last_patrol: null` placeholders is
    behaving correctly and must not be rejected (real-run evidence, #62:
    every fresh deployment's first patrol failed validation on exactly
    those two echoed fields, burned its one repair pass on them, and wrote
    nothing — a stable, not transient, failure that repeated identically
    on patrol 2). Presence stays checked: a *missing* key is a malformed
    skeleton; a present key with a placeholder value is the stamp's to
    overwrite.
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
    written to disk, never run through the write-path gate.
14a. **The seed passes the gate** [DONE: #62]. `WIKI_SEED` must itself
    satisfy `validate_wiki` (rule 15 as narrowed by rule 10b): the seed and
    the gate share one source of truth, so a fresh deployment's first patrol
    can never be rejected for echoing the seed it was handed. Pinned by
    test — `validate_wiki(WIKI_SEED)` is empty. This is an invariant of the
    two constants, not a runtime check on the patrol path: the seed is
    still never written to disk and still never validated by the write path
    (rule 14's clause stands) — with one deliberate exception [DONE:
    #60]: the minimal-write fallback (rule 31) may stamp-and-write the
    seed when the wiki file is absent, precisely because this rule makes
    the seed gate-clean and the stamp (rule 17) overwrites its
    placeholders — the written artifact is a valid first wiki whose
    clock is honest.

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
    always overwritten] [DONE: #40 — document length is equally
    unjudged: the soft budget (rule 29) never gates validation]
    [DONE: #62 — made literal for the frontmatter: the gate narrows to
    presence of all four keys, the closed key set, and the `wiki_version`
    value (rule 10b). The value checks on `cluster_id` (non-empty string),
    `patrol_count` (positive integer), and `last_patrol` (ISO-8601) are
    dropped: the stamp discards those values, and the seed's own
    placeholders must pass the gate (rule 14a). A missing key and an
    unknown key stay failures, and `wiki_version` keeps its value check.]. Each
    error names the element that failed (the
    missing section, the offending key, the entry missing its recheck).
    Validation is pure: no I/O, no model calls.
16. **Write path.** After the shape pass, a non-empty `updated_wiki` is validated
    before anything is written:
    - **valid** → expire (rules 20–22) → stamp (rule 17) → atomic write →
      commit → push (rules 4c–4e) [DONE: #7 — the expiry pass runs after
      the delivery decision resolves, immediately before the stamp]
      [DONE: #40 — the compaction pass (rule 28) joins between expiry
      and the stamp — expire → compact → stamp → write → commit → push —
      and the soft-budget warning (rule 29) fires on the final wiki];
    - **invalid** → one repair pass (rule 18). If the repaired report's wiki
      validates, that report is the patrol's final report (expire → compact
      → stamp → write → commit → push [DONE: #40 — compaction joins
      the tail]). If the repair is absent, fails, or its wiki is
      still invalid → keep the old wiki, log a warning to **stderr** (the wiki
      update was rejected; the previous wiki stands), write/commit/push nothing.
    Findings printing and notification run after the validation/repair decision
    resolves — before the physical write — so exactly one report's findings and
    notifications are delivered: the final one (the repaired report on repair
    success; the first report otherwise: a wiki-format failure never costs the
    patrol's observations). An empty `updated_wiki` (no update intended) skips
    validation and repair entirely (rule 4 unchanged) [DONE: #51 — by the
    time an empty wiki reaches this gate it is a twice-failed reporting
    attempt (already retried and warned about per
    `reporting-notifications.md` rule 7b); it still skips validation and
    repair — a classified failure writes nothing].
    [DONE: #60 — the empty-wiki leg of the write path gains a safety net
    beyond the retry: the wiki-repair pass and, behind it, the minimal-write
    fallback (rules 30–32; `reporting-notifications.md` rule 7d).]
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
    `PatrolReport` [DONE: #40 — when the rejected wiki is over the soft
    budget, its budget warning (rule 29) joins the error list as one more
    line: the model is asked to shorten while it fixes structure, though
    acceptance stays structural validity alone]. At most **one** repair pass
    per patrol (one model-call round
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
      kept, not silently retained, not deleted (rule 9's audit trail)
      [DONE: #40 — kept in the working set until the anomaly-log budget
      prunes it (rule 28); the git archive retains it either way]; a
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
    stamp (rule 16) [DONE: #40 — with compaction in the tail, "immediately"
    becomes "next": expire → compact → stamp (rule 16), and what is stamped
    is the compacted output]: its inputs are the validated wiki and the old on-disk
    wiki (the clock's carry source — the same basis rule 17 uses for
    `patrol_count`), and its output is what is stamped, written, committed,
    and pushed. It runs only on accepted writes (rule 20's clock semantics);
    empty updates and rejected wikis leave wiki and clocks untouched. The
    rewrite is minimal: aside from the `verified_patrol` pairs, the flipped
    entries' status values and location, and the annotation lines, the body
    is preserved byte-for-byte — prose, section order, item markers, and the
    frontmatter (the stamp owns the frontmatter, rule 17). The fake-model
    e2e covers the wiring (rule 4f).

### Wiki Health Stats

The wiki's four health numbers [DONE: #8] — "rules without measures drift"
(the OKF/AIX `--stats` precedent quoted in issue #8): the wiki imposes a
discipline — verify what you watch (#7's clock), resolve or expire deliberately,
grade confidence honestly — and the stats measure whether the discipline holds.
The size line extends the block with #40's measure: the working-set budgets
(rules 28–29) get their drift alarm too [DONE: #40].
All four numbers are computed by the patroller process, offline, from the wiki
document and its git history: no model calls, no cluster reads, no patrol, no
writes. ADR `decisions/0015-wiki-stats-offline-prose-churn.md` records the
shape.

23. **The stats mode.** `--wiki-stats` prints the four numbers for the wiki at
    `--wiki` and exits [DONE: #40 — plus the size line (rule 24): the
    wiki's size against the soft budget, the working-set drift measure] —
    it never runs a patrol and takes precedence over
    `--once` (the default mode). The mode is fully offline: it requires no
    `PATROL_*` environment (the startup env check is skipped), never resolves
    the cluster CLI, and never creates or initializes the wiki repo — reading
    stats must not mutate anything (`ensure_wiki_repo` is not called; the wiki
    git region is only read). A missing wiki file is a config error: a message
    to stderr naming the path, exit 1. A wiki that fails `validate_wiki` still
    gets its stats — the parser is defensive — with one stderr warning stating
    that the wiki fails skeleton validation and the error count; exit stays 0.
    Degradations never fail the run: no wiki repo (file-only mode, rule 4b),
    unusable git, or too little history print `n/a` for churn and exit 0.
    Combining `--wiki-stats` with `--loop` is a usage error (argparse `error()`,
    exit 2, message naming both flags and stating they cannot be combined):
    stats print and exit, they cannot schedule.
24. **The printed block.** `--wiki-stats` prints to stdout one block carrying
    the four numbers (exact keywords pinned; spacing and decoration free;
    percentages are floored integers) [DONE: #40 — plus the size line
    below: four numbers and size]:
    - a header naming the cluster and the patrol clock: `cluster <cluster_id>`
      and `patrol_count <P>`;
    - `verification: <X>/<Y> watch-list entries verified by patrol <P> (<Z>%)`
      — or `verification: n/a (empty watch-list)` when the Watch-list has no
      entries;
    - `resolution: watching <a> · resolved <b> · expired <c> (<N> tracked)`;
    - `confidence: high <h> · medium <m> · low <l>`;
    - `churn: <ids>` (comma-separated flagged entry ids, rule 26), `churn:
      none`, or `churn: n/a (no wiki history)`;
    - when the lopsided warning fires (rule 25): one warning line carrying
      the keyword `lopsided`, the dominant level, its share, and the
      principle — e.g. `WARNING: lopsided confidence: 4/6 high (66%) — a
      grade everyone gets is not a grade`;
    - `size: <N> chars (<P>% of budget)` — N the wiki document's character
      count (the whole file, frontmatter included), P the floored
      percentage of `WIKI_CHAR_BUDGET` [DONE: #40];
    - when the wiki is over the soft budget (rule 29): one warning line —
      the rule-29 warning text prefixed `WARNING: ` — e.g. `WARNING: wiki
      over length budget: 21400 chars vs 20000 budget — summarize and
      prune; git history is the archive` [DONE: #40].
25. **The document-stats parser (`wiki_stats`).** A pure function
    `wiki_stats(content) -> dict` — no I/O, no model calls — computing the
    three numbers that read the wiki document (the fourth, churn, reads git
    history — rule 27) [DONE: #40 — the size keys make it four document
    numbers; churn stays the history reader]. The parser is defensive: it reads skeleton-valid
    wikis exactly and hand-edited ones as far as they parse — a top-level list
    item of a metadata section is an **entry** iff its line parses into
    ` · `-separated `key: value` pairs carrying an `id`; anything else is
    invisible to every stat. Each stat uses the field it needs: an entry
    counts toward a stat iff that field's value is in the stat's vocabulary
    (garbage or missing values are skipped for that stat only). The dict has
    exactly ten keys [DONE: #40 — was eight; `char_count` and
    `over_budget` join] — `patrol_count` (P, the frontmatter `patrol_count`
    under `frontmatter_patrol_count` semantics: unparseable or absent counts
    0), `cluster_id` (the frontmatter string, `""` when absent), `watch_total`,
    `watch_verified`, `verification_rate`, `status_counts`,
    `confidence_counts`, `lopsided`, `char_count`, `over_budget`:
    - **verification rate** — over the **Watch-list** section's entries:
      `watch_total` (count), `watch_verified` (entries whose `verified_patrol`
      pair parses as an integer **equal to P** — missing, garbled, or unequal
      counts as not verified; Anomaly-log entries are never in the
      denominator), `verification_rate` = `watch_verified / watch_total` as a
      float, `None` iff `watch_total == 0`. "Verified this patrol" means
      verified by the most recent accepted write — rule 20's clock, whose unit
      is accepted writes, so the stat is exactly as trustworthy as the
      process-stamped clock (ADR 0014's known gap: a model advancing dates
      without verifying inflates it; the stat surfaces the clock, it cannot
      audit it).
    - **resolution ratio** — `status_counts`: the counts of `watching`,
      `resolved`, and `expired` over the entries of **both** metadata sections
      (expired entries are read straight from the Anomaly log, ADR 0014),
      bucketed by `status` value regardless of section; the three keys are
      always present (zeros when absent).
    - **confidence spread** — `confidence_counts`: the counts of `high`,
      `medium`, and `low` over the entries of both metadata sections (keys
      always present); plus `lopsided`: the dominant level's name when the
      warning fires, else `None`. The warning fires iff at least
      `WIKI_STATS_LOPSIDED_MIN` (5) entries carry a confidence AND one level's
      count is ≥ `WIKI_STATS_LOPSIDED_SHARE` (2/3) of the graded total — "a
      grade everyone gets is not a grade", for ANY dominant level, not only
      `high` (at ≥ 2/3 the dominant level is necessarily unique). Below the
      minimum the spread is reported without judgment — too little data to
      call lopsided.
    - **size** [DONE: #40] — `char_count`: the wiki document's character
      count (the whole file, frontmatter included — always computable,
      whatever the document's validity, like the rest of the defensive
      parser); `over_budget`: True iff `char_count` exceeds
      `WIKI_CHAR_BUDGET` (rule 29). The stat behind rule 24's `size:` line
      and warning — the working-set budgets' drift measure.
26. **The churn parser (`wiki_churn`).** A pure function
    `wiki_churn(revisions) -> list[str] | None` over a series of wiki contents
    oldest→newest — the photocopy-of-a-photocopy detector: entries the model
    rewords every patrol with no status change (rewording, not learning).
    Per revision (fence-aware scanning, rule 11's discipline: fenced lines are
    prose), every entry of both metadata sections maps `id → (status, prose)`
    where **prose** is the entry's indented continuation block — the same
    item extent the expiry pass moves verbatim (rule 21) — compared
    byte-exact; a duplicate id in one revision resolves to its first
    occurrence in document order. An id is **flagged** iff it is present in
    every revision of the series AND every adjacent revision-pair is a
    **churn pair** for it: prose differs AND the parsed `status` values are
    equal (absent or garbled status reads as absent on both sides). The
    comparison is prose-only by design: process stamps (`verified_patrol`,
    frontmatter) and meaningful metadata advances (`last_verified`, `recheck`
    rewrites, section moves) never count as rewording — an entry re-verified
    with stable prose is the healthy case, not drift. A status change in any
    pair breaks the flag for that entry — resolution, expiry, and #7's
    auto-expiry flip (status change + annotation + move) are learning events,
    never churn. Returns the flagged ids in the newest revision's order —
    Watch-list entries in document order, then Anomaly-log entries in document
    order (the live loop first) — or `None` when the series has fewer than two
    revisions (nothing measurable). Entries born mid-window are never flagged:
    drift detection lags by design — better under-reported than flapping.
27. **The revision series (`wiki_history`) and the window.** The churn series
    comes from the wiki repo: `wiki_history(wiki_path, window) -> list[str]`
    returns the wiki file's content at the last `window` commits that changed
    it, oldest→newest, read with the same git discipline as the patrol's own
    wiki git (per-command config, `safe.directory`, never a remote call). No
    repo, no commits, or any git failure yields an empty list — never a
    partial window and never an error (degradation, rule 4b's philosophy).
    `wiki_churn` is then fed `wiki_history(wiki_path, WIKI_CHURN_WINDOW)`;
    fewer than two revisions (including the empty case) print churn `n/a`.
    One commit ≈ one accepted write (rules 4d and 17: every accepted write
    changes the file and commits), so the window counts the same unit as the
    `verified_patrol` clock. `WIKI_CHURN_WINDOW` is 5 — the same span as the
    default `PATROL_WIKI_STALE_AFTER` grace: an entry that survives the
    expiry window because it keeps being re-verified, while rewriting its
    prose every single time, is exactly the photocopy effect. The stats mode
    is wired in `main()` right after argparse, before the env check, kubectl
    resolution, and repo initialization (rule 23); the fake-model e2e is not
    extended — the mode has no model in it.

### Working-Set Budget

The wiki is a **working set**, not a log [DONE: #40]: growth taxes every
patrol — the wiki is injected whole into the prompt (rule 3) and re-emitted
whole by the shape pass under its completion-token bound
(`REPORT_MAX_TOKENS` 64000, `reporting-notifications.md` rule 8b
[DONE: #63]; before it, the flat 16000 was the de-facto ceiling) — an
over-long wiki fails the reporting pass rather than being trimmed — while
the wiki repo already retains every accepted write (rule 4d). The stance:
**git history is the archive; the wiki is the working set** (ADR
`decisions/0017-working-set-wiki-not-log.md`). The Anomaly log is append-only
by nature — #7's expiry flips land there, resolutions accumulate — so it
gets the mechanical bound, enforced by the process the way expiry is (ADR
0014's rationale: models are unreliable at self-limiting bookkeeping);
everything else (prose sections bloat too) gets a soft budget that warns
and teaches but never gates — a hard failure would burn repair passes on
formatting instead of hypotheses, and the wiki stays a document (the
load-bearing constraint of #5).

28. **Anomaly-log keep budget.** On every accepted write the Anomaly log is
    bounded: the patroller keeps every `watching` Anomaly-log entry (live
    placements, the model's to manage) plus the **last** `WIKI_ANOMALY_KEEP`
    (20) `resolved`/`expired` entries in document order — the section is
    append-ordered (rule 21's flips append at its end; the model's rewrite
    carries whatever order it wrote), so "last" means most recent, and when
    the model reorders entries it effectively chooses which survive. Closed
    entries earlier in the section are **pruned**: the whole list item —
    metadata line plus indented continuation lines, the same item extent
    the expiry pass moves verbatim (rule 21) — is removed from the written
    wiki. Pruning is deletion from the working set, never from history: one
    commit per accepted write (rule 4d) means an entry accepted-written in
    an earlier patrol stays recoverable from the wiki repo (rule 9) — an
    entry introduced and pruned within the same write was never accepted
    memory, and the repo owes it nothing. (File-only mode, rule 4b, has no
    archive — there the wiki file is all there is, #3's degradation.) The
    pass:
    - is pure and deterministic — `compact_anomaly_log(content, keep)`
      returns `(rewritten content, [pruned entry ids])`, ids in document
      order; no I/O, no model calls; the same fence-aware section/entry
      scanning as the validator (rule 15) — fenced list-looking lines are
      prose, never pruned, never counted;
    - never touches the Watch-list (the refinement loop's home — #7's
      expiry already retires it; a resolved/expired entry sitting in the
      Watch-list is the model's placement to reconsider, not the process's
      to delete, mirroring rule 21's flip scope), never touches the prose
      sections or the frontmatter (the stamp owns it, rule 17), and never
      adds or removes prose — when nothing is pruned the content is
      returned unchanged;
    - runs in the write path after the expiry pass and before the stamp
      (rule 16), so entries flipped this patrol land at the section's end
      (rule 21) and are among the newest — they survive the cut; only a
      single patrol flipping more than `WIKI_ANOMALY_KEEP` entries
      (pathological: 20 stale watch-list entries at once) prunes a
      just-flipped entry — bounded and deterministic either way;
    - keeps the written wiki valid: removing whole entries cannot break the
      skeleton (ids stay unique, sections stay intact) — the compacted
      wiki must itself pass `validate_wiki`, pinned like rule 21's flip;
    - treats `keep = 0` as prune-everything-closed (aggressive but legal —
      `watching` entries still stay);
    - logs one console line per pruned entry (stdout, alongside rule 4d's
      wiki-commit line and rule 21's flip lines) naming the entry id, the
      keep budget, and that git history retains it — e.g.
      `[patrol] wiki: entry 'A-1' pruned from the Anomaly log (beyond the
      keep budget 20; git history retains it)` — the retention phrase states
      the git-backed case; file-only mode (rule 4b) has no archive and said
      so at startup. Pruning is wiki
      housekeeping, never a finding or a notification (rule 21's
      discipline). The first accepted write after this rule ships may
      prune a burst of legacy entries at once — one line each, once,
      honest.
29. **Soft length budget.** A whole-wiki character budget `WIKI_CHAR_BUDGET`
    (20,000 — roughly a third of the shape pass's completion-token output
    ceiling as it stood at #40's writing; the ceiling has since been raised
    to `REPORT_MAX_TOKENS` 64,000 for the shape pass
    (`reporting-notifications.md` rule 8b) [DONE: #63], so the budget is
    now set by drift-alarm judgment, not output-ceiling arithmetic — leaving
    room for findings and notifications in the same structured output
    regardless; the unit is characters, the wiki being UTF-8 text).
    **Soft**: `validate_wiki` (rule 15) never judges length, and a
    structurally valid over-budget wiki is accepted, written, committed,
    and pushed exactly like any other — the anomaly-log budget (rule 28)
    is the mechanical bound; this one is the drift alarm (a hard gate
    would burn repair passes on formatting instead of hypotheses, and
    push against #5's document constraint). The pure function
    `wiki_budget_warnings(content) -> list[str]` returns the overshoot
    warning — empty within budget, otherwise exactly one line naming the
    wiki's character count, the budget, and the summarize-and-prune
    instruction, e.g. `wiki over length budget: 21400 chars vs 20000
    budget — summarize and prune; git history is the archive`. The
    warning surfaces in three places:
    - **write path:** when the final wiki that is written (post expire →
      compact → stamp) exceeds the budget, one warning line to **stderr**
      (rule 16's channel for wiki problems); the write, the commit, and
      the push all proceed;
    - **repair pass:** when a repair pass runs (rule 18), the rejected
      wiki's budget warning joins the error list carried to the repair
      pass — the model is asked to shorten while it fixes structure — but
      the repaired report is accepted on structural validity alone;
      length never decides acceptance (an over-budget repair still
      writes, with the write-path warning);
    - **stats mode:** rule 24's `size:` line and over-budget warning make
      the drift measurable offline (rule 23's rationale: rules without
      measures drift).
29a. **Model-facing size note** [PLANNED: #67]. Rule 29's warning is
    process-side: the model never sees its own size, so it cannot act on
    it (the #67 evidence: consecutive patrols logged the over-budget
    warning while the wiki kept growing). The feedback loop closes inside
    the shape pass. A pure function
    `wiki_size_note(content, *, budget=None) -> str` renders exactly one
    deterministic line — empty string for empty content — with `budget`
    defaulting to `WIKI_CHAR_BUDGET`, read at call time (the rule-28/29
    convention; tests may pass a smaller budget). It states the content's
    character count, the budget, and the floored percentage of budget
    (`int(100 * count / budget)`), plus:
    - **within budget:** the headroom in characters and the
      do-not-grow-without-pruning instruction — e.g.
      `Current wiki size: 18500 characters (92% of the 20000-character
      soft budget) — 1500 characters of headroom. Do not grow it without
      pruning something else.` The boundary is `count == budget`: it is
      **within budget** (0 characters of headroom), matching rule 29's
      strict `count > budget` overshoot test — the `OVER budget` branch
      fires only when the count strictly exceeds the budget.
    - **over budget:** the excess in characters (`count - budget`) and a
      named prune target — e.g. `Current wiki size: 27296 characters
      (136% of the 20000-character soft budget) — 7296 characters OVER
      budget. Prune at least 7296 characters: rewrite existing prose
      tighter and drop what no longer earns its place.`
    The note is appended to the **final user message of every attempt's
    prompt**, a blank line between: after `REPORT_CONTRACT` on the first
    attempt, and after the corrective message on a retry — so the note is
    the last text the model reads on every attempt, and it appears exactly
    once per prompt. It is carried in every report-pass invocation — the
    report pass and the rule-18 validation-repair pass:
    - the **report pass** reports the old on-disk wiki's size — the seed
      on a file-absent deployment (rule 14), i.e. the content rule 3
      injects into exploration and the next patrol would read;
    - the **validation-repair pass** reports the **rejected wiki's** size
      — the content the model is being asked to fix. The rejected wiki's
      rule-29 budget warning also rides the repair error list (rule 18)
      **separately**: both surfaces appear deliberately and must not be
      de-duplicated — the error-list warning is part of the fix
      instructions, the size note is the measurement;
    - the **rule-7d wiki-repair pass carries no size note** on any of its
      invocations, including its internal retry: its ask is a minimal
      merge that preserves the old wiki, and a prune instruction would
      contradict it.
    `REPORT_CONTRACT`'s working-set item is mode-agnostic and shared by
    every invocation, so its wording is hedged ("a size line **may** be
    appended below") — the wiki-repair mode and direct
    `shape_report` callers with no `current_wiki` see no line.
    The note is instruction only, never a gate: acceptance stays
    structural (rule 29's softness), and no write-path behavior changes.
    `REPORT_CONTRACT`'s working-set item names the consequence chain the
    note makes actionable (`reporting-notifications.md` rule 5 amendment,
    the teach-harder half of #67). See
    `decisions/0025-wiki-size-feedback-not-prose-compaction.md` for the
    rejected process-side alternative.

### Still-Empty Safety Net

The recoverable tail of the #51 still-empty outcome [DONE: #60]. #51
made the empty `updated_wiki` visible — one named retry, one stderr
warning, findings preserved — and deliberately terminal: correct for a
single patrol, not survivable when it recurs. Real-run evidence (#60's
soak): 2 of 3 patrols in one process wrote nothing, the live wiki had
not advanced in 11 days (8 patrols, zero writes), and each patrol spent
500k+ input tokens delivering no memory. #63 removed the dominant cause
(the reporting call exhausting its completion budget on reasoning), but
whatever still fails needs a path back to writing — the **wiki-repair
pass** (rule 30) and, behind it, the **minimal-write fallback** (rule 31,
ADR `decisions/0022-minimal-write-advances-the-clock.md`). Bounded like
every other model-facing retry in the write path: at most one
wiki-repair invocation per patrol, then one deterministic, model-free
write.

30. **Wiki-repair pass.** When the reporting pass ends in the rule-7b
    still-empty outcome (a parsed report whose retry also returned an
    empty `updated_wiki` — the stderr-warning path), the patrol tail runs
    one **wiki-repair pass** before giving up on memory: the shape pass
    invoked a third way (`reporting-notifications.md` rule 7d — the same
    tool-free structured `PatrolReport` binding, the same reporting
    bounds of rules 8a/8b, the same internal parse/empty retry of rules
    7/7b) whose user message states that the reporting pass could not
    emit the wiki and carries the old on-disk wiki verbatim (the seed,
    rule 14, when the file is absent) plus the observed report's findings
    and notifications, asking for the old wiki rewritten **minimally** —
    the findings merged in, everything else preserved. The wiki-repair
    call never saw the cluster: its findings and notifications are
    **never delivered** — the patrol delivers the observed report's
    findings and notifications, exactly as rule 16's keep-old-wiki leg
    does; only the wiki is taken from the repair. The repaired wiki is
    gated by `validate_wiki` like any model wiki, with **no further
    repair** (rule 18's repair budget belongs to the validation leg;
    this pass gets exactly one invocation per patrol). A valid non-empty
    repaired wiki flows through the standard accepted-write tail
    (expire → compact → stamp → write → commit → push, rule 16); an
    absent, empty, or invalid one falls to the minimal-write fallback
    (rule 31). Scope boundary: only the reporting-pass still-empty
    outcome triggers the net — the rule-18 repair pass's own wiki-less
    leg (a repair that stays empty after its retry) keeps its
    keep-old-wiki outcome unchanged: that report's first wiki was
    non-empty and failed structurally, a different failure family, and
    its report has already consumed two model-retry rounds.
31. **Minimal-write fallback.** When the wiki-repair pass also fails, the
    patrol writes the old wiki with the process-owned frontmatter
    advanced: the old on-disk wiki is run through the
    accepted-write tail — expire → compact → stamp → write → commit →
    push — exactly as if the model had returned it verbatim. When the
    wiki file is absent (a brand-new deployment whose first patrol
    double-fails), the seed takes that ride: it is stamped-and-written —
    the one case where the seed reaches disk (rule 14a's deliberate
    exception; the seed is gate-clean and the stamp overwrites its
    placeholders, so the written artifact is a valid first wiki with an
    honest clock). Memory
    content stays stale, but the clock stops lying: `patrol_count`
    advances, `last_patrol` refreshes, and the staleness clock (rule 20)
    keeps counting, so entries the model can no longer verify eventually
    expire (rule 21) instead of freezing forever — stale memory is worse
    than no memory, and frozen-stale is the worst of both. This is an
    **accepted write** by explicit decision (ADR 0022): rule 17's "every
    accepted write advances the count" gains an empty-content leg. The
    expiry pass runs with the old wiki on both sides (the content being
    written *is* the old wiki): no `last_verified` advances, so clocks
    carry — except the first fallback on a pre-#7 wiki, which takes
    rule 20's migration grace once, exactly like any upgrade. The
    fallback requires a parseable frontmatter block in the old wiki (the
    stamp must have something to rewrite): a legacy wiki without one
    keeps rule 4's keep-old-wiki outcome — nothing written, nothing
    committed, the rule-7b warning alone. The fallback never re-validates
    its content: it is prior accepted memory, the seed (gate-clean by
    rule 14a — the process's own constants, not model output), or a
    legacy wiki (which the frontmatter requirement above already
    excludes from writing).
32. **Visibility and bounds.** The rule-7b stderr warning still fires
    first (the reporting failure is visible whatever happens next). The
    wiki-repair pass and fallback together cost at most one model call
    beyond the reporting pass (the wiki-repair invocation, which keeps
    the shape pass's own internal retry — rules 7/7b) and then one
    deterministic, model-free write. The wiki-repair pass logs its
    engagement to stderr alongside the attempt-failure lines; the
    fallback logs one console line (stdout, alongside rule 4d's
    wiki-commit line) naming the outcome — e.g. `[patrol] wiki: minimal
    write — memory content unchanged, patrol clock advanced` — wiki
    housekeeping, never a finding or a notification (rule 21's
    discipline). The fake-model e2e covers the wiring (rule 4f): the
    wiki-repair prompt carries the old wiki and the observed findings,
    the observed report's findings deliver (the repair's never do), the
    repaired wiki writes through the standard tail, and the double
    failure writes the old wiki with the clock advanced (writes nothing
    at all on a legacy no-frontmatter wiki, and writes the stamped seed
    on a file-absent fresh deployment — rule 31).
33. **Total-failure leg** [DONE: #66]. When the reporting pass ends in
    rule 7's total failure (`shape_report` returned `None` — no attempt
    parsed, a different failure family from rule 30's still-empty outcome:
    there is no observed report at all), the patrol tail runs the rule-31
    minimal-write fallback directly. The wiki-repair pass (rule 30) is
    skipped on this leg — it exists to merge *observed findings* into the
    old wiki, and a total failure has none to merge. Everything else is
    rule 31 verbatim: the old on-disk wiki runs through the accepted-write
    tail (expire → compact → stamp → write → commit → push), the seed
    takes that ride on a file-absent fresh deployment (rule 14a's
    deliberate exception), a legacy no-frontmatter wiki writes nothing,
    and the fallback logs its one console line. Ordering: the rule-7
    warning fires first (stdout, the line's existing channel — unchanged
    by #66), then the failure notification
    (`reporting-notifications.md` rule 7's #66 amendment — one `notify`
    call, `FAILURE`), then the write; no findings block prints (there is
    no report). ADR 0024 records the decision — the same
    frozen-wiki threat ADR 0022 answered for the still-empty outcome,
    answered the same way: honest staleness, never frozen staleness, and
    a blind patrol that tells the human it is blind.

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
- `--wiki-stats` — print the wiki's four health numbers (rules 23–27) and
  exit: verification rate, resolution ratio, confidence spread, churn
  [DONE: #40 — plus the size line (rule 24): the wiki's character count
  and over-budget warning]. Offline
  — no `PATROL_*` env, no cluster, no repo initialization; a missing wiki file
  is an error, everything else degrades to `n/a` [DONE: #8]
- `WIKI_CHURN_WINDOW` — hard-coded constant (not env-configurable), the
  number of wiki revisions (one per commit) the churn stat examines: 5
  [DONE: #8]
- `WIKI_STATS_LOPSIDED_MIN` / `WIKI_STATS_LOPSIDED_SHARE` — hard-coded
  constants (not env-configurable) for the confidence-lopsided warning (rule
  25): 5 graded entries minimum, dominant level ≥ 2/3 of the graded total
  [DONE: #8]
- `WIKI_ANOMALY_KEEP` — hard-coded constant (not env-configurable), the
  Anomaly log's keep budget (rule 28): the last 20 resolved/expired entries
  survive every accepted write; earlier ones are pruned by the patroller,
  recoverable from git history. Passed to the compaction pass by the write
  path (read at call time, the `STALE_AFTER` pattern) [DONE: #40]
- `WIKI_CHAR_BUDGET` — hard-coded constant (not env-configurable), the soft
  whole-wiki character budget (rule 29): 20,000 characters (the whole file,
  frontmatter included). Overshoot warns — write path (stderr),
  repair-pass error list, `--wiki-stats` size line — and never gates
  validation; read at call time by `wiki_budget_warnings` (and by
  `wiki_stats`'s `over_budget`) [DONE: #40]

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
- **A working set, not a log.** The wiki is bounded (#40): the Anomaly log
  keeps the last `WIKI_ANOMALY_KEEP` resolved/expired entries — the process
  prunes older ones, recoverable from git history — and a soft character
  budget warns on overshoot without ever gating validation. Growth taxes
  every patrol (the wiki is injected whole, rule 3); the archive is the
  wiki repo, not the document [DONE: #40].

## Planned Changes

| Ticket | Summary |
|---|---|
| #67 | Model-facing wiki size feedback (rule 29a; the contract amendment is `reporting-notifications.md` rule 5): every report-pass invocation carries a deterministic `wiki_size_note` line — the wiki-in-play's character count, the budget, the floored percentage, and the excess (over budget, with a named prune target) or headroom (under budget) — appended after `REPORT_CONTRACT`; the report pass reports the on-disk wiki, the validation-repair pass the rejected wiki, and the wiki-repair pass carries none. The contract names the consequence chain (over-budget wiki → slower reporting pass → gateway ceiling → the next patrol re-reading prose) and requires the returned wiki to be smaller when over budget unless new facts require the space. ADR 0025 rejects process-side prose compaction. Impl commit also syncs `AGENTS.md`'s test list |
| ~~#66~~ | ~~Total-failure leg of the minimal-write net (rule 33; ADR 0024): a `report is None` patrol skips the wiki-repair pass (no findings to merge) and runs the rule-31 fallback directly — warning, FAILURE notification, then the accepted-write tail on the old wiki. The reporting-pass input compaction and failure-visibility halves of #66 live in `reporting-notifications.md` rules 7e/7f. Impl commit also syncs
  `AGENTS.md`'s test list~~ |
| ~~#60~~ | ~~Still-empty safety net (rules 30–32, ADR 0022; the call shape is `reporting-notifications.md` rule 7d): after the #51 still-empty outcome, one wiki-repair pass — the shape pass carrying the old wiki verbatim plus the observed findings, asking for a minimal merge; its findings are never delivered — and, when that fails too, a minimal-write fallback that runs the accepted-write tail on the old wiki (clock advanced, body preserved; no-op on a legacy no-frontmatter wiki). Impl commit also syncs `AGENTS.md`'s test list and `ARCHITECTURE.md`'s wiki prose if it mentions the terminal still-empty outcome~~
| ~~#62~~ | ~~The gate does not judge the owned values (rules 10b + 14a + the rule-15 narrowing): frontmatter validation drops the value checks on `cluster_id`/`patrol_count`/`last_patrol` (presence, closed set, and `wiki_version` value stay), and `WIKI_SEED` must itself pass `validate_wiki` — the seed/gate single source of truth, pinned by test. Impl commit also syncs `AGENTS.md`'s test list~~
| ~~#51~~ | ~~Empty-`updated_wiki` classification (rules 4 + 16 amendments): empty is a failed reporting attempt, not a no-update signal — retried and warned inside the reporting pass (`reporting-notifications.md` rule 7b); the write gate itself is unchanged (empty still writes nothing)~~ |
| ~~#3~~ | ~~Git-backed wiki: wiki directory is a git repo (init at startup, file-only fallback), atomic write (temp + rename), one commit per wiki-changing patrol (`patrol N: X findings, Y warn`), optional `PATROL_WIKI_REMOTE` push — bounded, best-effort, local-first~~ |
| ~~#5~~ | Skeleton schema + validation-on-write (rules 10–19): YAML frontmatter (`wiki_version` == 1, `cluster_id`, `patrol_count`, `last_patrol` — the last three process-stamped on write), fixed section set enforced exactly-once, entry metadata line (`id`, `status` watching/resolved/expired, `first_seen`, `last_verified`, `source`, `confidence` high/medium/low) in Watch-list + Anomaly log, `validate_wiki` gate with one shape-pass repair retry else keep-old-wiki; PyYAML joins the runtime deps (ADR 0011). Impl commit also syncs `ARCHITECTURE.md`'s wiki prose ("Planned next" paragraph) and `AGENTS.md`'s test list |
| ~~#6~~ | ~~Per-entry `recheck:` commands — every Watch-list entry carries exactly one guard-acceptable `recheck:` pair (rule 12a): the `kubectl_readonly` argument string that re-verifies it, tool-argument form (no `kubectl`/`oc` prefix), judged by the guard's refusal grammar through shared code; optional in the Anomaly log; the report contract asks for it and the patrol prompt consumes it (ADR 0013). Impl commit also syncs `ARCHITECTURE.md`'s wiki prose (the planned-next line) and `AGENTS.md`'s test list~~ |
| ~~#7~~ | ~~`stale_after`: auto-expire watch-list entries not re-verified within N patrols (flip to `expired`, keep in anomaly log) — process-stamped `verified_patrol` clock + expiry flip (rules 20–22, ADR 0014); impl commit also syncs `ARCHITECTURE.md`'s wiki prose (the planned-next line), `AGENTS.md`'s test list, and `README.md`'s config table (`PATROL_WIKI_STALE_AFTER`)~~ |
| ~~#8~~ | ~~`--wiki-stats`: the four wiki-health numbers (rules 23–27, ADR 0015) — verification rate (`verified_patrol` == frontmatter `patrol_count` over the Watch-list), resolution ratio (status counts over both metadata sections), confidence spread with the lopsided warning (≥ 2/3 of ≥ 5 graded entries), churn (prose-only comparison over the last `WIKI_CHURN_WINDOW` = 5 commits, flagged when every pair changed with no status change); a fully offline mode (no `PATROL_*` env, no cluster, no repo init) that degrades to `n/a`, never fails. Impl commit also syncs `README.md` (usage + config table), `AGENTS.md`'s test list, and `ARCHITECTURE.md`'s wiki prose (the planned-next line)~~ |
| ~~#40~~ | ~~Wiki length budget (rules 28–29, ADR 0017): the Anomaly log becomes a bounded working set — the patroller keeps every watching entry plus the last `WIKI_ANOMALY_KEEP` (20) resolved/expired entries in document order, pruning older whole items on every accepted write (pure `compact_anomaly_log`, fence-aware, one console line per prune, recoverable from git history); a soft whole-wiki character budget `WIKI_CHAR_BUDGET` (20,000) that never gates validation — it warns on the write path (stderr), rides the repair pass's error list, and surfaces in `--wiki-stats` (the `size:` line + over-budget warning; `wiki_stats` gains `char_count`/`over_budget`, ten keys); the report contract carries the summarize-and-prune expectations. Impl commit also syncs `ARCHITECTURE.md`'s wiki prose (the planned-next line), `AGENTS.md`'s test list, the `--wiki-stats` argparse help (the size number), and `README.md`'s `--wiki-stats` prose (usage + config table: the four numbers gain the size line)~~ |
