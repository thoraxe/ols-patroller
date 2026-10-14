# 0001. deepagents as the patrol engine

Date: 2026-10-13 · Status: accepted (commit 14caf93, OLS-4439)

## Context

The PoC began with a hand-rolled raw-requests agent loop. The OLS agentic sandbox
standardized on [langchain-ai/deepagents](https://github.com/langchain-ai/deepagents);
maintaining a bespoke loop meant diverging from the engine the surrounding ecosystem
uses, and losing its graph/streaming/middleware features.

## Decision

Swap the patrol engine to upstream deepagents (`create_deep_agent`), depending on it
**directly** — no OLS code is imported. Constrain its defaults: no backend,
read-file-only FilesystemMiddleware, default `task` subagent disabled.

## Consequences

- Engine alignment with the OLS agentic sandbox without any OLS code dependency —
  the patroller stays standalone/open-source.
- The default deepagents tool surface (shell, filesystem writes, execute, subagents)
  must be actively dismantled; the safety story depends on that wiring
  (see `how/agent-engine.md`).
- Exposure to upstream drift, including private `_models` helpers used to disable
  the default subagent — guarded by graceful degradation and (planned) in-repo e2e
  tests (#4).
