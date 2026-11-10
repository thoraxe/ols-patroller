# Agent Engine (deepagents wiring)

How the patrol loop is built on upstream `deepagents`, how the budget works, and the
gotchas an agent must know before touching this code.

## Module Map

| Symbol | Responsibility |
|---|---|
| `make_model` | `ChatOpenAI` (any OpenAI-compatible endpoint; temp 0.3; `timeout` and `max_tokens` parametrized per call shape — `REQUEST_TIMEOUT` 300 s / `REQUEST_MAX_TOKENS` 16000 defaults, `REPORT_TIMEOUT` 900 s / `REPORT_MAX_TOKENS` 28000 for the shape pass) [DONE: #49; max_tokens split: DONE: #63 — was a flat 16000; DONE: #88 — the reporting bound drops 64000 → 32000; DONE: #97 — drops again to 24000 with the cheap first attempt; DONE: #101 — retunes 24000 → 28000] |
| `_disable_default_subagent` | Registers a `HarnessProfile` disabling deepagents' general-purpose `task` subagent |
| `make_kubectl_tool` | Wraps the guarded `kubectl_readonly` as the single langchain tool |
| `run_patrol` | Builds the agent, streams it, accounts tokens, handles budget cut (step budget and the #10 token soft-stop), invokes the shape pass |
| `FIXTURES` / `fixture_for` | Dry-cluster substitution inside the tool |

## Data Flow

`run_patrol` → `create_deep_agent(model, tools=[kubectl_readonly], system_prompt,
middleware=[FilesystemMiddleware(tools=["read_file"])])` → `agent.astream({messages},
config={"recursion_limit": max_steps * 2 + 12}, stream_mode="values")` → per-state
iteration over new messages (token accounting from `AIMessage.usage_metadata`; per-turn
previews of reasoning/tool calls/results — on by default, `--quiet` suppresses
[DONE: #27]) → natural end, `GraphRecursionError`, or the #10 token cut →
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
| agent + shape pass | `make_model` | two `ChatOpenAI` instances from the same seam: the agent's (exploration bound) and the shape pass's (reporting bound, shared by the report and repair calls) [DONE: #49 — was one shared instance] |

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
  "~30 turns" soft target in the prompt — the target is now decoupled from the
  hard budget as `SOFT_TARGET_TURNS` (20), specced by #10
  (`patrol-engine.md` rule 8a, ADR 0027) [DONE: #10].
- **Token soft-stop (#10, `patrol-engine.md` rule 8b):** `run_patrol` maintains
  `tokens_in` (already accounted from `usage_metadata`) and stops iterating
  `astream` once it exceeds the budget (`PATROL_TOKEN_BUDGET` /
  `--token-budget`, default 500,000; `0` disables) — the stop shares the
  `GraphRecursionError` path's semantics exactly: `budget_cut = True`, the
  dangling-tool-call salvage (the step-budget ToolMessage text, reused
  verbatim — rule 8b), the same shape pass, and a console warning naming
  consumed tokens and budget. Checked between streamed states, so an in-flight
  turn always completes — the cut never lands mid-turn [DONE: #10].
- **OUT_CAP fights:** hard truncation at 15k chars can burn turns when the model
  retries truncated dumps (patrol #6 fought node proxy stats) — motivates #10
  (smarter truncation) and #11 (refuse `--raw`) [DONE: #11]. Since #20 the
  truncation marker carries a narrowing hint (`--field-selector` / `-o jsonpath` /
  single-object drilldown) so the model recovers systematically instead of
  retrying the same dump [DONE: #20].
- **E2E verification:** the engine wiring — exact tool surface, subagent disable,
  budget-cut salvage, shape-pass handoff — is pinned by `tests/test_patrol_deepagents.py`,
  a scripted wire-level endpoint behind the `make_model` seam (see
  `project-structure.md` and `decisions/0009-wire-level-fake-endpoint-e2e.md`); it
  exists to catch deepagents/langchain drift on upgrades [DONE: #4].
- **Two models, two bounds** (#49; #63 extends one layer down): the request
  timeout and the completion-token bound are both client-level properties in
  `langchain-openai`, so the per-call-shape bounds
  (`reporting-notifications.md` rules 8a + 8b) mean two `ChatOpenAI`
  instances: `run_patrol` builds the agent's with
  `make_model(timeout=REQUEST_TIMEOUT)` (max_tokens defaults to
  `REQUEST_MAX_TOKENS`) and the shape pass's with
  `make_model(timeout=REPORT_TIMEOUT, max_tokens=REPORT_MAX_TOKENS)` (the
  repair call reuses the shape instance). The e2e seam passes the bounds
  through — `ScriptedEndpoint.model` accepts (and ignores) the `timeout` and
  `max_tokens` kwargs. Real-run evidence: a 59-tool-call fresh-cluster patrol's
  reporting call timed out at 300 s on attempt 1 and succeeded on attempt
  2; local shape passes ran 4–5 minutes [DONE: #49]. Token-bound evidence:
  a 43.5k-token report prompt spent all 16000 completion tokens on
  reasoning (`finish_reason=length`, zero content) on both attempts — no
  wiki write for ~14 patrols; the same run at a 64k reporting budget
  completed at ~20k completion tokens and committed
  [DONE: #63]. Ceiling evidence: at 64k the same pass then hit the model
  gateway's ~900 s wall clock at ~902 s on both attempts (≈71 output
  tok/s), so the reporting bound drops to 32000 — ~450 s of generation,
  still ~12k above the observed 20k completion, so #63's
  reasoning-exhaustion fix stays intact (ADR 0031, DONE: #88). Recidivism
  evidence (#97): the first attempt still 504ed at ~902 s on 3 of 5
  post-#88 patrols while the retry — `SHAPE_TOOL_KEEP_RETRY` (500) plus
  the brevity corrective — finished the full report in ~260 s every time,
  so #97 makes that shape the default: one `SHAPE_TOOL_KEEP` cap of 500
  for every attempt (the retry constant is retired), a pinned concision
  ask in `REPORT_CONTRACT`, and `REPORT_MAX_TOKENS` 32000 → 24000
  (ADR 0034, DONE: #97). The retry half (rule 7g) still rebuilds its
  base and re-states the contract after the ceiling corrective. Truncation
  evidence (#101): the post-#97 first attempt then exhausted the token
  bound instead of the clock — `out 24000 tokens, bound 24000 tokens` at
  289 s (~83 output tok/s), empty wiki, recovered by the rule-7b retry in
  ~230 s — so the bound retunes 24000 → 28000 (ADR 0036, DONE: #101):
  +4000 tokens of reasoning-variance headroom over the bound that was
  exhausted, still ~700–800 s at the ~35–40 output tok/s the #97 retry
  measured on this payload shape, i.e. inside the gateway window. Nothing
  else in the wiring changes — the shape pass keeps the single
  `make_model(timeout=REPORT_TIMEOUT, max_tokens=REPORT_MAX_TOKENS)` call,
  the repair pass keeps reusing that instance, and the bounds stay
  constants rather than env config.
