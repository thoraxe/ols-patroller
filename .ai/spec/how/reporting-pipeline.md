# Reporting Pipeline (shape pass)

How the finished patrol conversation becomes the typed `PatrolReport`, and how the
report is delivered. The behavioral contract is in `what/reporting-notifications.md`.

## Module Map

| Symbol | Responsibility |
|---|---|
| `REPORT_CONTRACT` | Instructions appended for the reporting pass (field semantics, wiki sections, tagging) |
| `shape_report` | Tool-free structured-output pass with one retry |
| `report_fields` | Normalizes the result (pydantic model or dict) into plain data |
| `notify` | Console `🔔 NOTIFY` + optional Slack webhook post |
| `run_patrol` (tail) | Prints findings, calls `notify`, writes the wiki atomically, commits + pushes it (wiki git persistence — `project-structure.md`) [DONE: #3] |

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
5. Consumers: findings printed (severity column; evidence in verbose), `notify`
   called per notification, wiki written atomically and committed to the wiki repo
   if `updated_wiki` non-empty (push when `PATROL_WIKI_REMOTE` is set) [DONE: #3],
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
| `run_patrol` | wiki file | `write_wiki_atomic` → `commit_wiki` → `push_wiki`, only when `updated_wiki` non-empty [DONE: #3] |

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
  [DONE: #3].
