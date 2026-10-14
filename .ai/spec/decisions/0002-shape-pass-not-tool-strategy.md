# 0002. Shape pass instead of in-agent ToolStrategy

Date: 2026-10-13 · Status: accepted (commit 766c21f)

## Context

Passing `ToolStrategy(PatrolReport)` to `create_deep_agent` forces
`tool_choice="any"` on **every** turn. Under it, GLM never self-terminated — every
real patrol ran to the step budget (patrol #6: 36 turns, ~1.02M input tokens,
budget-forced report).

## Decision

Remove the in-agent response format entirely. The agent explores with auto tool
choice and ends on its own with a plain-text summary; a separate **tool-free
reporting pass** (`with_structured_output(PatrolReport, method="function_calling")`)
converts the finished conversation into the typed report. The pass runs identically
for natural termination and budget cut — one code path, no salvage special-case.
Same "shape pass" pattern as the OLS agentic sandbox.

## Consequences

- Patrol #7 self-terminated at 34 turns (33 tool calls + summary) with 215k input
  tokens — 4.7× cheaper than #6 — and the summary showed memory economy (wiki
  baselines verified instead of re-derived).
- Typed output now depends on a second model call; its quality depends on the
  conversation plus the patrol's own final summary.
- Termination behavior is model-dependent: the soft "~N turns" target in the prompt
  appears to anchor GLM's stopping point — tuning is open (#10).
