# 0009. Wire-level fake endpoint for the e2e

Date: 2026-10-15 · Status: proposed

## Context

Issue #4 commits the fake-model e2e into the repo. Its stated purpose is guarding
the engine contract against deepagents upgrades and making the repo
contributor-friendly. The original ad-hoc e2e (run from `/tmp` during the engine
swap) drove the graph with a langchain `GenericFakeChatModel` subclass that
overrode `bind_tools` and `with_structured_output` — which bypasses exactly the
machinery where upstream drift bites: `ChatOpenAI` function-calling, the openai
SDK wire format, and the structured-output parsing the shape pass depends on. A
class-level fake can stay green while the real model stack breaks.

## Decision

The in-repo e2e scripts the **wire, not the model class**: a real `ChatOpenAI`
is pointed (via `http_client`/`http_async_client` on an `httpx.MockTransport`)
at an in-process OpenAI-compatible endpoint that serves scripted chat
completions — exploration turns from a script, the `PatrolReport` tool call when
a request forces `tool_choice` on the schema (the shape pass). The injection
seam is `make_model` (monkeypatched); every request body is recorded so tests
assert on what the model actually sees. Rationale: the safety and engine
contracts are observable at the wire (tool surface offered, invalid-tool
rejections, `REFUSED:` results, shape-pass prompts), so the tripwire should sit
there.

## Consequences

The e2e exercises the full production model stack end-to-end and catches drift
in the openai SDK / langchain function-calling path, at no new dependency cost:
`httpx` is already in the lock (transitively via langchain-core et al.) and the
explicit dev-group pin just makes the tests' direct import honest. Note that
`openai` 3.x itself depends on `httpx2`; its legacy-`httpx.Client`
compatibility layer is what accepts the MockTransport-wrapped client, so the
injection keeps working despite the SDK's migration. Class-level fakes
remain available for narrower unit needs; the wiki-git wiring assertions reuse
the same endpoint to run complete patrols through `main()`.
