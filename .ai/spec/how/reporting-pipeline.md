# Reporting Pipeline (shape pass)

How the finished patrol conversation becomes the typed `PatrolReport`, and how the
report is delivered. The behavioral contract is in `what/reporting-notifications.md`.

## Module Map

| Symbol | Responsibility |
|---|---|
| `REPORT_CONTRACT` | Instructions appended for the reporting pass (field semantics, wiki skeleton format: frontmatter keys, section set, entry metadata line [DONE: #5]; the Watch-list recheck requirement + its tool-argument form [DONE: #6]; the staleness clock's model-facing half — `verified_patrol` is process-stamped, `last_verified` advances only on actual re-verification, unverified watching entries auto-expire [DONE: #7]; the working-set expectations — the wiki is a working set (git history is the archive), summarize and prune within the soft character budget, the Anomaly log keeps the last `WIKI_ANOMALY_KEEP` closed entries [DONE: #40], plus #67's consequence chain and over-budget-must-shrink instruction [DONE: #67]; the wiki-required instruction — `updated_wiki` is required on every report, full content even when nothing needs changing, an empty one is never a valid answer [DONE: #51]) |
| `wiki_size_note` | Pure model-facing size line (#67, `wiki-memory.md` rule 29a): `wiki_size_note(content, *, budget=None) -> str` — empty for empty content, else one line naming the character count, budget, floored percentage, and the excess (`N characters OVER budget. Prune at least N characters…`) or headroom; `budget` defaults to `WIKI_CHAR_BUDGET` read at call time [DONE: #67] |
| `shape_report` | Tool-free structured-output pass with one schema retry; `repair=(rejected_wiki, errors)` re-invokes it as the wiki-validation repair pass [DONE: #5]; a parsed result with an empty `updated_wiki` is a failed attempt — retried with a corrective message naming it, a still-empty retry returns the report [DONE: #51]; a `None`/unparsed result's failure reason is classified from the raw response (`classify_empty_result`: truncated-at-max_tokens vs no-tool-call) [DONE: #63]; `wiki_repair=(old_wiki, findings, notifications)` re-invokes it as the wiki-repair pass — the old wiki verbatim plus the observed findings, minimal-merge ask (rule 7d) [DONE: #60]; every report-pass invocation appends the `wiki_size_note` of its wiki-in-play (on-disk wiki for the report pass, rejected wiki for the validation repair; none for the wiki-repair pass) [DONE: #67] |
| `report_fields` | Normalizes the result (pydantic model or dict) into plain data |
| `notify` | Console `🔔 NOTIFY` + apprise multi-sink delivery (`PATROL_NOTIFY_URLS`, rules 10a–10c) [DONE: #9] |
| `notification_entry_id` | Pure parser for a proposed notification's leading `[<entry id>]` tag (rule 4a); `None` for an untagged message [DONE: #35] |
| `wiki_entry_states` | Pure reader of a wiki's metadata-section entries: id → `{status, severity, first_seen, last_notified}` (first occurrence wins, fence-aware; missing optional pairs read as `None`) [DONE: #35] |
| `filter_state_change_notifications` | Pure old-vs-new wiki diff over proposed notifications (rule 4a): `(delivered, suppressed, notified_ids)` — new/severity-changed/status-changed/removed entries deliver; standing unchanged entries suppress; untagged/unattributable messages pass through fail-open [DONE: #35] |
| `digest_notifications` | Pure periodic-digest builder (rule 4c): `(digest, ids)` for standing watching entries older than `PATROL_NOTIFY_DIGEST_AFTER` days and not notified within the window; skips entries the expiry pass is about to retire and `exclude_ids` already notified this patrol [DONE: #35] |
| `stamp_last_notified` | Pure process-owned per-entry clock stamp (rule 4d): `stamp_last_notified(content, ids, day) -> str` — rewrites or appends `last_notified: <day>` for the notified ids, nothing else touched [DONE: #35] |
| `classify_empty_result` | Pure classifier for a `None`/unparsed result's failure reason from the raw response (truncated-at-max_tokens vs no-tool-call) [DONE: #63] |
| `classify_exception` | Pure classifier for an exception failure's reason — 5xx (`Error code: 5NN`) or timeout markers become `gateway failure (…)`; anything else keeps the exception's string [DONE: #66] |
| `compact_messages` | Pure conversation compaction for every shape-pass invocation: all messages kept in order, tool-result bodies over `SHAPE_TOOL_KEEP` (1500) chars truncated with a marker; non-tool messages verbatim [DONE: #66] |
| `run_patrol` (tail) | Validates the report's wiki against the skeleton (one repair pass on failure, `validate_wiki`), delivers findings/notifications, expires stale watch-list entries (`expire_stale_entries`) [DONE: #7], compacts the Anomaly log to its keep budget (`compact_anomaly_log`) [DONE: #40], stamps the per-entry `last_notified` clock for entries notified this patrol (`stamp_last_notified`) [DONE: #35], stamps the frontmatter (`stamp_wiki`), then warns on soft-budget overshoot (`wiki_budget_warnings` on the final wiki, stderr — post-stamp, rule 29) [DONE: #40], writes the wiki atomically, commits + pushes it (wiki git persistence — `project-structure.md`) [#3: DONE; validation/repair/stamp: DONE: #5]; notification delivery is filtered first (`filter_state_change_notifications` over the on-disk and final wiki) and the rule-4c digest appended last [DONE: #35]; an empty `updated_wiki` on arrival (post-retry) logs one stderr warning naming it before delivery — the no-op is never silent [DONE: #51]; after that warning the tail runs the #60 safety net: one wiki-repair pass (rule 7d), then the minimal-write fallback — the accepted-write tail on the old wiki, no-op without a frontmatter block, one console line [DONE: #60]; a `None` report (total failure) warns, sends one FAILURE notification, and runs the rule-31 fallback directly — the wiki-repair pass skipped (rule 33) [DONE: #66] |

## Data Flow

`run_patrol`'s accumulated `messages` → `shape_report(model, messages, budget_cut)`:

1. `model.with_structured_output(PatrolReport, method="function_calling")` — a
   separate tool-free model call.
2. Prompt = compacted conversation + user message (`prefix + REPORT_CONTRACT`)
   — `compact_messages(messages)` in every mode (rule 7e) [DONE: #66]; the
   report and validation-repair modes append the rule-29a size note to the
   attempt's final user message (after the contract on the first attempt,
   after the corrective on a retry), a blank line between — the on-disk
   wiki's count for the report pass, the rejected wiki's for the validation
   repair, none for the wiki-repair pass [DONE: #67]; the prefix
   differs by `budget_cut`: "completed and wrote its final summary" vs "CUT OFF …
   commands marked 'not executed' never ran. Report only on what was actually
   observed."
3. On exception or empty result: append "That was not a valid PatrolReport. Produce
   it now." and retry once — the rule-29a size note is appended last on the
   retry prompt too (it describes the wiki-in-play, not the response), so the
   retry prompt also ends with the note, exactly once [DONE: #67]. Two failures → `None` — and the tail's `None` leg
   warns, notifies (FAILURE), and minimal-writes (rule 33) [DONE: #66]. Every
   attempt-failure log line carries the attempt's elapsed seconds, and an
   exception's reason passes `classify_exception` (rule 7f) [DONE: #66]. A
   parsed result whose
   `updated_wiki` is empty is equally a failed attempt
   (`reporting-notifications.md` rule 7b): its corrective message names the
   empty wiki and demands the full content; a still-empty retry returns the
   report — the caller delivers its findings and warns. Across failure
   compositions the pass returns the last result that parsed — a wiki-less
   report included — and `None` only when no attempt parsed (rule 7b)
   [DONE: #51]. The pass invokes the structured output with
   `include_raw=True`, so an unparsed result is observed alongside its raw
   response — and the failure reason is classified from it:
   `empty result (truncated at max_tokens)` vs `empty result (no tool
   call)`, by the pure `classify_empty_result` (rule 7c); exceptions and
   structured-output parsing errors keep their own message as the reason
   [DONE: #63].
4. `report_fields` normalizes (pydantic → `model_dump`; dict → `.get()` with falsy
   notifications filtered).
5. Wiki gate (#5): a non-empty `updated_wiki` is checked by `validate_wiki`
   (pure; PyYAML parses the frontmatter; the recheck check judges Watch-list
   `recheck` pairs — required — and Anomaly-log ones — at most one — with the
   guard's refusal grammar [DONE: #6]). Invalid → one repair pass:
   `shape_report` re-invoked with `repair=(rejected_wiki, errors)`, whose user
   message states the wiki failed validation and carries the errors + rejected
   wiki (plus the rejected wiki's budget warning when it is over the soft
   budget [DONE: #40]). The repaired report replaces the first wholesale when its wiki
   validates; otherwise the old wiki stands, a warning logs, and the first
   report's findings/notifications deliver. An empty `updated_wiki` — by then a
   twice-failed attempt, already retried in step 3 — reaches no write and logs
   the empty-wiki warning [DONE: #51] [DONE: #60 — the tail then runs the
   #60 safety net: one wiki-repair pass (`wiki_repair=(old_wiki, findings,
   notifications)`, rule 7d; its findings are never delivered), then the
   minimal-write fallback — the accepted-write tail (steps 7–8) on the old
   on-disk wiki with the observed report's findings delivered, skipped
   entirely when the old wiki has no frontmatter block
   (`wiki-memory.md` rules 30–32)].
6. Delivery (#5): findings printed (severity column; evidence by default —
   `--quiet` suppresses [DONE: #27]) and `notify` called once with the
   report's notifications, `notify_type` from `report_notify_type` (rules
   10a–10c) [DONE: #9] —
   both after the validation/repair decision resolves and before the physical
   write, so the final report is the one delivered [DONE: #5]. Notification
   dedup (#35, rule 4a) runs first, but only when a model-authored accepted
   wiki exists (`new_wiki` from the report or the wiki-repair pass; `old_wiki`
   is the on-disk wiki, or the seed when the file is absent, rule 14):
   `filter_state_change_notifications(proposed, old_wiki, new_wiki)` drops
   standing unchanged tagged messages (count to stderr) and returns the
   notified ids; `digest_notifications(old_wiki, new_wiki, today=…,
   digest_after=NOTIFY_DIGEST_AFTER, exclude_ids=notified_ids,
   patrol_count=…, stale_after=STALE_AFTER)` appends at most one digest last.
   The minimal-write / keep-old-wiki legs deliver every proposed message
   unfiltered and append no digest (fail-open: no new state to diff).
   Both functions are pure, no model calls [DONE: #35].
7. Expiry (#7): before the stamp, `expire_stale_entries(new_wiki, on-disk wiki,
   patrol_count, STALE_AFTER)` maintains the per-entry `verified_patrol` clock
   (new id or advanced `last_verified` → the current write's count, else carried,
   missing/garbled → grace) and flips watching Watch-list entries whose clock is
   `STALE_AFTER` writes behind into the end of the Anomaly log, each with an
   `auto-expired` annotation line and a console log line — after delivery, so
   the findings block and notifications precede any expiry line on the console
   [DONE: #7].
7a. Compaction + budget warning (#40): after expiry and before the stamp,
   `compact_anomaly_log(new_wiki, WIKI_ANOMALY_KEEP)` prunes resolved/expired
   Anomaly-log entries beyond the last `WIKI_ANOMALY_KEEP` (20) in document
   order — whole items, watching entries kept, one console line per prune
   naming the id and the budget; after the stamp, `wiki_budget_warnings` on
   the final wiki prints one stderr warning when it is over
   `WIKI_CHAR_BUDGET` — the write, commit, and push proceed regardless (the
   budget is soft) [DONE: #40].
7b. Notify stamp (#35): between compaction and the frontmatter stamp,
   `stamp_last_notified(new_wiki, notified_ids, today)` rewrites or appends
   the process-owned `last_notified` date on every entry a delivered
   notification named (state-change ids plus digest ids). It is the digest
   cadence's state and runs only on a model-authored accepted write — the
   minimal-write/keep-old-wiki legs have no new state and stamp nothing
   [DONE: #35].
8. Stamping (#5): before the write, `stamp_wiki` rewrites the process-owned
   frontmatter fields — `patrol_count` (on-disk value + 1, legacy wikis count 0),
   `last_patrol` (patrol-start timestamp), `cluster_id` (`PATROL_CLUSTER_ID`).
9. Persistence: the wiki written atomically and committed to the wiki repo if
   `updated_wiki` non-empty (push when `PATROL_WIKI_REMOTE` is set) [DONE: #3],
   token totals printed.

## Key Abstractions

- **Shape pass** (same pattern as the OLS agentic sandbox): the agent explores with
  natural tool choice and ends on its own; this pass converts the conversation —
  including the patrol's plain-text final summary — into the typed report. One code
  path serves natural termination and budget cut; no salvage special-case.
- **Two models, one seam:** the agent and the shape pass use separate
  `ChatOpenAI` instances from the same `make_model` seam, differing in
  request timeout — exploration 300 s, structured calls 900 s
  (`reporting-notifications.md` rule 8a; see `agent-engine.md`)
  [DONE: #49 — was one shared instance] — and in completion-token bound:
  exploration 16000, structured calls 64000
  (`reporting-notifications.md` rule 8b) [DONE: #63 — was one flat
  16000].

## Integration Points

| Consumer | Provider | Mechanism |
|---|---|---|
| `shape_report` | langchain `with_structured_output` | function-calling schema binding to `PatrolReport` |
| `run_patrol` | `shape_report` | message history + `budget_cut` flag |
| `run_patrol` | `filter_state_change_notifications`, `digest_notifications`, `stamp_last_notified` | proposed notifications + old/new wikis → delivered messages + per-entry notify clock (rules 4a–4d) [DONE: #35] |
| `notify` | apprise | `Apprise` instance per invocation, one `notify` per message, `notify_type` from `report_notify_type` (rules 10a–10c) [DONE: #9] |
| `run_patrol` | wiki file | `write_wiki_atomic` → `commit_wiki` → `push_wiki`, only when `updated_wiki` is non-empty and skeleton-valid (the gate, step 5; delivery, expiry, stamp, steps 6–8) [#3: DONE; gate: DONE: #5; expiry: DONE: #7] |

## Implementation Notes

- `method="function_calling"` — the pass itself must be tool-free; it must never
  re-enter the agent loop.
- `report_fields` is defensive because `with_structured_output` can return a pydantic
  model or a plain dict depending on the path; anything else yields empty data
  rather than crashing.
- Real-run economics for context: patrol #7 (shape pass, auto tool choice)
  self-terminated at 34 turns with 215k input tokens vs patrol #6 (forced tool
  choice) at ~1.02M — 4.7× cheaper.
- No report → wiki untouched (rule: never let a failed reporting pass corrupt
  memory) — and no commit is created: history advances only on wiki writes
  [DONE: #3]. [DONE: #66 — superseded by rule 33 for the total-failure
  leg: a no-report patrol warns, sends the FAILURE notification, and
  minimal-writes the old wiki through the accepted-write tail (ADR 0024);
  "no commit" now describes only the legacy no-frontmatter legs of
  rules 31/33.] The same degradation applies to a wiki that fails skeleton
  validation and repair: keep the old wiki, warn, deliver the findings
  [DONE: #5].
- The validator is pure and the repair bounded: at most one repair pass per
  patrol (one extra model-call round); the common case (valid first write) adds
  no model calls [DONE: #5].
- The length budget is deliberately soft (#40): it never rejects a wiki — the
  anomaly-log keep budget is the mechanical bound, the character budget the
  drift alarm (rule 29) [DONE: #40].
- The empty-wiki classification (#51) is process-level by design: the
  pydantic field keeps its lenient default so a wiki-omitting response
  still parses and its findings survive — the parser never enforces the
  requirement, the named retry and the stderr warning do. Zero findings
  alone never triggers it (a clean cluster is legal); the classification
  keys on the wiki field [DONE: #51].
- The empty-result reason classification (#63) is read-only diagnostics:
  `include_raw=True` changes how the pass observes a failure, never what
  it does about one — the retry, the corrective message, and the
  keep-old-wiki outcomes are untouched; only the reason string in the
  attempt-failure log line gains its cause [DONE: #63]. Envelope
  handling is defensive, like `report_fields`: a result that is a dict
  carrying a `parsed` key is the include_raw envelope (its `parsed` is
  the report, its `raw` feeds the classifier); anything else is the
  parsed value itself — older stubs and direct pydantic results keep
  working.
- The compaction (#66, rule 7e) is input-side only and uniform: it applies
  identically to every shape-pass mode (report, repair, wiki-repair), never
  touches the pass's own retry correctives, and never mutates the caller's
  `messages` list (the exploration conversation stays intact for pod-log
  visibility). Truncation is the only lossy dimension — order, roles, and
  all non-tool text (the patrol's final summary included) are preserved —
  so the pass's behavior contract is unchanged; only what it carries
  shrinks [DONE: #66].
- The failure-timing/classification half (#66, rule 7f) is, like #63,
  read-only diagnostics plus one new tail behavior (the `None` leg's
  notification and minimal write, rule 33); `classify_exception` is
  string-based because the SDK's exception types are not importable
  guarantees across langchain-openai versions [DONE: #66].
- The size note (#67, rule 29a) is instruction-only feedback: it changes
  what the model is told, never what the patrol accepts. It is computed
  from the content in play — never from the model's own response — and
  appended after the contract so the number is the last thing the model
  reads before emitting the report. The on-disk count comes from the
  `wiki` `run_patrol` loaded at start (the seed when the file is absent),
  passed to `shape_report` as `current_wiki`; the validation-repair mode
  uses its `rejected_wiki` argument; the wiki-repair mode appends none
  (its ask is a minimal merge). Rejected process-side prose compaction is
  ADR 0025 [DONE: #67].
