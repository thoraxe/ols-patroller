# 0016. Sweep efficiency is taught, not guard-enforced

Date: 2026-10-21 · Status: proposed (issue #20)

## Context

#20 cuts the token cost of broad queries — first-patrol mapping on a dry wiki
especially — by steering the agent toward field selection and layered sweeps
instead of flat enumeration (`get pods -A`) and default table dumps. All the
efficient patterns (`--field-selector`, `-o jsonpath`, custom-columns,
`--no-headers`) are already guard-permitted for non-secret resources, and the
expensive anti-patterns (flat pod enumeration on a big cluster, full `-o json`
on lists) are legal read-only commands. The tempting enforcement move — having
the guard refuse flat enumeration or list dumps — would turn the guard into a
cost instrument alongside its safety role.

## Decision

Cost steering lives in the prompt (the layered-sweep teaching,
`patrol-engine.md` rules 3a–3e) and in output shaping (the truncation marker's
recovery hint, `kubectl-guard.md` rule 11) — never in the guard's refusal
grammar. The guard refuses only for safety (verb whitelist, secret values,
raw paths); it never refuses a legal read for cost reasons.

## Consequences

- The guard stays a pure safety instrument, and its refusal grammar remains
  the single source of truth for recheck validity (ADR 0013): a cost-motivated
  refusal would retroactively invalidate existing wiki `recheck` citations —
  commands that re-verified fine last patrol — with zero safety gain.
- A model that ignores the teaching still gets correct answers (cost suffers,
  correctness never). Process-level cost budgets, if ever needed, are #10's
  concern (turn/token budgets), not refusal rules.
- Dry-cluster keeps answering the flat forms (honest demo data); the one
  deliberate exception is the unfiltered event firehose, which no fixture
  serves (`patrol-engine.md` rule 13a).
