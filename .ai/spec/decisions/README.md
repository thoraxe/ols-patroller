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
