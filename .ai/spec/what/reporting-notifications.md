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
   [DONE: #6]. [DONE: #7 — the contract also carries the staleness
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
   verbatim. An empty updated_wiki is never a valid answer: a patrol with
   nothing to change returns the wiki unchanged."
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
    invalid-report corrective. A retry that returns a wiki recovers
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
    (rule 7b) is untouched: a parsed report with an empty wiki keeps its
    `empty wiki` reason.
7d. **Wiki-repair pass** [DONE: #60]. The shape pass's third
    invocation mode: `wiki_repair=(old_wiki, findings, notifications)`.
    Its user message states that the reporting pass could not emit the
    wiki and carries the old on-disk wiki verbatim plus the observed
    report's findings and notifications, asking for a corrected
    `PatrolReport` whose `updated_wiki` is the old wiki rewritten
    **minimally** — the findings merged in, everything else preserved.
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
    hard-coded constant (1500 characters, not env-configurable — the
    same convention as the rules-8a/8b bounds): real-run evidence
    (#66's soak) shows the shape pass's *input* — the full raw
    conversation plus the wiki plus the contract — has grown with the
    wiki to the point where the pass legitimately flirts with the model
    gateway's ~900 s request ceiling (both attempts 504ing at ~902 s on
    one patrol); a client-side timeout raise cannot help while the
    *gateway* caps the request, so the fix is to reduce what the pass
    carries. Evidence survives: findings cite short excerpts, and a
    1500-character excerpt of any tool result is more citation than the
    report contract asks for; the full text remains in the patrol's own
    transcript (pod logs, rule 9a) and in the wiki's prose.
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
8. A budget-cut report must cover only what was actually observed; the pass is told
   which commands never executed.
8a. **Per-call-shape request bounds** [DONE: #49]. The two call shapes
    carry different HTTP request bounds, both hard-coded constants (not
    env-configurable): exploration turns are bounded at `REQUEST_TIMEOUT`
    (300 s — turns are small and fast, so a hung endpoint fails snappily),
    while this pass and its repair pass (rule 7a) are bounded at
    `REPORT_TIMEOUT` (900 s) — the single tool-free structured call
    carries the entire patrol conversation (real-run evidence: ~100k
    tokens in, up to 16k out at the time — 64k since #63, rule 8b) and
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
    (64000). The shape pass must emit the entire wiki — ~15 KB of
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
  allowlist — the egress question (network-level exfiltration controls) is #34's,
  not this contract's [DONE: #9].

## Planned Changes

| Ticket | Summary |
|---|---|
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
| ~~#27~~ | Per-turn output stream on by default (`-q/--quiet` opts out); evidence prints with findings (rule 9a) |
| ~~#5~~ | Wiki-skeleton validation on `updated_wiki` with one shape-pass repair retry; final-report delivery ordering (rule 7a; contract in `wiki-memory.md` rules 15–19) |
