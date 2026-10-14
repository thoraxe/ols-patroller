# 0004. Read-only patrol with delegated remediation

Date: 2026-10-13 · Status: accepted (direction; implementation is #12, in-cluster
boundary is #2)

## Context

An SRE agent that acts directly needs powerful RBAC and human trust — prior art
either advises only (no action) or acts directly (needs broad permissions). The
prior-art survey (`docs/prior-art.md`) identified "delegate action to a governed
AgenticRun" as this project's structural advantage.

## Decision

The patrol stays read-only forever: it is the eyes and memory. Actionable findings
(warn/crit with a known remediation) hand off to a governed AgenticRun — sandbox,
approval gate, derived RBAC, audit — via API surface only (`oc create agenticrun`,
templated like the alerts-adapter templates alerts). In-cluster, the patrol pod's SA
RBAC (`cluster-reader`) is the real enforcement boundary; the `kubectl_readonly`
guard is defense-in-depth.

## Consequences

- The safety story is structural (no write path exists) rather than behavioral
  (prompting the model to behave).
- Remediation quality becomes the AgenticRun pathway's problem, not this repo's;
  the patroller must never grow local mutation "just this once".
- Remediation attempts can be harvested back into the wiki as memory
  ("we tried X, verification said Y") once #12 lands.
- Integration constraint: API-surface-only, no OLS code imports — the patroller
  stays standalone (see `constraints.md`).
