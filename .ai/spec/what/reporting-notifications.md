# Reporting & Notifications

The output contract of every patrol: the typed `PatrolReport`, the severity
vocabulary, and how notifications reach a human. Implementation (the shape pass) is
in `how/reporting-pipeline.md`.

## Behavioral Rules

### Report Schema

1. Every completed patrol yields a `PatrolReport` with exactly three fields:
   `findings` (list of `Finding`), `notifications` (list of strings), and
   `updated_wiki` (full new markdown content of the wiki).
2. A `Finding` is: `severity` + `summary` (short, human-readable) + `evidence`
   (short citation of what was actually observed; defaults to empty).
3. Severity vocabulary: **info** = note / baseline / expected state; **warn** =
   drift / degradation / something suspicious; **crit** = broken, needs a human now.
4. `notifications` contains **only** the warn/crit items a human should actually see
   (don't cry wolf), as human-readable messages.
5. `updated_wiki` is the FULL new wiki content in the skeleton format
   (`wiki-memory.md` rules 10–13): frontmatter with the four keys, the five
   canonical sections, entry metadata lines in Watch-list and Anomaly log, a
   guard-acceptable `recheck` command on every Watch-list entry (rule 12a)
   [DONE: #6], free prose inside entries, stale entries expired. The report
   contract (`REPORT_CONTRACT`) instructs the model in this format — the
   frontmatter keys, the section set, and the entry metadata line [DONE: #5],
   and the recheck requirement with its tool-argument form (no leading
   `kubectl`/`oc`; validated against the guard's refusal grammar)
   [DONE: #6]. The recheck guidance must also state exactly: `A recheck must
   never read a Secret — not even names or metadata.` [DONE: #33].
   [DONE: #7 — the contract also carries the staleness
   clock's model-facing half: entry metadata lines may carry a
   `verified_patrol` pair that the patroller re-stamps after validation
   (copy or drop it freely, never compute it); `last_verified` advances only
   when the entry was actually re-verified this patrol (it is the signal the
   patroller's clock reads); and a watching Watch-list entry whose
   `last_verified` stops advancing is auto-expired by the patroller after
   `PATROL_WIKI_STALE_AFTER` accepted writes — re-verify what is worth
   keeping, resolve or expire the rest deliberately
   (`wiki-memory.md` rules 20–22).]
   [DONE: #40 — the contract also carries the working-set expectations:
   the wiki is a working set and git history is the archive, so summarize
   and prune rather than accumulate — rewrite entries tighter instead of
   appending new prose on top of old; keep the whole wiki within the soft
   budget (`WIKI_CHAR_BUDGET` characters, stated in the contract); the
   Anomaly log keeps only the last `WIKI_ANOMALY_KEEP` resolved/expired
   entries (the patroller prunes older ones) — never hoard closed history
   there (`wiki-memory.md` rules 28–29). The contract's existing “expire
   stale entries rather than deleting history” sentence is reworded
   accordingly: prose-section entries are still expired, never deleted, by
   the model — pruning closed Anomaly-log history is the patroller's job.]
   [DONE: #51 — the contract also requires the wiki on every report.
   The contract sentence, verbatim (the executable pins live in
   `test_empty_report.py`):
   "updated_wiki is required on every report: return the FULL wiki
   content even when nothing needs changing — copy unchanged sections
   verbatim except that patrol-history prose is never carried forward.
   An empty updated_wiki is never a valid answer: a patrol with nothing
   to change returns the wiki unchanged."
   [DONE: #85 — the sentence is narrowed by eight words:
   `except that patrol-history prose is never carried forward`
   (`wiki-memory.md` rule 29b). Without it the contract contradicts
   itself on a no-change patrol over a history-bearing wiki — #51's
   "copy unchanged sections verbatim" versus #85's "never write
   patrol-history prose" — and the contradiction reaches the wiki-repair
   pass too, whose user message carries this contract and now also asks
   for history prose to be dropped (rule 7d). Content completeness is
   #51's requirement and is untouched (the whole wiki still comes back);
   patrol-history prose is not content the wiki may keep. Every
   model-facing surface that repeats the ask carries the narrowed
   wording, not the old one: this clause, the empty-wiki corrective
   (rule 7b), and the wiki-repair ask (rule 7d).]
   The schema field keeps its lenient default (an omitted field parses
   to `""`), so a wiki-omitting response still parses and its findings
   survive: the process, not the parser, enforces the requirement
   (rule 7b).]
   [DONE: #67 — the working-set instruction also names the
   consequence chain and the prune target, and tells the model its own
   size. The contract text added, verbatim (executable pins live in
   `test_wiki_budget.py`):
   "A size line may be appended below: the current wiki's character count
   and the soft budget. An over-budget wiki slows this reporting pass,
   risks the model gateway's request ceiling, and makes the next patrol
   re-read prose that should have been pruned. When the size line says
   the wiki is over budget, the updated_wiki you return must be smaller
   than the current wiki — prune at least the stated excess — unless this
   patrol observed new facts that genuinely require the space; then prune
   elsewhere to pay for them. Use the line: retain what is load-bearing,
   drop what is not."]
   [DONE: #85 — the working-set item also forbids
   patrol-history prose (`wiki-memory.md` rule 29b). Three sentences
   join item 5, verbatim (executable pins live in `test_wiki_budget.py`):
   - `The wiki records current state only — never write patrol-history prose: no per-patrol headline log, no History: list, no "as of patrol #N" chronology. Git history is the record of what changed when.`
   - `Keep the opening prose to a few lines: what the wiki is and the dominant standing severity.`
   - `A ### status block after the Anomaly log is legal; a sixth ## section is not.`
   It also narrows the #51 verbatim-copy sentence in this same rule (above):
   the narrowed wording (`except that patrol-history prose is never carried
   forward`) is what the contract carries, so a no-change patrol returns the
   whole wiki but never reproduces its history prose.
   The first sentence is the ban (the scope and rationale are rule 29b's:
   per-entry state stays, document-level chronology goes), the second keeps
   the intro bounded, and the third settles the structural question — a
   level-3 status block is legal prose (rule 11's free section order),
   a sixth level-2 section is a validation failure. Nothing mechanical
   changes: the working-set/size-note machinery of rules 29/29a is
   unchanged, `validate_wiki` still judges no prose (rule 15), and
   `wiki_version` stays 1 — the rule is taught and measured, never gated
   (`decisions/0032-no-patrol-history-prose.md`).]
   [DONE: #97 — the contract also asks for **concision**, because a
   verbose answer is what spends the model gateway's wall clock (rule 7g's
   ceiling). Two pinned sentences join the contract, verbatim (executable
   pins live in `test_shape_input.py`):
   `Be concise: one line per finding with a short evidence citation, and no optional prose padding around the report — the wiki keeps its own section and entry prose. Brevity governs how you say it, never what you include: every finding, notification, and the FULL updated_wiki are still required.`
   It rides in `REPORT_CONTRACT`, so every attempt of every mode carries it
   (the rule-7g retry re-states the contract), and it is an ask, never a
   gate: rule 5's completeness requirements, rule 7b's empty-wiki
   classification, and the wiki's own content rules are unchanged. The
   wiki clause is not decoration: "no optional prose" is scoped to padding
   *around* the report, so the model must never read the ask as a licence to
   strip the free prose inside Topology/Baselines/Open-questions entries
   (`wiki-memory.md` rules 13 and 29b keep that prose). The
   sentence exists because #97's soak measured the first reporting attempt
   hitting the gateway ceiling on slow runs while the retry — compacted to
   500-character tool results *and* asked for the most concise valid report
   — finished the full report in ~260 s every time; making that shape the
   default is the fix (rule 7e's cap, this sentence), not a per-attempt
   special case.]

### Notification Dedup

Notifications are the patrol's scarce human attention channel: a standing
condition is wiki state, not a new event. Dedup is **state-change keyed**,
not message-text keyed — the wiki's standing entries are the diff baseline
(issue #35; ADR `decisions/0026-notification-dedup-state-change.md`). The
four notification-worthy events are: a **new entry**, a **severity change**
(including escalation), a **status change** (resolution, expiry, or
re-opening), and a **periodic digest** for long-unfixed entries. A standing
entry whose status and severity are unchanged is wiki-only.

4a. **State-change filter** [DONE: #35]. Every notification the model
    proposes carries its target wiki entry's id as a leading bracketed tag:
    `[<entry id>] <human-readable message>`. The tag is part of the delivered
    message (the human benefits from seeing which entry an alert concerns).
    The pure parser
    `notification_entry_id(message) -> str | None` returns the id for a
    message matching `^\[([^\[\]]+)\] .+` (a non-empty id, a space, and a
    non-empty message), else `None` (untagged — including `[id]` with no
    message). The pure reader `wiki_entry_states(content) -> dict[str, dict]`
    maps each metadata-section entry id to
    `{"status", "severity", "first_seen", "last_notified"}` (a missing or
    unparseable optional pair reads as `None`; `status` and `first_seen` are
    the required six-key fields; first occurrence wins for a duplicate id;
    fence-aware scanning, rule 11's discipline; both metadata sections).
    The pure filter
    `filter_state_change_notifications(notifications, old_wiki, new_wiki) ->
    (delivered, suppressed, notified_ids)` decides each proposed message
    against the on-disk wiki (`old_wiki`) and the final validated wiki
    (`new_wiki`); on a file-absent fresh deployment `old_wiki` is the seed
    (rule 14) — it carries no entries, so every tagged target is new:
    - **new entry** — the tagged id is present in `new_wiki` but absent from
      `old_wiki` → deliver (newness is keyed to the wiki diff, not the
      `first_seen` date: an id the wiki has not carried before is new, an
      entry re-introduced after pruning is new again);
    - **status change** — present in both, `status` differs → deliver
      (resolution, expiry, or re-opening);
    - **severity change** — present in both, severity differs → deliver.
      A severity present on one side and absent on the other is a change
      (fail-open: gaining/losing the pair is news);
    - **removed entry** — present in `old_wiki` but absent from `new_wiki`
      → deliver (the entry left the working set; never suppress a
      disappearance);
    - **standing unchanged** — present in both with equal status and equal
      severity (including both absent) → **suppress**: not delivered, not
      printed, wiki-only;
    - **unattributable** — untagged, or the tagged id is in neither wiki →
      deliver unchanged (dedup must never lose news it cannot attribute).
    `delivered` and `suppressed` preserve the input order. `notified_ids` is
    the de-duplicated, first-delivery-ordered list of tagged ids delivered
    whose target exists in `new_wiki` (the entries the rule-4d notify stamp
    updates). The filter is pure — no I/O, no model calls, no mutation. The
    caller logs the suppression count to stderr — only when the count is
    greater than zero (no line on a clean patrol), verbatim:
    `[patrol] notifications: suppressed N standing-unchanged notification(s)
    (wiki-only)` (wording fixed; only `N` varies). Filtering runs only when
    the patrol has a **model-authored accepted wiki** to diff: the final
    report's wiki after validation/repair, or the rule-7d wiki-repair
    result. The keep-old-wiki, empty-wiki, and rule-31/33 minimal-write legs
    deliver every proposed notification unfiltered (fail-open) — there is no
    new state on disk, so there is nothing to dedup against, and an observed
    finding must never be lost because the wiki write failed. Findings are
    never filtered; only notification delivery is.
4b. **Model-facing dedup contract** [DONE: #35]. `REPORT_CONTRACT`
    carries the facts the mechanical filter depends on, verbatim
    (executable pins live in `test_notification_dedup.py`):
    - every notification begins with the entry-id tag —
      `Every notification MUST begin with the wiki entry id it concerns in
      square brackets, then the human-readable message — for example
      "[W-1] worker-1 has been NotReady for 40 days."`;
    - notifications are state changes — `Notify only when an entry is new, its
      severity changed, or its status changed (resolution or re-opening). Do
      not notify about an entry that is standing and unchanged: update its
      wiki entry and leave notifications empty for it.`;
    - every entry records its current severity — `Every Watch-list and
      Anomaly-log entry also carries a severity: <info|warn|crit> pair (after
      confidence, alongside recheck) recording the current severity of the
      condition; the patroller compares it across patrols to decide whether a
      change is worth a notification.`
    The contract teaches the same four events as rule 4a; the filter enforces
    them mechanically, so a compliant model's standing entries stay quiet and
    the patrol's own diff is the backstop. The issue's "watching →
    materialized" escalation is expressed as a status or severity change in
    this vocabulary — no new status value (`materialized`) is added to the
    skeleton (rule 12's closed status set).
4c. **Periodic digest** [DONE: #35]. A standing condition should nag
    occasionally, not every patrol. After rule-4a filtering, the process
    appends **at most one** digest notification for entries that have been
    unfixed long enough: the pure function
    `digest_notifications(old_wiki, new_wiki, *, today, digest_after,
    exclude_ids=(), patrol_count=None, stale_after=None) -> (digest, ids)`.
    `today` is the patrol start date (`YYYY-MM-DD`); `digest_after` is
    `PATROL_NOTIFY_DIGEST_AFTER` (Configuration Surface; default 7 days, `0`
    disables the digest entirely — returns `(None, [])`). An entry is a
    digest candidate iff all hold:
    - it is present in `new_wiki` with `status: watching` (resolved/expired
      entries are closed — the resolution was their notification);
    - its id is not in `exclude_ids` (already notified this patrol under
      rule 4a);
    - when `patrol_count` and `stale_after` are supplied (the write path
      passes P — the on-disk frontmatter count + 1, the value the stamp is
      about to write — and `STALE_AFTER`), a **Watch-list** candidate is
      **not** about to be auto-expired. This check reads the
      **process-maintained clock**: the
      old wiki's `verified_patrol` (the model's copy in `new_wiki` is never
      trusted, rule 20) together with the new entry's `last_verified`. If the
      new `last_verified` is strictly later than the old entry's (both
      parseable `YYYY-MM-DD`), the expiry pass will reset the clock to P —
      not skipped. Otherwise a parseable old `verified_patrol` with
      `P - verified_patrol >= stale_after` is skipped (`stale_after == 0`
      disables expiry and excludes nothing) — the digest must not announce
      what the expiry pass is about to retire. The skip applies only to
      Watch-list candidates: rule 21 never auto-expires an Anomaly-log
      entry, so an Anomaly-log `watching` entry is always digest-eligible
      (it must not fall permanently silent). The check is best-effort by
      construction: a fresh entry has no old clock and cannot be stale;
      a re-verification resets it; an old clock the model cannot alter is
      the only basis;
    - the **old wiki's** `first_seen` parses as `YYYY-MM-DD` and
      `(today - first_seen).days > digest_after` (strictly older than the
      threshold; exactly `digest_after` days is not yet digest-worthy); a new
      entry has no old `first_seen` and is never a digest candidate (its own
      notification is the state-change path);
    - its **last notification** — `last_notified` from `old_wiki` when
      parseable, else the **old wiki's** `first_seen` (the
      initial-notification day, so a pre-#35 entry gets one migration nudge)
      — is at least `digest_after`
      days old: `(today - last).days >= digest_after`. This makes the
      cadence weekly by default rather than per patrol.
      [DONE: #93 — the cadence is only durable because rule 4d
      **carries** the clock: the `last_notified` this bullet reads is the
      old on-disk wiki's value, and the write path restores that same value
      onto the new wiki before stamping, so a model rewrite that omits the
      pair cannot reset the cadence (the #93 evidence: the pair was dropped
      on every rewrite, and the digest re-fired every other patrol). The
      `first_seen` fallback therefore stays what it was meant to be — one
      migration nudge for a pre-#35 entry — and never a recurring
      substitute for a clock the model keeps dropping.]
    Candidates are ordered Watch-list document order, then Anomaly-log
    document order. When there is at least one, the digest is exactly
    `[digest] <count> entry unfixed for more than <N> days: <id>, <id>` for
    one candidate, or the same with `entries` for more than one; `N` is
    `digest_after`; ids are comma-and-space separated. It is a single
    process-authored message, never a finding, delivered last — after the
    filtered model notifications — through the standard `notify` path
    (rules 10–10c). The `[digest]` prefix is not an entry id: the digest is
    appended after rule-4a filtering and is never re-filtered, so the tag
    grammar's collision is harmless and deliberately labeled. The returned
    `ids` are the digest's entry ids in message
    order. When no candidate exists the digest is `None` with no ids. The
    function is pure — no I/O, no model calls.
4d. **Last-notified stamp** [DONE: #35]. The digest cadence reads a
    process-owned per-entry clock, `last_notified: YYYY-MM-DD` (the
    metadata-line pair of `wiki-memory.md` rule 12c) — the patrol start date
    on which the entry was last named by a delivered notification (state
    change under rule 4a or digest under rule 4c). After delivery, on a
    **model-authored accepted write** with a non-empty final wiki (not the
    keep-old-wiki or minimal-write legs), the pure function
    `stamp_last_notified(content, ids, day) -> str` rewrites the pair on every
    metadata-section entry whose id is in `ids`: an existing
    `last_notified: <date>` pair is replaced in place, an absent one is
    appended as the metadata line's final pair (` · last_notified: <day>`),
    and nothing else on the line, in the prose, or in the frontmatter is
    touched (byte-for-byte otherwise; fence-aware scanning; duplicate id —
    first occurrence only; an id absent from the content is a no-op). It runs
    in the write path after the anomaly-log compaction and before the
    frontmatter stamp (`wiki-memory.md` rule 16). The model's own
    `last_notified` value is never trusted (the pair is process-owned like
    `verified_patrol`); validation does not judge its value. The stamp is
    skipped on a patrol that writes no wiki (empty, rejected, or
    total-failure legs) — no accepted write, no clock advance (rule 20's
    accepted-write semantics).
    [DONE: #93 — the clock is **carried**, never merely re-stamped. Before
    the stamp, the write path runs a second pure function,
    `carry_last_notified(new_wiki, old_wiki) -> str`, which restores the old
    on-disk clock onto the wiki about to be written: for each
    metadata-section entry of `new_wiki` (first occurrence per id,
    fence-aware scanning, both metadata sections — the same reader discipline
    as `wiki_entry_states`, and the same first-occurrence-only rule as
    `stamp_last_notified` — a duplicate id's later lines are left
    byte-for-byte alone), the entry's `last_notified` pair becomes the
    **old wiki's** value for that id when that value is a valid calendar date
    in `YYYY-MM-DD` form (the `_is_calendar_date` test `wiki_entry_states`
    uses — `2026-02-30` is not a clock) — an existing pair replaced in place,
    an absent one appended
    as the metadata line's final pair — and is **removed** from the line when
    the old wiki carries no parseable clock for the id, or does not carry the
    id at all (a model-authored clock is never durable: whatever the model
    wrote is either replaced by the old value or dropped). Nothing else on
    the line, in the prose, or in the frontmatter is touched (byte-for-byte
    otherwise; a duplicate `last_notified` pair on the line collapses to the
    single carried value (all model-written pairs on that line go); an id
    absent from
    `new_wiki` is a no-op). It runs on the same leg as the stamp — a
    model-authored accepted write with a non-empty final wiki — after the
    anomaly-log compaction and **before** `stamp_last_notified`
    (`wiki-memory.md` rule 16), so an entry notified this patrol carries its
    old value and is then stamped with today's date, while an entry notified
    earlier keeps the carried date. Unlike the stamp it is **not** gated by a
    non-empty notified-id set: a quiet patrol whose model rewrite drops the
    pair still preserves the clock. The keep-old-wiki,
    empty-wiki, and rule-31/33 minimal-write legs run no carry: they write no
    model-authored content, and the minimal write re-writes the old wiki
    verbatim, its clocks included. Both functions are pure — no I/O, no model
    calls. `last_notified` stays unjudged by validation (`wiki-memory.md` rule
    15) and `wiki_version` stays 1: the schema does not change. Teaching the
    pair to the model is explicitly **not** the fix (the issue's direction 2):
    the model already drops optional process-owned pairs, so `REPORT_CONTRACT`
    is unchanged and the clock's durability is the process's job
    (`decisions/0033-process-owned-carry-of-the-notify-clock.md`).]
4e. **Delivery order and scope** [DONE: #35]. `run_patrol` builds the
    delivered list as the rule-4a `delivered` messages in proposed order,
    followed by the rule-4c digest when one exists (and only when a
    model-authored accepted wiki exists — the minimal-write and keep-old-wiki
    legs append no digest), then calls `notify` once as today.
    `report_notify_type` (rule 10b) still maps the patrol's findings, not the
    messages, so the digest never changes the sink-level severity.
    Per-notification severity and per-severity sink routing (apprise tags)
    remain deferred: notifications are still plain strings, and the id tag is
    the only structured part. The console `🔔 NOTIFY:` surface (rule 9a) is
    unchanged — only the set of messages reaching it shrinks.

### Reporting Pass

6. The report is produced by a **tool-free structured-output pass** over the finished
   patrol conversation — never by an in-agent forced response format (see
   `patrol-engine.md` Constraints). The conversation the pass receives is the
   **compacted** one (rule 7e) [DONE: #66] — the raw patrol conversation never
   reaches the reporting call.
7. The pass retries once on an invalid or empty result (a corrective user message is
   appended); total failure means no report and a warning is logged
   [DONE: #66 — and the total-failure outcome no longer stops at the warning:
   the patrol delivers one failure notification (rule 33) and runs the
   total-failure leg of the minimal-write net (`wiki-memory.md` rule 33) —
   the wiki-repair pass is skipped on this leg (there is no observed report,
   so there are no findings to merge) and the rule-31 fallback runs directly:
   the old wiki (the stamped seed on a file-absent fresh deployment, per rule
   31's clause) is written with the clock advanced; a legacy no-frontmatter
   wiki writes nothing, exactly like rule 31. The patrol also delivers one
   failure notification through the standard `notify` path (rules 10/10c) with
   `notify_type="FAILURE"` — message verbatim: `patrol produced no report —
   both reporting-pass attempts failed (see pod logs)` — so a chronically
   failing reporting pass is never silent (rule 4's don't-cry-wolf applies to
   *findings*; a blind patrol is always wolf-worthy). The warning line is
   verbatim: `[patrol] WARNING: reporting pass produced no report; skipping
   wiki write.` — "skipping" names the model's write; the rule-33
   fallback's minimal write follows it.]
7a. A report whose `updated_wiki` fails skeleton validation triggers **one repair
    pass**: the shape pass re-invoked with the validation errors and the rejected
    wiki. The repaired report replaces the first wholesale when its wiki
    validates; otherwise the first report's findings and notifications stand and
    the wiki is left unchanged (`wiki-memory.md` rules 15–19). Delivery — findings
    printing, notification — runs after the validation/repair decision resolves
    and before the physical wiki write, so exactly one report, the final one, is
    delivered [DONE: #5].
7b. **Empty-wiki classification** [DONE: #51]. A parsed result whose
    `updated_wiki` is empty is a **failed reporting attempt**, not a valid
    report — the patrol cannot distinguish an omitted field from a
    deliberate no-op, so the contract removes the no-op meaning entirely
    (rule 5: "nothing changed" is expressed by returning the wiki
    unchanged, never by omitting it). The pass retries once with a
    corrective user message that names the empty `updated_wiki` and
    demands the full content — a message distinct from rule 7's generic
    invalid-report corrective. [DONE: #85 — the corrective repeats
    rule 5's *narrowed* copy-verbatim ask (`except that patrol-history
    prose is never carried forward`), never the unqualified one:
    `wiki-memory.md` rule 29b applies to a no-change patrol too, and the
    executable pin lives in `test_empty_report.py`.] A retry that returns a wiki recovers
    normally — and that retry's report is the patrol's report: it replaces
    the first attempt wholesale (findings, notifications, wiki), the same
    wholesale replacement a successful rule-7a repair uses. When the retry
    also comes back wiki-less the report is
    **not discarded**: its findings and notifications still deliver (a
    wiki omission must never cost the patrol's observations), the wiki is
    left unchanged, and one stderr warning names the empty-wiki condition
    — verbatim: `[patrol] WARNING: reporting pass returned an empty
    updated_wiki; keeping the previous wiki, writing nothing` — the no-op
    is visible and countable, never silent. The
    classification keys on the wiki field alone: zero findings is a legal
    outcome (a clean cluster) and never triggers it. The repair pass
    (rule 7a) shares the classification — a repaired report with an empty
    wiki is retried the same way before the keep-old-wiki outcome
    applies. A repair that stays wiki-less falls to that keep-old-wiki
    outcome: the first report's findings and notifications deliver and
    the wiki-rejection warning fires (`wiki-memory.md` rule 16) — the
    first report's wiki was non-empty (it failed validation, not
    emission), so the empty-wiki warning does not. Across failure
    compositions the pass returns the **last
    result that parsed** — a wiki-less report included — and `None` only
    when no attempt parsed (rule 7's total failure): an empty-wiki
    attempt followed by an exception still delivers the parsed report's
    findings with the empty-wiki warning.
    [DONE: #60 — the still-empty outcome is no longer the end of the
    write path: the patrol tail runs the wiki-repair pass (rule 7d) and,
    behind it, the minimal-write fallback (`wiki-memory.md` rules 30–32).
    The warning above still fires first, verbatim.]
    [DONE: #106 — the reason this attempt logs gains rule 7h's
    truncation qualifier (`empty wiki (truncated at max_tokens)`).
    Nothing else in this rule moves: the classification, the corrective
    text, the one-retry bound, the last-parsed return, the verbatim
    warning, and the keep-old-wiki outcome are unchanged.]
7c. **Visible empty-result reasons** [DONE: #63]. A failed attempt's
    log line names its cause. The pass invokes the structured output with
    `include_raw=True`, so a `None`/unparsed result carries the raw model
    response — and the reason string in the attempt-failure log line
    (`[patrol] reporting pass attempt N failed (<reason>)`, rules 7/7b)
    is derived from it by a pure classifier,
    `classify_empty_result(raw, *, max_tokens=REPORT_MAX_TOKENS)`:
    - `empty result (truncated at max_tokens)` — the raw response's
      `finish_reason` is `"length"`, or its completion-token count
      (`usage_metadata`'s output tokens, reasoning included) reached the
      bound: the model ran out of budget before emitting the tool call
      (the #63 frozen-wiki root cause — on a reasoning model the entire
      combined reasoning+output budget can be spent thinking).
    - `empty result (no tool call)` — the model finished
      (`finish_reason` anything else, tokens under the bound) without
      emitting the PatrolReport tool call.
    The classifier is pure (no I/O, no model calls) and duck-typed over
    the raw message (`response_metadata["finish_reason"]`,
    `usage_metadata["output_tokens"]`). Exceptions keep today's behavior
    — the reason is the exception's string — and a structured-output
    parsing error reported in the raw envelope (rather than raised) is
    treated as the exception it replaces. The empty-wiki classification
    (rule 7b) is untouched by *this* rule: a parsed report with an empty
    wiki keeps its `empty wiki` reason unless rule 7h's truncation
    qualifier applies — and that qualifier fires on this rule's predicate
    (`hit_completion_bound`, rule 7h), never on a second definition of
    "truncated". [DONE: #106 — that "unless" clause is this sentence's only
    change; this rule's classifier, its `max_tokens` parameter, and both of its
    result strings are unchanged.]
7d. **Wiki-repair pass** [DONE: #60]. The shape pass's third
    invocation mode: `wiki_repair=(old_wiki, findings, notifications)`.
    Its user message states that the reporting pass could not emit the
    wiki and carries the old on-disk wiki verbatim plus the observed
    report's findings and notifications, asking for a corrected
    `PatrolReport` whose `updated_wiki` is the old wiki rewritten
    **minimally** — the findings merged in, everything else preserved.
    [DONE: #85 — "preserved" yields to the no-patrol-history ban
    (`wiki-memory.md` rule 29b): the minimal merge drops patrol-history
    prose rather than carrying it forward, and the user message states it
    verbatim (executable pin lives in `test_empty_report.py`):
    `Drop patrol-history prose while merging: no per-patrol headline log, no History: list, no "as of patrol #N" chronology — git history is the record.`
    The unqualified `preserve everything else verbatim` ask is reworded
    out — everything else is still preserved, but the prose rule 29b
    forbids is not (the same test pins the phrase's absence): a merge that
    preserved a per-patrol HEADLINE log would re-teach the model exactly
    the prose the ban forbids.]
    The mode shares everything with the pass's other modes: the
    `PatrolReport` binding, the reporting bounds (rules 8a/8b), and the
    internal parse/empty retry (rules 7/7b — a wiki-repair result with
    an empty wiki is retried with the named corrective before the
    caller's fallback applies). When the tail runs this mode, the rule
    that the call's own findings and notifications are never delivered
    (it never saw the cluster), and the minimal-write fallback behind
    it, are `wiki-memory.md` rules 30–32.
7e. **Compacted shape-pass input** [DONE: #66]. Every invocation of
    this pass — the report pass, the rule-7a repair pass, and the rule-7d
    wiki-repair pass — carries a **compacted** copy of the patrol
    conversation, not the raw one, produced by the pure function
    `compact_messages(messages, *, keep_chars=SHAPE_TOOL_KEEP)`. The
    compaction is mechanical and lossy in exactly one dimension: every
    message is kept, in order, with its role — but a tool-result body
    (a langchain `ToolMessage`, or a dict with `role == "tool"`) longer
    than `SHAPE_TOOL_KEEP` characters is replaced by its first
    `SHAPE_TOOL_KEEP` characters plus the marker line
    `[truncated by the patroller: first {keep} of {total} characters — full text not shown]`
    appended. Non-tool messages — the system prompt, the initial user
    mission, every model turn, and the patrol's plain-text final summary
    included — pass through verbatim. The function is pure (no I/O, no
    model calls), returns a new list (the input is never mutated), and
    duck-types both langchain message objects and plain dicts. The
    corrective retry messages the pass itself appends (rules 7/7b) are
    added after compaction, untruncated. `SHAPE_TOOL_KEEP` is a
    hard-coded constant (**500** characters, not env-configurable — the
    same convention as the rules-8a/8b bounds): real-run evidence
    (#66's soak) shows the shape pass's *input* — the full raw
    conversation plus the wiki plus the contract — has grown with the
    wiki to the point where the pass legitimately flirts with the model
    gateway's ~900 s request ceiling (both attempts 504ing at ~902 s on
    one patrol); a client-side timeout raise cannot help while the
    *gateway* caps the request, so the fix is to reduce what the pass
    carries. Evidence survives: findings cite short excerpts, and a
    500-character excerpt of any tool result is more citation than the
    report contract asks for; the full text remains in the patrol's own
    transcript (pod logs, rule 9a) and in the wiki's prose.
    [DONE: #97 — the cap drops 1500 → **500** and becomes the *only*
    cap: `SHAPE_TOOL_KEEP_RETRY` is retired — the symbol is **removed from
    `patrol.py`**, not left defined-but-unused, so `hasattr(patrol,
    "SHAPE_TOOL_KEEP_RETRY")` is false after implementation — and one
    constant governs every attempt of every mode. The evidence is the #97 soak — after #88 bounded
    the pass, the FIRST attempt still 504ed at ~902 s on 3 of 5 patrols,
    while the retry (500-char tool results plus the rule-7g brevity ask)
    completed the full report in ~260 s every time. The retry's shape is
    now the default instead of the recovery. Two levers made the retry
    cheap, and this rule owns one of them: `compact_messages` truncates
    **tool-result bodies only** (the wiki is not compacted here — it rides
    in the initial user message; its own pressure is the rule-29a size note
    and `wiki-memory.md` rules 28–29), so the smaller cap cuts the
    patrol transcript's tool payload; the rule-5 concision ask cuts the
    answer. 500 characters still carries every citation the report contract
    asks for, so the tool payload shrink is free at the report's fidelity. There is nothing
    left for a retry to shrink, which is why rule 7g loses its smaller
    cap (see that rule).]
7f. **Visible failure timing and gateway classification** [DONE: #66].
    Every attempt-failure log line (rules 7/7b/7c — the pass's internal
    retry, in every mode) carries the attempt's elapsed wall-time:
    `[patrol] reporting pass attempt N failed (<reason>, took E s)` with
    `E` the whole seconds (`int`) from attempt start (`time.monotonic`)
    to failure — so the gateway ceiling is measurable from pod logs
    (the #66 evidence: 504s landing at ~902 s were inferable only from
    timestamps). The reason for an **exception** failure is derived by
    the pure classifier `classify_exception(exc) -> str`: when the
    exception's string contains an OpenAI-style 5xx status (`Error
    code: 5NN`) or a timeout marker (case-insensitive `timed out` or
    `timeout`), the reason is `gateway failure (<original message>)`;
    any other exception keeps its own string, as today. The classifier
    is pure (no I/O) and string-based (the SDK's exception types are
    not importable guarantees); the rule-7c raw-response classification
    and the rule-7b `empty wiki` reason are untouched — only the
    exception leg and the elapsed suffix are new.
    [DONE: #88 — the line also carries the attempt's token counts. The
    full format becomes `[patrol] reporting pass attempt N failed
    (<reason>, took E s, in I out O tokens)`: `I` and `O` are the failed
    attempt's `usage_metadata` `input_tokens` and `output_tokens` from
    the raw response (the include_raw envelope; `O` counts reasoning
    tokens too, like the rule-7c completion count). When there is no raw
    response (an exception) or the metadata is absent, the field renders
    `?` — `in ? out ? tokens` — so the line is always the same shape and
    always present; the ceiling is measurable from pod logs in every
    failure mode, the gateway-failure one it exists to expose included
    (the #88 soak's two 902 s 504s carried no token counts, so the ~71
    output tok/s they imply was reconstructible only from the wiki's
    size). Everything else in this rule — elapsed seconds,
    `classify_exception`, and the rule-7c/rule-7b reasons — is
    unchanged.]
    [DONE: #97 — the line also names the completion bound the attempt
    ran under, so a tail is comparable across image versions: the full
    format becomes `[patrol] reporting pass attempt N failed (<reason>,
    took E s, in I out O tokens, bound B tokens)` with `B` =
    `REPORT_MAX_TOKENS` (rule 8b). It is a client-side constant, not
    gateway-reported data — nothing is invented, and the `?` rendering of
    `I`/`O` (no raw response, no metadata) is unchanged. Without it the
    #97 soak's question — "was the 24000 bound in force on this patrol?" —
    could only be answered from the image tag.]
    [DONE: #106 — the line's reason can now carry a qualifier: an empty-wiki
    failure reads `empty wiki (truncated at max_tokens)` when the attempt
    reached its completion bound (rule 7h). The line's shape —
    `(<reason>, took E s, in I out O tokens, bound B tokens)` — its elapsed,
    token, and bound fields, and `classify_exception` are unchanged; the
    parenthetical "untouched" statements above are scoped to their own
    changes and are superseded only for that one reason string.]
7g. **Ceiling-aware retry** [DONE: #88]. A **gateway failure** — the rule-7f `classify_exception` reason
    beginning `gateway failure (` (an OpenAI-style 5xx or a timeout) —
    still sends attempt 2 down its own path rather than extending attempt
    1's list: the gateway caps wall-clock time, not tokens, so a rebuild
    keeps the request from inheriting attempt 1's appended user message,
    and the pinned ceiling corrective labels the failure for the model and
    the log (the #88 evidence: two 504s at ~902 s apiece, ~41 minutes and
    ~550k input tokens for no report). Attempt 2 instead:
    - rebuilds its base message list from the raw patrol conversation
      with exactly `compact_messages(messages)` — the same single cap as
      attempt 1, so rule 7e's mechanics are unchanged (every message
      kept, in order, with its role; a tool-result body over the cap
      replaced by its first 500 characters plus the rule-7e marker line
      naming 500 and the true total; all non-tool text verbatim; the
      input never mutated). The attempt-1 message list is **discarded** —
      including the user message the pass appended to it — so the retry's
      prompt is the rebuilt base plus exactly one appended user message;
    - appends that one user message, which carries in this order, a blank
      line between each part: the mode's prefix (rule 7's report framing,
      rule 7a's validation-repair framing, or rule 7d's wiki-repair
      framing — unchanged), the pinned corrective below, then
      `REPORT_CONTRACT`, then — where the mode has one — the rule-29a
      `wiki_size_note`, which stays **last**. The contract is re-stated
      after the corrective because the rebuilt base no longer carries
      attempt 1's contract message, and a request for brevity must never
      cost the wiki-required instruction or the rule-5 concision ask (both
      live in the contract); the note stays last and appears exactly once
      in the prompt (rule 29a unchanged).
      The corrective, verbatim (reworded by #97 so it cannot contradict
      rule 5's completeness clause or the contract's wiki clause: it asks for
      the FULL wiki *without padding*, never for a shorter one, and confines
      "no optional prose" to the text around the report):

      `Your previous attempt failed at the model gateway's request ceiling — the request took too long. Return the PatrolReport again, stated without padding: the FULL updated_wiki unchanged in content, findings at one line each, no optional prose around the report.`

    The rebuild and the corrective are the retry's whole value now: the
    payload is already at its floor, a gateway 504 is **tail latency** —
    #97's soak had 3 of 5 first attempts fail and attempt 2 complete the
    full report every time in ~260 s — so the second attempt is a genuine
    second roll of the dice, and it stays the last-resort net before rule
    33. The corrective remains verbatim (not folded into the contract) so
    a ceiling failure still labels itself in the prompt and in the
    attempt log.
    Every shape-pass invocation's internal retry is ceiling-aware: the
    report pass, the rule-7a validation-repair pass, and the rule-7d
    wiki-repair pass all share the loop, so all three rebuild on a gateway
    failure (the wiki-repair mode still appends no size note, rule 29a).
    **Non**-gateway failures — a 4xx status, a plain exception, a raw empty
    result (rule 7c), or an empty wiki (rule 7b) — keep today's behavior
    exactly: the attempt-1 message list is extended, never rebuilt, with
    the existing corrective (the generic `That was not a valid
    PatrolReport. Produce it now.` or rule 7b's named empty-wiki
    corrective) plus the note suffix, and the base keeps the same
    `SHAPE_TOOL_KEEP` (500) compaction. The empty-wiki corrective
    therefore stays distinct and unchanged (rule 7b): a wiki omission is
    still answered by re-demanding the wiki, never by asking for brevity.
    There is exactly one compaction cap in the code (`SHAPE_TOOL_KEEP`).
    [DONE: #97 — the retry keeps its own path but can no longer shrink
    the payload, because attempt 1 now carries the retry's shape (rule 7e's
    single `SHAPE_TOOL_KEEP` cap of 500 characters, the rule-5 concision
    ask). Three things change, all of them narrowings:
    - the rebuild above uses **the same** `compact_messages(messages)` cap
      as attempt 1 (the pre-#97 text differed: 500 for the retry, 1500 for
      attempt 1), and the #88-era `SHAPE_TOOL_KEEP_RETRY` constant is
      **retired and deleted** from `patrol.py` (the module defines no such
      name after implementation), because a second, smaller cap would
      describe a payload no attempt sends;
    - the pinned corrective above is reworded as printed: it asks for the
      FULL `updated_wiki` *without padding* rather than "the shortest valid
      updated_wiki", so it cannot contradict rule 5's completeness clause or
      the contract's wiki clause (unqualified "no optional prose" was a
      licence to strip the wiki's free prose — `wiki-memory.md` rules 13,
      29b);
    - the retry's remaining value is stated plainly: the payload is already
      at its floor, a gateway 504 is **tail latency** (#97's soak: 3 of 5
      first attempts failed, attempt 2 completed the full report every time
      in ~260 s), so attempt 2 is a genuine second roll of the dice and
      stays the last-resort net before rule 33's total-failure leg.]
7h. **Bound-truncation qualifier on the empty-wiki reason** [DONE: #106].
    A failed empty-wiki attempt (rule 7b) reports **why** it was empty when
    the attempt hit its completion budget. The attempt-failure log line's
    reason (rules 7/7b/7f — every attempt of every mode) is
    `empty wiki (truncated at max_tokens)` when
    the attempt reached its completion bound, and plain `empty wiki`
    otherwise — so a tail is self-diagnosing from pod logs across image
    versions instead of reading as an unqualified empty wiki (the v0.3.2
    soak's attempt 1, `out 24000`, `bound 24000`: ADR 0036's design-space
    option 4, deferred there because a value change must not carry a
    classification change through one gate).
    "Reached its completion bound" is exactly rule 7c's truncation
    predicate — no stricter, no looser: the raw response's `finish_reason`
    is `"length"`, **or** its completion-token count (`usage_metadata`'s
    `output_tokens`, reasoning included) reached the bound
    (`REPORT_MAX_TOKENS`, rule 8b). One pure predicate
    (`hit_completion_bound(raw, *, max_tokens=REPORT_MAX_TOKENS) -> bool`)
    decides it for both reason paths — rule 7c's unparsed-result reason and
    this one — so the two can never disagree about what truncation is;
    `classify_empty_result` keeps its signature and both of its strings,
    expressed through that predicate. A pure builder
    (`empty_wiki_reason(raw, *, max_tokens=REPORT_MAX_TOKENS) -> str`)
    constructs this rule's two reason strings, duck-typed over the raw
    message like rule 7c's classifier, and never raises.
    The qualifier is **observability only** — it names a cause, it never
    changes an outcome. Unchanged, verbatim: the retry decision and its
    one-attempt bound, the corrective message and the narrowed copy-verbatim
    ask (rule 7b's #85 text), the last-parsed-result return of rule 7b's
    composition clause, the verbatim empty-wiki stderr warning, the
    keep-old-wiki outcome (rule 7b, `wiki-memory.md` rule 16), and the
    append-only retry shape of rule 7g (which keys on gateway failures
    alone — a truncated empty wiki is a non-gateway failure). Rule 33's
    total-failure leg is untouched and unreachable from here: an
    empty-wiki attempt *parsed*, so the pass returns its report and the
    FAILURE notification cannot fire on this reason; the qualifier appears
    on the attempt-failure log line only.
    The reason is built once and shared by **every** mode of the pass — the
    report, the rule-7a validation repair, and the rule-7d wiki repair — so
    no mode carries its own wording. When the attempt offers no truncation
    signal — no raw response to read at all (a directly-parsed result rather
    than the `include_raw` envelope), or a raw response that carries neither
    a `"length"` finish reason nor a usable completion count (absent or
    non-integer `usage_metadata`) — the reason stays the unqualified
    `empty wiki`, never a guess. Each signal stands alone: a `"length"`
    finish reason fires the qualifier even when the token count is missing.
    No new configuration surface: no env var, no CLI flag, no new constant.
    `REPORT_MAX_TOKENS` (28000), `REPORT_TIMEOUT` (900 s), #97's cheap
    first-attempt shape, and rule 33's total-failure net are all unchanged.
    The executable pin is `tests/test_empty_report.py`.
8. A budget-cut report must cover only what was actually observed; the pass is told
   which commands never executed.
8a. **Per-call-shape request bounds** [DONE: #49]. The two call shapes
    carry different HTTP request bounds, both hard-coded constants (not
    env-configurable): exploration turns are bounded at `REQUEST_TIMEOUT`
    (300 s — turns are small and fast, so a hung endpoint fails snappily),
    while this pass and its repair pass (rule 7a) are bounded at
    `REPORT_TIMEOUT` (900 s) — the single tool-free structured call
    carries the entire patrol conversation (real-run evidence: ~100k
    tokens in, up to 16k out at the time; the bound was 24k from #97
    (32k from #88 and 64k from #63 before that) and retunes to 28k
    [DONE: #101] — rule 8b [DONE: #88] [DONE: #97]) and
    legitimately runs minutes on
    reasoning-capable models. A reporting pass that cannot finish under
    the bound never writes the wiki, so the next patrol re-explores just
    as hard and the patroller never converges its memory — a fresh
    cluster never bootstraps; the shape pass therefore gets the headroom
    the exploration turns do not need (ADR
    `decisions/0018-reporting-pass-own-request-bound.md`).
8b. **Per-call-shape completion-token bounds** [DONE: #63]. The two
    call shapes carry different `max_tokens` bounds too, both hard-coded
    constants (not env-configurable): exploration turns keep
    `REQUEST_MAX_TOKENS` (16000 — turns are small), while this pass and
    its repair pass (rule 7a) are bounded at `REPORT_MAX_TOKENS`
    (28000; 24000 until #101 [DONE: #101]). The shape pass must emit
    the entire wiki — ~15 KB of
    markdown — in one structured response, and on a reasoning model the
    reasoning tokens come out of the same `max_tokens` allowance
    (real-run evidence, the frozen-wiki root cause: a 43.5k-token report
    prompt spent all 16000 completion tokens reasoning,
    `finish_reason=length`, zero content, twice per patrol — no wiki
    write for ~14 patrols; the same run with a 64k reporting budget
    completed at ~20k completion tokens and committed). `max_tokens` is
    a client-level property like the timeout, so it parametrizes the
    same `make_model` seam (default the exploration bound; the shape
    pass passes the reporting bound) — the reporting model carries both
    reporting bounds (ADR
    `decisions/0021-reporting-pass-own-token-bound.md`).
    [DONE: #88 — the reporting bound drops 64000 → 32000, keeping
    every word of the reasoning-exhaustion rationale above. The gateway
    caps wall-clock time, not tokens, and the #63 value spends it: the
    #88 soak's 64k-bounded generations hit the ~900 s cutoff at ~902 s
    on both attempts (≈71 output tok/s), while #63's successful run
    completed at ~20k completion tokens. 32000 caps generation at
    roughly half that wall time (~450 s), leaving headroom under the
    ceiling, and still sits ~12k above the observed 20k completion —
    strictly above it, which is the condition #63's fix depends on: the
    bound must never come back down to the starvation point where
    reasoning consumes the whole allowance. The full-text rationale for
    the value is ADR
    `decisions/0031-bound-the-shape-pass-with-a-ceiling-aware-retry.md`,
    which amends this value and leaves ADR 0021's per-call-shape split
    in force.]
    [DONE: #97 — the reporting bound drops 32000 → **24000**, keeping
    every word of the reasoning-exhaustion rationale above. #88's 32000
    capped generation at ~450 s at the ~71 output tok/s the 64k-bound
    full-payload run implied — a rate that belongs to the pre-#97 request —
    but the #97 soak (post-#88 image, a >20k-char wiki) still hit the ~900 s
    ceiling on the FIRST attempt of 3 of 5 patrols. The retry's rate is the
    honest one to plan from: it wrote a full report (~10k completion tokens:
    the ~20.5k-char wiki at ≈4 characters per token, plus findings and
    reasoning) in ~260 s, i.e. ~35–40 output tok/s, at which a 32000-token
    bound needs ~800–900 s — the window itself. 24000 needs ~600–685 s, leaving
    headroom under the ceiling, and sits ~4k above #63's observed ~20k
    completion, so the bound stays strictly above the reasoning-starvation
    point #63's fix depends on (the condition is "above 20k", not "12k
    above"). Lowering the bound is the *insurance*, not the fix: the #97
    soak's own recovery was the smaller payload plus the brevity ask, which
    now shape every attempt (rule 7e's 500-character cap, the rule-5
    concision ask). The value and the first-attempt compaction are recorded
    in ADR
    `decisions/0034-first-attempt-cheap-and-a-24k-report-bound.md`, which
    amends ADR 0031's values and leaves ADR 0021's per-call-shape split and
    the rule 8b reasoning rationale in force. If a 24k bound still measures
    tight after #86's anomaly-log byte bound lands, retuning it is another
    spec change with a fresh soak.]
    [DONE: #101 — the reporting bound retunes 24000 → **28000**, keeping
    every word of the reasoning-exhaustion rationale above and #97's cheap
    first-attempt shape: rule 7e's single `SHAPE_TOOL_KEEP` cap (500
    characters) and the rule-5 concision ask are unchanged, and so is rule
    8a's `REPORT_TIMEOUT` (900 s). The #101 evidence is the "24k still
    measures tight" case ADR 0034 anticipated, one bound later: on the v0.3.2
    image (ols-demo-2, a 19,215-char wiki) attempt 1's rule-7f line read
    `in 16435 out 24000 tokens, bound 24000 tokens` — `out` equal to the
    bound exactly, at 289 s, i.e. the completion budget exhausted and not
    the gateway clock — and its `updated_wiki` came back empty; the rule-7b
    retry recovered a 6-finding report in ~230 s on the same bound. 28000 is
    set by the two constraints the value must satisfy:
    - **above the observed truncation.** A valid report fits *under* 24000
      (the retry produced one there), so the truncating attempt's overshoot
      is reasoning variance, and 28000 adds 4000 tokens (17%) of headroom
      over the bound that was actually exhausted.
    - **inside the gateway's ~900 s window at the slowest observed
      throughput.** The #97 retry — the same cheap shape — wrote a full
      report of ~10k completion tokens in ~260 s, i.e. ~35–40 output tok/s,
      at which 28000 needs ~700–800 s. 32000 needs ~800–914 s at that rate —
      the window itself, which is the #88/#97 failure mode returning. The
      #101 soak's ~83 output tok/s (24000 tokens in 289 s) would make 32000
      comfortable (~385 s), but the two measured rates disagree by more than
      2×, and a hard gateway wall clock is the more expensive failure: a 504
      burns the whole window *and* the retry, while a token truncation is
      recovered by rule 7b in ~230 s. The value is therefore set by the
      slowest rate measured on this payload shape, and 32000 is rejected for
      this release — it stays the next rung if the soak shows attempt-1
      truncations persisting.
    ADR 0034's trigger is fulfilled by this retune while #86 stays open, and
    deliberately so: the trigger's `after #86's anomaly-log byte bound lands`
    clause attaches to the wiki's **input**-size lever (#86 bounds Anomaly-log
    bytes — ADR 0034's other half of the cost), while #101's failure is
    completion-side: `out` equal to the bound with a 19,215-char wiki (under
    the rule-29 20,000-char soft budget) and a valid report that the retry
    proved fits under that same bound. Nothing here bounds the wiki, prunes
    it, or changes rules 28–29; #86 remains the wiki-size lever it always was.
    What ADR 0034 requires of a retune — a fresh spec change with a fresh soak
    — is exactly what this rule and its acceptance soak are, so the #86 clause
    is not overridden; it does not gate a completion-side retune.
    The value is a hypothesis with a required acceptance soak (3–5 real
    patrols recording the existing rule-7f fields: the attempt-1 outcome, its
    `in`/`out` token counts, and the `bound` line), exactly as ADR 0034
    requires of any retune — a retune is a spec change with a fresh soak,
    never a hotfix. Nothing else moves: rule 7f's `B` field follows the
    constant, so the failure line reads `bound 28000 tokens` after
    implementation, and the #63 floor (20k — the observed completion #63's
    successful run produced, pinned as the regression guard in
    `tests/test_empty_report.py`) is still 8k below the bound.
    The optional #101 observability narrowing — naming truncation when a
    *parsed* report carries an empty `updated_wiki` **and** the completion
    count reached the bound, i.e. giving rule 7b's `empty wiki` reason a
    truncation qualifier — is **deferred** to its own spec item and is not
    part of this retune: it changes rule 7b/7c classification, and a value
    change must not carry a classification change through the same gate.
    [DONE: #106 — that deferred item is now specced as rule 7h; nothing
    above moves.]

### Delivery

9. Findings print to the console with a severity column; each finding's evidence
   prints with them unless suppressed by `--quiet` [DONE: #27 — evidence used
   to print only in verbose mode].
9a. The patrol's per-turn output — model reasoning previews, tool invocations,
    the guard's expanded command line, tool-result previews, agent messages —
    prints to **stderr** as it happens, **on by default**; `-q/--quiet`
    suppresses it (findings and notifications always print). In a container,
    pod logs are the human surface and stderr is line-buffered, so the stream
    lands in `oc logs` line-by-line with no buffering workaround — a healthy
    patrol is never silent between its start banner and its findings block
    [DONE: #27].
9b. **Portable console text** [DONE: #77]. The process's stdout and stderr
    are **UTF-8 with LF newlines regardless of the host locale**: at startup
    both streams are reconfigured to `encoding="utf-8"`, `errors="replace"`,
    and `newline="\n"` before any line is printed (a stream that cannot be
    reconfigured is left as-is — never fatal). The console surface carries the
    ` · ` separator in the stats block (`wiki-memory.md` rule 24) and the `🔔`
    notification prefix (rule 10), so without the pin the same
    line is UTF-8 on Linux and cp1252/CRLF on Windows — and an emoji can raise
    `UnicodeEncodeError` outright on a pipe. Console text is human output, never
    re-read by the process; the pin makes pod logs, redirected files, and test
    captures host-independent (ADR 0029).
10. Each notification prints with a `🔔 NOTIFY:` prefix; if `PATROL_NOTIFY_URLS` is
    set, notifications are also delivered to every configured sink via
    [apprise](https://github.com/caronc/apprise) (rules 10a–10c) [DONE: #9].
10a. **Sink configuration** [DONE: #9]. `PATROL_NOTIFY_URLS` holds one or
    more apprise sink URLs (`slack://…`, `discord://…`, `tgram://…`,
    `ntfys://…`, … — any scheme apprise supports), separated by commas and/or
    newlines. Parsing is a pure function,
    `parse_notify_urls(value) -> list[str]`: split on `,` and `\n`, strip
    whitespace, drop empty entries. Unset or empty → zero sinks (console
    only — today's no-`SLACK_WEBHOOK_URL` behavior). The variable replaces
    `SLACK_WEBHOOK_URL`, which is **removed**: an operator migrating a Slack
    incoming webhook expresses it as its apprise `slack://` URL (apprise's
    documented webhook form). The parsed list is read once at import (the
    same env-derived-config convention as the other `PATROL_*` vars).
10b. **Severity → notify type** [DONE: #9]. Delivery maps the report's
    severities onto apprise's `notify_type` at the patrol level, via the pure
    helper `report_notify_type(findings) -> "FAILURE" | "WARNING"`:
    `FAILURE` when the delivered report contains at least one `crit` finding,
    `WARNING` otherwise. `INFO` is never emitted — rule 4 restricts
    notifications to warn/crit, and notifications are plain strings with no
    per-message severity (pairing them back to findings would be fragile), so
    the patrol's worst severity is the honest mapping. Per-notification
    severity and per-severity sink routing (apprise tags) are deferred to the
    #35 follow-up family.
10c. **Delivery semantics** [DONE: #9]. Per-sink best-effort, never fatal
    (the rule-10 status quo generalized). `notify(messages, *,
    notify_type="WARNING")` builds one apprise instance per invocation that
    has at least one configured URL (an empty message list still constructs
    it and sends nothing; zero configured URLs constructs none — console
    only), adds
    each configured URL (a URL apprise refuses to load is logged to stderr
    and skipped — the other sinks still deliver), and calls
    `apprise.notify(title="Cluster patrol", body=<message>,
    notify_type=notify_type)` once per message. A sink delivery failure
    (exception or `False` return) is logged to stderr and never raises: a
    patrol with zero reachable sinks still delivers its findings, writes the
    wiki, and exits zero. The console `🔔 NOTIFY:` line (rule 9a's pod-log
    surface) is unchanged and unconditional. The `:rotating_light:` Slack
    emoji prefix is dropped — apprise renders `notify_type` per sink.
11. Token usage (input/output, summed across the patrol's model turns) prints at
    patrol end.

## Configuration Surface

- `PATROL_NOTIFY_URLS` — if set, apprise sink URLs (comma/newline separated);
    notifications are also delivered to every configured sink [DONE: #9]
- `PATROL_NOTIFY_DIGEST_AFTER` — the periodic-digest threshold in days
    (rule 4c): a standing watching entry older than this many days, and not
    notified within this many days, is named by one process-authored digest.
    Non-negative integer, default 7; `0` disables the digest (state-change
    notifications are unaffected). Read once at import; an unparseable or
    negative value fails startup with a config error — the patrol never runs
    on a silently wrong digest cadence [DONE: #35]
- ~~`SLACK_WEBHOOK_URL`~~ — removed by #9; superseded by `PATROL_NOTIFY_URLS`
  (a Slack incoming webhook is expressed as its apprise `slack://` URL)

## Constraints

- Notification dedup is mechanical but **state-keyed** (rules 4a–4d): the wiki's
  persisted entries are the diff baseline, so a patrol whose wiki write is
  rejected or empty has no new baseline and repeats its state-change
  notifications next run. The digest has the same shape: with no accepted write
  there is no `last_notified` stamp, so an over-due entry digests every patrol
  until a write lands. Untagged notifications bypass dedup by design
  (fail-open): dedup may reduce noise, never lose news. The model still owns
  whether a notification is worth writing at all (rule 4's don't-cry-wolf) and
  whether an entry carries its severity pair; the process enforces the diff it
  can see.
- The configured apprise sinks are the patrol's deliberate outbound notification
  allowlist — network-level exfiltration control is `egress-control.md`'s (#34:
  the derived default-deny allowlist, ADR 0030), not this contract's
  [DONE: #9] [DONE: #34 — supersedes the sentence that left the egress
  question open].

## Planned Changes

| Ticket | Summary |
|---|---|
| ~~#106~~ | ~~The empty-wiki reason names bound truncation (rule 7h; rule 7c's truncation predicate shared by both reason paths, ADR 0036's design-space option 4 deferred there): a parsed report whose `updated_wiki` is empty logs `empty wiki (truncated at max_tokens)` when the attempt reached its completion bound and plain `empty wiki` otherwise, so a tail is self-diagnosing from pod logs across image versions. Observability only — classification, corrective, retry bound, last-parsed return, verbatim warning, keep-old-wiki outcome, and rule 33's total-failure leg are unchanged; no new configuration surface. Impl commit also syncs `AGENTS.md`'s test list and `how/project-structure.md`'s per-file test inventory (both enumerate `test_empty_report.py`'s coverage) and extends `shape_report`'s docstring where it states the empty-wiki classification (the reason now has two strings).~~ |
| ~~#101~~ | ~~The reporting pass's completion bound retunes `REPORT_MAX_TOKENS` 24000 → **28000** (rule 8b's `[DONE: #101]` block, ADR 0036; the value also lands in `patrol-engine.md`, `system-overview.md`, `wiki-memory.md` rule 29, `how/agent-engine.md`, `how/reporting-pipeline.md`, and the glossary): the v0.3.2 soak's first attempt exhausted the 24000 completion budget (`out 24000`, 289 s — the token bound, not the gateway clock) and returned an empty wiki, so the goal is headroom over the bound that was actually exhausted while staying inside the gateway's ~900 s window at the slowest observed throughput (~35–40 output tok/s, where 28000 is ~700–800 s and 32000 would be ~800–914 s). #97's cheap shape is unchanged — one `SHAPE_TOOL_KEEP` cap of 500 characters, the rule-5 concision ask, rule 7g's same-cap rebuild — as are rule 8a's `REPORT_TIMEOUT` 900 s, rule 7f's `bound B tokens` field (which follows the constant), and rule 33's total-failure net. The optional #101 observability narrowing (a truncation qualifier on rule 7b's `empty wiki` reason) is deferred to its own spec item, named in rule 8b. A 3–5 patrol real-cluster soak recording attempt-1 outcomes with the existing failure line is the issue's acceptance evidence, and it is not a test. Impl commit also syncs `AGENTS.md`'s test list and rewrites the stale `patrol.py` comment above `REPORT_MAX_TOKENS` (its “at that honest rate 24000 is ~600–685 s” arithmetic and its ADR list carry the pre-#101 value; they must state 28000's ~700–800 s at the slow rate and cite ADR 0036)~~ |
| ~~#93~~ | ~~The notify clock is carried, not just stamped (rule 4d's new carry paragraph, plus rule 4c's durability note; the wiki half is `wiki-memory.md` rules 12c/16; ADR 0033): the digest cadence collapsed to every other patrol because the model drops the optional process-owned `last_notified` pair on every rewrite and rule 4d only stamped the ids notified *this* patrol. A new pure `carry_last_notified(new_wiki, old_wiki)` restores the old on-disk value onto every entry present in both wikis and drops any model-written pair for an entry the old wiki has no clock for, running on the same accepted-write leg as the stamp — after compaction, before `stamp_last_notified`. The model's copy is never trusted, `REPORT_CONTRACT` is unchanged (teaching is not the fix), and the keep-old-wiki/empty-wiki/minimal-write legs run no carry. No schema change (`wiki_version` stays 1), no configuration surface change. Impl commit also syncs `AGENTS.md`'s test list~~ |
| ~~#97~~ | ~~The reporting pass's **first** attempt becomes the cheap one (rules 5's concision ask, 7e's cap, 7f's bound field, 7g's narrowed retry, 8a parenthetical, 8b value here; the bound also lands in `wiki-memory.md`, `patrol-engine.md`, `system-overview.md`, `how/agent-engine.md`, `how/reporting-pipeline.md`, `how/project-structure.md`, and the glossary; ADR 0034 amends ADR 0031): after #88 the first attempt still 504ed at ~900 s on 3 of 5 patrols while the retry — 500-character tool results plus the brevity corrective — finished the full report in ~260 s every time, so the retry's shape becomes the default. `SHAPE_TOOL_KEEP` drops 1500 → 500 and becomes the only compaction cap (`SHAPE_TOOL_KEEP_RETRY` retired: there is nothing left for a retry to shrink, so rule 7g keeps its rebuild and corrective but no longer claims a smaller payload); one pinned concision ask (two sentences) joins `REPORT_CONTRACT`, so every attempt of every mode carries it; `REPORT_MAX_TOKENS` drops 32000 → 24000 (≈600–685 s of generation at the observed throughput, still ~4k above #63's observed ~20k completion — the starvation floor is untouched); and the attempt-failure log line gains `bound B tokens` so a tail is comparable across image versions. The rule-7g retry, rule 7b's empty-wiki corrective, and rule 33's total-failure net are unchanged in reach. A real-cluster soak showing zero attempt-1 504s (or a recorded decision that the retry remains the intended path) is the issue's acceptance evidence, and it is not a test. Impl commit also syncs `AGENTS.md`'s test list — which includes **rewriting** its #88 retry paragraph (it still describes the retry as shrinking its base to a `SHAPE_TOOL_KEEP_RETRY` cap and the non-gateway retry as append-only at a 1500-char base), not merely appending a #97 bullet. The impl commit also rewrites the stale `patrol.py` comments: the `SHAPE_TOOL_KEEP_RETRY` constant and its comment go, `shape_report`'s docstring stops describing a smaller retry cap, and the `SHAPE_TOOL_KEEP`/`REPORT_MAX_TOKENS` comments carry the #97 values~~ |
| ~~#88~~ | ~~The shape pass inside the model gateway's ~900 s ceiling (rules 7f, 7g, 8a parenthetical, 8b value here; rule 8b's `REPORT_MAX_TOKENS` 64000 → 32000 also lands in `wiki-memory.md`, `patrol-engine.md`, `system-overview.md`, `how/agent-engine.md`, and `how/reporting-pipeline.md`; ADR 0031): the bound halves because a 64k-bounded generation hit the gateway's ~900 s wall clock at ~902 s twice (≈71 output tok/s) while #63's successful run needed only ~20k completion tokens — ~450 s at 32000, still ~12k above the observed completion. A gateway-failure retry becomes ceiling-aware (rule 7g): attempt 2 rebuilds its base from the raw conversation compacted at the new hard-coded `SHAPE_TOOL_KEEP_RETRY` (500 characters) and asks for the most concise valid report — with the contract re-stated after the corrective and the size note last — instead of repeating the identical request; non-gateway failures keep their correctives and the 1500-char cap, and the rule-7b empty-wiki corrective stays distinct. The attempt-failure log line gains the attempt's token counts (`in I out O tokens`, `?` when unknown). Total-failure behavior is unchanged (`wiki-memory.md` rule 33). Splitting the report into two calls was rejected (ADR 0031). Exploration overshoot (547,804 vs 500,000 input tokens, checked per turn) is out of scope here — the token soft-stop owns it (`patrol-engine.md` rule 8b, #10). Impl commit also syncs `AGENTS.md`'s test list~~ |
| ~~#85~~ | ~~Patrol-history prose is banned in the report contract (rule 5's #85 clauses; behavior in `wiki-memory.md` rule 29b, ADR 0032): the wiki records current state only — no per-patrol headline log, no `History:` list, no "as of patrol #N" chronology, because git history is the record of what changed when; the opening prose stays a few lines; a `###` status block after the Anomaly log is legal while a sixth `##` section is not. #51's copy-verbatim sentence is narrowed by the same exception (`except that patrol-history prose is never carried forward`) and the empty-wiki corrective repeats the narrowed wording, so no model-facing surface carries both asks. The wiki-repair mode's minimal merge drops history prose instead of preserving it (rule 7d). Teaching only — the working-set/size-note machinery is unchanged and length still never gates validation. Impl commit also syncs `AGENTS.md`'s test list and `how/reporting-pipeline.md`'s `REPORT_CONTRACT` row~~ |
| ~~#34~~ | ~~Only the Constraint sentence that left network-level egress control open: it now points at `egress-control.md` (the derived default-deny allowlist, ADR 0030). The notification contract here is unchanged — the configured sinks stay the deliberate outbound notification allowlist, and what they may carry is the guard's business~~ |
| ~~#77~~ | ~~Dev/test host portability (rule 9b here; `wiki-memory.md` rules 4c/4g; ADR 0029): stdout/stderr are reconfigured to UTF-8/LF at startup so the ` · ` separator, severity marks, and `🔔` prefix print as the same bytes on every host, and a non-UTF-8 stream never raises. The wiki half is the byte-determinism pin on rule 4c (`wiki-memory.md`) and the explicit-UTF-8 read rule. Impl commit also syncs `AGENTS.md` (test conventions + test list) and `how/project-structure.md`~~ |
| ~~#67~~ | ~~Wiki size feedback in the reporting pass (rule 5 amendment here; rule 29a in `wiki-memory.md`): `REPORT_CONTRACT` names the consequence chain (over-budget wiki → slower reporting pass → model gateway's request ceiling → the next patrol re-reading prose) and requires a smaller returned wiki when the size line says over budget (prune at least the excess, unless new facts require the space); every report-pass invocation appends the deterministic `wiki_size_note` line. ADR 0025 rejects process-side prose compaction. Impl commit also syncs `AGENTS.md`'s test list~~ |
| ~~#66~~ | Reporting-pass gateway-ceiling survival (rules 6, 7, 7e, 7f here; `wiki-memory.md` rule 33; ADR 0024): every shape-pass invocation carries the compacted conversation (pure `compact_messages`, `SHAPE_TOOL_KEEP` 1500 chars, tool-result bodies truncated with a marker); every attempt-failure log line carries elapsed seconds and exception reasons pass the pure `classify_exception` (5xx/timeout → `gateway failure (…)`); a total-failure patrol delivers one FAILURE notification (pinned message) and runs the rule-31 minimal-write fallback (wiki-repair pass skipped — no findings to merge). Impl commit also syncs `AGENTS.md`'s test list~~ |
| ~~#60~~ | ~~Wiki-repair pass + minimal-write fallback (rule 7d here; the tail contract, bounds, and fallback are `wiki-memory.md` rules 30–32, ADR 0022): the shape pass gains a `wiki_repair=(old_wiki, findings, notifications)` mode — old wiki verbatim plus the observed findings, minimal-merge ask, shared binding/bounds/retry; its findings are never delivered. Impl commit also syncs `AGENTS.md`'s test list~~
| ~~#63~~ | ~~Per-call-shape completion-token bounds + visible empty-result reasons (rules 7c + 8b): exploration `REQUEST_MAX_TOKENS` 16000, structured reporting/repair calls `REPORT_MAX_TOKENS` 64000 through the `make_model` seam; the attempt-failure log line names its cause (`truncated at max_tokens` vs `no tool call`) via a pure classifier over the raw response. Impl commit also syncs `AGENTS.md`'s test list and the stale `max_tokens` prose in `patrol.py`'s code comments (e.g. the working-set comment near the shape-pass budget)~~ |
| ~~#51~~ | ~~Empty PatrolReport (rules 5 + 7b): the report contract requires `updated_wiki` on every report; an empty one is a failed attempt — one named corrective retry, then findings-only delivery with a stderr warning, wiki unchanged. Impl commit also syncs `AGENTS.md`'s test list~~ |
| ~~#49~~ | ~~Per-call-shape request bounds (rule 8a): exploration turns `REQUEST_TIMEOUT` 300 s, structured reporting/repair calls `REPORT_TIMEOUT` 900 s — two `make_model` instances through the same seam; the specced constants in `agent-engine.md` / `system-overview.md` updated to the split, bounds pinned by test. Impl commit also syncs `AGENTS.md`'s test list~~ |
| ~~#9~~ | apprise multi-sink notifications (rules 10a–10c): `PATROL_NOTIFY_URLS` (comma/newline-separated apprise URLs, pure `parse_notify_urls`) replaces the removed `SLACK_WEBHOOK_URL`; patrol-level `notify_type` via pure `report_notify_type` (crit anywhere → FAILURE, else WARNING); per-sink best-effort delivery, failures logged to stderr, never fatal; apprise joins the pinned dependency set (`container-image.md` rules 6–7), `requests` drops from the top-level set (transitive via apprise); manifest Secret optional key becomes `PATROL_NOTIFY_URLS`. Impl commit also syncs `AGENTS.md`'s test list, the repo `README.md`'s env/config table (`SLACK_WEBHOOK_URL` → `PATROL_NOTIFY_URLS`) and any remaining Slack prose, and the `run_patrol_cli` test docstring's env pin list |
| ~~#35~~ | ~~Notification dedup, state-keyed (rules 4a–4e; `wiki-memory.md` rules 12b–12c and the rule-16 write-path amendment; ADR 0026): notifications carry `[<entry id>]` tags and are filtered against the old-vs-new wiki diff — new entry, severity change, status change, or removal delivers; a standing unchanged entry is wiki-only (suppressed, count on stderr). Entries gain an optional `severity` pair (judged when present) so severity changes are diffable, and the model contract requires the tag and the severity pair. A process-authored weekly digest (`PATROL_NOTIFY_DIGEST_AFTER`, default 7 days, `0` disables) names long-unfixed watching entries; entries notified this patrol get a process-stamped `last_notified` date after compaction. Per-notification severity and apprise-tag sink routing stay deferred. Impl commit also syncs `README.md`'s config table and `AGENTS.md`'s test list~~ |
| ~~#40~~ | ~~Report contract states the working-set expectations: the wiki is a working set (git history is the archive), summarize and prune rather than accumulate, the soft character budget, the anomaly-log keep budget (`wiki-memory.md` rules 28–29)~~ |
| ~~#7~~ | ~~Report contract states the staleness clock's model-facing half: `verified_patrol` is process-stamped (never computed by the model), `last_verified` advances only on actual re-verification, and unverified watching entries auto-expire (`wiki-memory.md` rules 20–22)~~ |
| ~~#6~~ | ~~Report contract asks for recheck commands: every Watch-list entry carries a guard-acceptable `recheck:` pair in tool-argument form (`wiki-memory.md` rule 12a)~~ |
| ~~#33~~ | ~~Recheck guidance carries the full Secret-access ban (rule 5): the contract states exactly that a recheck must never read a Secret — not even names or metadata — matching the guard's rules 5–6b (ADR 0028). Impl commit also syncs the `REPORT_CONTRACT` text and `AGENTS.md`'s test list~~ |
| ~~#27~~ | Per-turn output stream on by default (`-q/--quiet` opts out); evidence prints with findings (rule 9a) |
| ~~#5~~ | Wiki-skeleton validation on `updated_wiki` with one shape-pass repair retry; final-report delivery ordering (rule 7a; contract in `wiki-memory.md` rules 15–19) |
