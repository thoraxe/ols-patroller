# Reporting Pipeline (shape pass)

How the finished patrol conversation becomes the typed `PatrolReport`, and how the
report is delivered. The behavioral contract is in `what/reporting-notifications.md`.

## Module Map

| Symbol | Responsibility |
|---|---|
| `REPORT_CONTRACT` | Instructions appended for the reporting pass (field semantics, wiki skeleton format: frontmatter keys, section set, entry metadata line [DONE: #5]; the Watch-list recheck requirement + its tool-argument form [DONE: #6]; the staleness clock's model-facing half — `verified_patrol` is process-stamped, `last_verified` advances only on actual re-verification, unverified watching entries auto-expire [DONE: #7]; the working-set expectations — the wiki is a working set (git history is the archive), summarize and prune within the soft character budget, the Anomaly log keeps the last `WIKI_ANOMALY_KEEP` closed entries [DONE: #40]; the wiki-required instruction — `updated_wiki` is required on every report, full content even when nothing needs changing, an empty one is never a valid answer [DONE: #51]) |
| `shape_report` | Tool-free structured-output pass with one schema retry; `repair=(rejected_wiki, errors)` re-invokes it as the wiki-validation repair pass [DONE: #5]; a parsed result with an empty `updated_wiki` is a failed attempt — retried with a corrective message naming it, a still-empty retry returns the report [DONE: #51]; a `None`/unparsed result's failure reason is classified from the raw response (`classify_empty_result`: truncated-at-max_tokens vs no-tool-call) [PLANNED: #63] |
| `report_fields` | Normalizes the result (pydantic model or dict) into plain data |
| `notify` | Console `🔔 NOTIFY` + optional Slack webhook post |
| `run_patrol` (tail) | Validates the report's wiki against the skeleton (one repair pass on failure, `validate_wiki`), delivers findings/notifications, expires stale watch-list entries (`expire_stale_entries`) [DONE: #7], compacts the Anomaly log to its keep budget (`compact_anomaly_log`) [DONE: #40], stamps the frontmatter (`stamp_wiki`), then warns on soft-budget overshoot (`wiki_budget_warnings` on the final wiki, stderr — post-stamp, rule 29) [DONE: #40], writes the wiki atomically, commits + pushes it (wiki git persistence — `project-structure.md`) [#3: DONE; validation/repair/stamp: DONE: #5]; an empty `updated_wiki` on arrival (post-retry) logs one stderr warning naming it before delivery — the no-op is never silent [DONE: #51] |

## Data Flow

`run_patrol`'s accumulated `messages` → `shape_report(model, messages, budget_cut)`:

1. `model.with_structured_output(PatrolReport, method="function_calling")` — a
   separate tool-free model call.
2. Prompt = full conversation + user message (`prefix + REPORT_CONTRACT`); the prefix
   differs by `budget_cut`: "completed and wrote its final summary" vs "CUT OFF …
   commands marked 'not executed' never ran. Report only on what was actually
   observed."
3. On exception or empty result: append "That was not a valid PatrolReport. Produce
   it now." and retry once. Two failures → `None`. A parsed result whose
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
   [PLANNED: #63].
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
   the empty-wiki warning [DONE: #51].
6. Delivery (#5): findings printed (severity column; evidence by default —
   `--quiet` suppresses [DONE: #27]) and `notify` called per notification —
   both after the validation/repair decision resolves and before the physical
   write, so the final report is the one delivered [DONE: #5].
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
  (`reporting-notifications.md` rule 8b) [PLANNED: #63 — was one flat
  16000].

## Integration Points

| Consumer | Provider | Mechanism |
|---|---|---|
| `shape_report` | langchain `with_structured_output` | function-calling schema binding to `PatrolReport` |
| `run_patrol` | `shape_report` | message history + `budget_cut` flag |
| `notify` | Slack incoming webhook | `requests.post` (15 s timeout, failure logged not raised) |
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
  [DONE: #3]. The same degradation applies to a wiki that fails skeleton
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
  attempt-failure log line gains its cause [PLANNED: #63]. Envelope
  handling is defensive, like `report_fields`: a result that is a dict
  carrying a `parsed` key is the include_raw envelope (its `parsed` is
  the report, its `raw` feeds the classifier); anything else is the
  parsed value itself — older stubs and direct pydantic results keep
  working.
