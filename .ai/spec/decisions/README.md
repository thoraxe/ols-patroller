# Decisions

Architecture decision records (ADRs). One file per decision, named `NNNN-slug.md`
(zero-padded sequence + short slug). Seeded from decisions discovered during spec
exploration; new significant decisions get a new record.

## Format

```markdown
# NNNN. <Title>

Date: YYYY-MM-DD · Status: proposed | accepted | superseded by NNNN

## Context
<The forces at play; what problem demanded a decision>

## Decision
<What was chosen, in one or two sentences>

## Consequences
<What becomes easier/harder; follow-ups and their tickets>
```

## Index

| ADR | Title |
|---|---|
| [0001](0001-deepagents-engine.md) | deepagents as the patrol engine |
| [0002](0002-shape-pass-not-tool-strategy.md) | Shape pass instead of in-agent ToolStrategy |
| [0003](0003-git-backed-wiki.md) | Git-backed wiki instead of an MCP memory service |
| [0004](0004-readonly-with-delegated-remediation.md) | Read-only patrol with delegated remediation |
| [0005](0005-pinned-requirements-for-image.md) | Pinned requirements.txt for the image; uv header stays for dev |
| [0006](0006-cluster-reader-binding.md) | Bind to the built-in cluster-reader, not a bespoke read-only role |
| [0007](0007-scc-fsgroup-writable-wiki.md) | /wiki writability via the SCC-assigned fsGroup |
| [0008](0008-ci-published-image.md) | Post-merge CI publishes the image; the manifests deploy it |
| [0009](0009-wire-level-fake-endpoint-e2e.md) | Wire-level fake endpoint for the e2e, not a class-level fake model |
| [0010](0010-release-retag-not-rebuild.md) | Release tags are registry-side retags of the sha image, not rebuilds |
| [0011](0011-wiki-skeleton-stamped-counters.md) | Wiki skeleton: validated structure, stamped counters, one repair pass |
| [0012](0012-opt-in-deploy-key-wiring.md) | Opt-in deploy-key wiring via a committed example patch |
| [0013](0013-recheck-guard-bounded-citations.md) | Recheck commands are guard-bounded citations |
| [0014](0014-process-enforced-stale-entry-expiry.md) | Stale-entry expiry is process-enforced, counted in accepted writes |
| [0015](0015-wiki-stats-offline-prose-churn.md) | Wiki stats: an offline command; churn is prose-keyed over a bounded git window |
| [0016](0016-sweep-taught-not-guard-enforced.md) | Sweep efficiency is taught, not guard-enforced |
| [0017](0017-working-set-wiki-not-log.md) | The wiki is a working set; git history is the archive |
| [0018](0018-reporting-pass-own-request-bound.md) | Per-call-shape request bounds: 300 s exploration, 900 s structured reporting |
| [0019](0019-empty-wiki-is-a-failed-report.md) | An empty `updated_wiki` is a failed report, not a no-op signal |
| [0020](0020-one-event-loop-per-process.md) | One event loop per process in loop mode |
| [0021](0021-reporting-pass-own-token-bound.md) | Per-call-shape completion-token bounds: 16k exploration, 64k structured reporting |
| [0022](0022-minimal-write-advances-the-clock.md) | The minimal-write fallback advances the patrol clock |
