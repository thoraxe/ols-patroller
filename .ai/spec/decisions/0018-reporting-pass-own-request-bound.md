# 0018. The reporting pass gets its own request bound

Date: 2026-10-22 · Status: proposed

## Context

Real-cluster validation of v0.2 (#49): one `timeout=300` sat on the whole
`ChatOpenAI` client, so every request — exploration turns and the structured
reporting/repair calls — shared one bound. But the call shapes are wildly
different: an exploration turn is small and fast, while the single tool-free
structured call carries the entire patrol conversation (evidence: ~100k tokens
in, up to 16k out) and legitimately runs 4–6+ minutes on reasoning-capable
models. A fresh-cluster first patrol (necessarily a full sweep, week-stale
wiki, 59 tool calls) timed out at 300 s on attempt 1 in-cluster; attempt 2
succeeded. Because the wiki write is the only thing that shortens subsequent
patrols, a patrol that cannot finish its reporting pass never converges the
patroller's memory — and a fresh cluster never bootstraps.

Design space: (1) raise the global bound — simplest, but a genuinely hung
endpoint then takes 900 s to detect on every exploration turn too; (2) an env
knob with a raised default — operator-tunable, but the default still has to be
picked and the mismatch is structural, not deployment-specific; (3) per-call-
shape bounds.

## Decision

Per-call-shape bounds, hard-coded (not env-configurable): exploration turns
keep `REQUEST_TIMEOUT` (300 s — snappy failure on a hung endpoint); the
structured reporting call and its repair pass get `REPORT_TIMEOUT` (900 s).
The request timeout is a client-level property in `langchain-openai`, so this
is two `ChatOpenAI` instances from the same `make_model` seam.

## Consequences

Hung-endpoint detection stays fast where calls are cheap (exploration) and
gets headroom where calls are fat (reporting). A hung endpoint during a
structured call now takes up to 900 s to detect — accepted: that call is rare,
and its input cost is already sunk. The e2e seam passes the bound through
(`ScriptedEndpoint.model` accepts the `timeout` kwarg), so the two-model
wiring is testable. If deployments ever need different numbers, that knob
belongs to #10 (cost control), not here.
