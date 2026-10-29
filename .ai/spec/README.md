# OLS Patroller — Specifications

OLS Patroller is a proactive, non-deterministic SRE agent ("resident SRE") for
OpenShift/Kubernetes clusters: on a schedule, an LLM agentically explores a cluster
**read-only**, validates reality against a persistent markdown **wiki** (its memory),
rewrites the wiki, and notifies a human (console / apprise sinks) about anything it thinks is
wrong. These specs cover the current single-file PoC (`patrol.py`) and its planned
evolution.

Tracked as [OLS-4439](https://redhat.atlassian.net/browse/OLS-4439) (research spike).
Roadmap work lives in [GitHub issues and
milestones](https://github.com/thoraxe/ols-patroller/issues) — spec files reference
issue numbers with `[PLANNED: #N]` markers.

## Structure

| Layer | Path | Purpose |
|---|---|---|
| **what/** | `.ai/spec/what/` | System contracts. What the system must do — behavioral rules, tool-guard contract, data formats, configuration surface. |
| **how/** | `.ai/spec/how/` | Codebase map. How the code is organized — module map, engine wiring, reporting pipeline. |
| **decisions/** | `.ai/spec/decisions/` | Architecture decision records (`NNNN-slug.md`). |
| | `.ai/spec/glossary.md` | Domain vocabulary. |
| | `.ai/spec/constraints.md` | Cross-cutting project constraints. |

## Scope

Covered: `patrol.py` (the entire implementation), its runtime artifacts
(the wiki directory — `wiki/cluster-wiki.md` and its git repo [DONE: #3]), the
in-cluster image artifacts (repo-root `Containerfile` + `requirements.txt`, #1),
the in-cluster deployment manifests (repo `manifests/` kustomize root + the
post-merge image CI at `.github/workflows/image.yml`, #2),
`docs/prior-art.md` positioning, and planned behavior tied to
GitHub issues.

Out of scope: the OpenShift Lightspeed (OLS) service itself, AgenticRun internals,
the contents of any observed cluster, and the k8sgpt/HolmesGPT ecosystems
(positioning only — see `docs/prior-art.md`).

## Audience

AI agents. Content is optimized for precision and machine consumption.

## Quick Start

| Task | Start here |
|---|---|
| Understand the system | `what/system-overview.md` |
| What one patrol does | `what/patrol-engine.md` |
| Read-only safety guarantees | `what/safety-model.md`, `what/kubectl-guard.md` |
| The wiki (agent memory) contract | `what/wiki-memory.md` |
| The in-cluster container image | `what/container-image.md` |
| Run it in-cluster (manifests + CI-published image) | `what/in-cluster-deployment.md` |
| Report & notification formats | `what/reporting-notifications.md` |
| Where code lives | `how/project-structure.md` |
| How the deepagents engine is wired | `how/agent-engine.md` |
| How the typed report is produced | `how/reporting-pipeline.md` |
| Why things are this way | `decisions/` |

## Cross-Reference

| what/ | how/ |
|---|---|
| `what/patrol-engine.md` | `how/agent-engine.md` |
| `what/reporting-notifications.md` | `how/reporting-pipeline.md` |
| `what/kubectl-guard.md`, `what/wiki-memory.md` | `how/project-structure.md` (all symbols live in `patrol.py`) |
| `what/container-image.md` | `how/project-structure.md` (Containerfile + `requirements.txt` path entries) |
| `what/in-cluster-deployment.md` | `how/project-structure.md` (`manifests/` + `.github/workflows/` path entries) |

## Conventions

- **Rule numbering:** behavioral rules are numbered sequentially within each what/
  file. Numbers are stable identifiers — do not renumber when a rule is removed (leave
  a gap) or inserted (use sub-numbers like 16a, 16b). This keeps external references
  (issue comments, PR descriptions) valid.
- **Planned changes lifecycle:** unimplemented behavior is marked `[PLANNED]` or
  `[PLANNED: #N]` inline next to the rule it affects (tickets are GitHub issues).
  Under the spec-first PR workflow (`AGENTS.md`), planned behavior is **fully
  specified before implementation**: `/skill:spec-first` expands the marker into
  complete, testable contract rules plus failing tests; a human approves the spec;
  a context-clear agent (`/skill:spec-implement`) implements and flips the marker to
  `[DONE: #N]`. `[DONE]` markers are cleanup candidates — remove them in a
  subsequent pass once the rule text fully reflects the shipped behavior. In Planned
  Changes tables, strike through completed entries (`~~#N~~`) rather than deleting
  them, so readers can see what changed recently.
- **Constraints:** component-specific constraints go in the relevant what/ file's
  Constraints section, co-located with behavioral rules. Cross-cutting project-wide
  rules live in `constraints.md`. Development conventions go in `AGENTS.md`.
- **Authority:** what/ specs are authoritative for behavior. how/ specs are
  authoritative for implementation. When they conflict, what/ wins.
- **When to create a new file vs. extend an existing one:** if the new concern has its
  own lifecycle, configuration surface, and can be understood independently, it gets
  its own file. If it's a capability added to an existing component, it goes in that
  component's file.
