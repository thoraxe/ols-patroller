# Agent Engine (deepagents wiring)

How the patrol loop is built on upstream `deepagents`, how the budget works, and the
gotchas an agent must know before touching this code.

## Module Map

| Symbol | Responsibility |
|---|---|
| `make_model` | `ChatOpenAI` (any OpenAI-compatible endpoint; temp 0.3, max_tokens 16000, timeout 300 s) |
| `_disable_default_subagent` | Registers a `HarnessProfile` disabling deepagents' general-purpose `task` subagent |
| `make_kubectl_tool` | Wraps the guarded `kubectl_readonly` as the single langchain tool |
| `run_patrol` | Builds the agent, streams it, accounts tokens, handles budget cut, invokes the shape pass |
| `FIXTURES` / `fixture_for` | Dry-cluster substitution inside the tool |

## Data Flow

`run_patrol` → `create_deep_agent(model, tools=[kubectl_readonly], system_prompt,
middleware=[FilesystemMiddleware(tools=["read_file"])])` → `agent.astream({messages},
config={"recursion_limit": max_steps * 2 + 12}, stream_mode="values")` → per-state
iteration over new messages (token accounting from `AIMessage.usage_metadata`; per-turn
previews of reasoning/tool calls/results — on by default, `--quiet` suppresses
[DONE: #27]) → natural end or `GraphRecursionError` →
shape pass over the accumulated message list.

## Key Abstractions

- **No backend:** the agent is created without a `backend`, so nothing touches disk
  and no shell/filesystem tools exist.
- **Middleware replacement:** deepagents' default `FilesystemMiddleware` (which would
  expose ls/write_file/edit_file/delete/glob/grep/execute) is replaced with
  `FilesystemMiddleware(tools=["read_file"])` on the in-memory `StateBackend`. This is
  the minimum deepagents allows — it refuses a FilesystemMiddleware without
  `read_file` because that backs tool-result offloading.
- **Harness profile:** `task` subagent disabled via `register_harness_profile` so the
  patrol stays single-threaded and the tool surface stays exactly one cluster tool.
- **Budget as recursion limit:** each LLM round ≈ 2 graph steps (model node + tools
  node), plus headroom for the shape pass → `recursion_limit = max_steps * 2 + 12`.
  `max_steps` counts LLM turns.

## Integration Points

| Consumer | Provider | Mechanism |
|---|---|---|
| `run_patrol` | deepagents `create_deep_agent` | agent graph construction + `astream` |
| agent graph | `make_kubectl_tool` | langchain `@tool` (single tool) |
| `run_patrol` | `shape_report` | message history handoff (see `reporting-pipeline.md`) |
| agent + shape pass | `make_model` | same `ChatOpenAI` instance shared |

## Implementation Notes

- **No in-agent response format — load-bearing.** An earlier revision passed
  `ToolStrategy(PatrolReport)` to `create_deep_agent`; that forces
  `tool_choice="any"` on every turn, which removes the model's natural stopping
  signal. Under it GLM never self-terminated (every real patrol ran to the budget;
  patrol #6: 36 turns, ~1.02M input tokens). Removed in 766c21f; see
  `decisions/0002-shape-pass-not-tool-strategy.md`.
- **`_disable_default_subagent` uses private deepagents helpers**
  (`deepagents._models.get_model_provider/get_model_identifier`). It registers the
  profile under both the provider key and `provider:identifier` so lookup resolves
  either way, and degrades gracefully — if internals drift, the default subagent
  simply stays enabled; it would inherit the same read-only middleware and single
  tool, so still safe, just noisier.
- **Budget-cut salvage:** on `GraphRecursionError`, if the run died between a tool
  call and its execution, synthetic `ToolMessage`s ("ERROR: step budget exhausted;
  command not executed.") close the dangling `AIMessage.tool_calls` so the history is
  valid input for the shape pass.
- **Verbose streaming detail:** reasoning content previewed at 400 chars, assistant
  content 600, tool results 220; `HumanMessage`s skipped.
- **GLM specifics:** needed >15 turns even single-node (budget raised 15→30,
  OLS-4439); patrol #7 self-terminated at 34/36 turns, apparently keying off the
  "~30 turns" soft target in the prompt — termination tuning is open (#10).
- **OUT_CAP fights:** hard truncation at 15k chars can burn turns when the model
  retries truncated dumps (patrol #6 fought node proxy stats) — motivates #10
  (smarter truncation) and #11 (refuse `--raw`) [DONE: #11]. Since #20 the
  truncation marker carries a narrowing hint (`--field-selector` / `-o jsonpath` /
  single-object drilldown) so the model recovers systematically instead of
  retrying the same dump [PLANNED: #20].
- **E2E verification:** the engine wiring — exact tool surface, subagent disable,
  budget-cut salvage, shape-pass handoff — is pinned by `tests/test_patrol_deepagents.py`,
  a scripted wire-level endpoint behind the `make_model` seam (see
  `project-structure.md` and `decisions/0009-wire-level-fake-endpoint-e2e.md`); it
  exists to catch deepagents/langchain drift on upgrades [DONE: #4].
