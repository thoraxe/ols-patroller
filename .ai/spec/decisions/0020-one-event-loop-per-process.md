# 0020. One event loop per process in loop mode

Date: 2026-10-22 · Status: proposed

## Context

Overnight soak of `--loop --interval 21600` against a real cluster + real
endpoint (#51): the first patrol of the process succeeded; the second failed
immediately after its first tool call with "Event loop is closed"; the third
ran clean. Self-healing (the loop's catch-and-continue), observed once (n=1),
but structural: `main()` ran each patrol through a fresh `asyncio.run`, and
`asyncio.run` closes its loop on exit — something in the engine stack holds
loop-bound state (cached async client, keep-alive pool, surviving background
task) from the first patrol, and the second patrol trips over the closed
loop. The fake-model e2e cannot reproduce it (in-process endpoint, no real
async HTTP client, no idle gap). Alternatives: keep per-patrol loops and hunt
whichever library object holds the state (brittle, private internals); accept
the one skipped window per process start (leaves a 6 h hole on every pod
restart).

## Decision

Loop mode runs every patrol of the process on one process-lifetime event
loop: a single `asyncio.run` over a loop coroutine that awaits each patrol
(try/except per patrol, as today) and `time.sleep`s the interval between
patrols. `--once` keeps its single `asyncio.run` — nothing follows it in the
process, so nothing can be stranded.

## Consequences

Loop-bound state always references a live loop, independent of which library
held it — the fix does not depend on diagnosing the private internals. The
inter-patrol wait is a deliberate blocking `time.sleep`: the loop has no
concurrent work while idle, and a blocking sleep is trivially
ctrl-c-interruptible. The e2e pins the structure (loop identity across two
patrols through `main() --loop`) rather than the unreproducible failure
itself. #14 (CRD dispatcher) will replace this process loop eventually; the
one-loop property should carry over.
