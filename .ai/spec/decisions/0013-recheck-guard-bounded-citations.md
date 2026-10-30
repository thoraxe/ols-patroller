# 0013. Recheck commands are guard-bounded citations

Date: 2026-10-16 · Status: proposed (issue #6)

## Context

#6 makes every Watch-list entry carry a `recheck:` command — the wiki's citation
made regenerable (OKF/AIX rule 2 for evidence that can be re-derived). The
minimal contract would check presence only. But the wiki is model-authored and
is the planned input to automated re-verification (#7 expiry checks it, a future
lint-only mode would execute it, humans copy-paste it): a hallucinated
`delete pod …` or a `--raw` dump in a recheck would be a landmine in exactly the
surface meant to be executed later. Meanwhile the guard already defines,
unit-checks, and maintains the exact grammar of "a command `kubectl_readonly`
accepts" (`kubectl-guard.md` rules 2–6b).

## Decision

A valid recheck is a command the guard would accept, not refuse: the wiki
validator checks the recheck value against the guard's pre-execution refusal
grammar (parseability, verb whitelist, Secret-access ban, raw-path refusal,
local-file/credential-flag refusal) through the same code path the guard itself
uses — single source of
truth, never by executing the command. The value is the exact tool-argument
form: everything after the CLI name, no leading `kubectl`/`oc` token.

## Consequences

- The wiki's citations are bounded by the same read-only channel as the patrol
  itself: memory can never teach or require a command outside safety-model
  layer 1. A future lint-only mode can execute rechecks through
  `kubectl_readonly` with no new safety analysis.
- Guard grammar changes now affect wiki validation (a newly refused command
  shape also invalidates rechecks citing it). That coupling is the point: one
  definition of "read-only command", two consumers — the guard's tests and the
  wiki-schema tests pin the same grammar from both sides.
- The validator stays pure (no subprocess); the refusal grammar is extracted
  from `kubectl_readonly` into a shared pure predicate (`guard_refusal`,
  `how/project-structure.md`) — a refactor with no guard behavior change.
- The strict tool-argument form costs an occasional repair pass when the model
  prefixes `kubectl`/`oc`; the report contract instructs the exact form and the
  repair error names the fix, so drift is a one-round repair.
- Hand-edited wikis are outside the gate by design: the validator judges the
  model's `updated_wiki`, not operator edits (the patroller is the single
  writer; the dedicated-wiki-directory constraint — rule 4a and the
  Constraints section — already covers pointing it at a directory someone
  cares about).
