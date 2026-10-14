# 0003. Git-backed wiki instead of an MCP memory service

Date: 2026-10-13 · Status: accepted (issue #3; MCP end-state retired, return-triggers
recorded in #16)

## Context

The original imagined end-state used an MCP memory service for agent memory. But the
wiki's actual access pattern is single-writer, whole-file read/write, low frequency —
problems git solves trivially, while an MCP service adds a deployed component for
capabilities nothing uses.

## Decision

The wiki becomes a git repo on the PVC: `git init` if absent; after each patrol one
commit (`patrol N: X findings, Y warn`) with per-command user config (non-root
safe); atomic write (temp file + rename) before committing; optional
`PATROL_WIKI_REMOTE` push that retries but **never blocks the patrol loop**
(local-first).

## Consequences

- Irreversibility insurance: a bad rewrite is revertible; the wiki survives cluster
  death (post-mortems).
- Measurable compounding: update-vs-create ratio and drift detection feed the
  planned `--wiki-stats` health numbers (#8).
- Enables the multicluster story via git federation (#13) with no central memory
  service.
- Adds a `git` dependency to the in-cluster image (#1).
- An MCP memory facade returns only on a concrete trigger (concurrent writers,
  retrieval at scale, or OLS productizing a memory service) — and then as a thin
  facade over the same files, not a migration (#16).
