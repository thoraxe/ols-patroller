# Reporting Pipeline (shape pass)

How the finished patrol conversation becomes the typed `PatrolReport`, and how the
report is delivered. The behavioral contract is in `what/reporting-notifications.md`.

## Module Map

| Symbol | Responsibility |
|---|---|
| `REPORT_CONTRACT` | Instructions appended for the reporting pass (field semantics, wiki skeleton format: frontmatter keys, section set, entry metadata line [DONE: #5]; the Watch-list recheck requirement + its tool-argument form [DONE: #6]; the staleness clock's model-facing half — `verified_patrol` is process-stamped, `last_verified` advances only on actual re-verification, unverified watching entries auto-expire [DONE: #7]) |
| `shape_report` | Tool-free structured-output pass with one schema retry; `repair=(rejected_wiki, errors)` re-invokes it as the wiki-validation repair pass [DONE: #5] |
| `report_fields` | Normalizes the result (pydantic model or dict) into plain data |
| `notify` | Console `🔔 NOTIFY` + optional Slack webhook post |
| `run_patrol` (tail) | Validates the report's wiki against the skeleton (one repair pass on failure, `validate_wiki`), delivers findings/notifications, expires stale watch-list entries (`expire_stale_entries`) [DONE: #7], stamps the frontmatter (`stamp_wiki`), writes the wiki atomically, commits + pushes it (wiki git persistence — `project-structure.md`) [#3: DONE; validation/repair/stamp: DONE: #5] |

## Data Flow

`run_patrol`'s accumulated `messages` → `shape_report(model, messages, budget_cut)`:

1. `model.with_structured_output(PatrolReport, method="function_calling")` — a
   separate tool-free model call.
2. Prompt = full conversation + user message (`prefix + REPORT_CONTRACT`); the prefix
   differs by `budget_cut`: "completed and wrote its final summary" vs "CUT OFF …
   commands marked 'not executed' never ran. Report only on what was actually
   observed."
3. On exception or empty result: append "That was not a valid PatrolReport. Produce
   it now." and retry once. Two failures → `None`.
4. `report_fields` normalizes (pydantic → `model_dump`; dict → `.get()` with falsy
   notifications filtered).
5. Wiki gate (#5): a non-empty `updated_wiki` is checked by `validate_wiki`
   (pure; PyYAML parses the frontmatter; the recheck check judges Watch-list
   `recheck` pairs — required — and Anomaly-log ones — at most one — with the
   guard's refusal grammar [DONE: #6]). Invalid → one repair pass:
   `shape_report` re-invoked with `repair=(rejected_wiki, errors)`, whose user
   message states the wiki failed validation and carries the errors + rejected
   wiki. The repaired report replaces the first wholesale when its wiki
   validates; otherwise the old wiki stands, a warning logs, and the first
   report's findings/notifications deliver.
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
- **Shared model:** the same `ChatOpenAI` instance is used for the agent and the
  shape pass.

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
