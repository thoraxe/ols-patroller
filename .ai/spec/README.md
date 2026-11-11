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
| Prevent data exfiltration (egress allowlist) | `what/egress-control.md` |
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
| `what/egress-control.md` | `how/project-structure.md` (the egress derivation/render symbols + the `--egress-policy` mode) |

## Writing rules for what/

The reviewer approves behavior from the specs. Thus every `what/` file,
`glossary.md`, `constraints.md`, the ADRs, and the Summary of every spec PR uses
**extremely simple, plain English**. We follow **ASD-STE100 (Simplified Technical
English)** where possible.

**Sentences**
1. Write a maximum of 20 words in a rule or an instruction. Write a maximum of 25
   words in a description.
2. Write only one idea in each sentence.
3. Use the active voice. Write "The patroller writes the wiki", not "The wiki is
   written".
4. Use only the simple present, the simple past and the simple future ("writes",
   "wrote", "will write").
5. Write instructions as commands: "Read the wiki", not "The patrol should read
   the wiki".
6. Use "a", "an" and "the" before nouns. Do not drop them to save words.
7. Write a maximum of 6 sentences in a paragraph. Use a list when you have 3 or
   more items.

**Words**
8. Use one word for one thing. Use the word that `glossary.md` gives. For example,
   always write "wiki". Do not write "memory file", "store" or "notes".
9. Use one meaning for one word. For example, "patrol" always means one run,
   never the schedule.
10. Use simple, common words. Write "use", not "utilize". Write "start", not
    "initiate". Write "about", not "approximately". Write "if", not "in the event
    that".
11. Do not use phrasal verbs when a simple verb exists. Write "show", not "bring
    up". Write "find", not "figure out".
12. Do not use "-ing" words as verbs or adjectives. Write "when the patrol ends",
    not "after ending the patrol".
13. Do not use idioms, jokes or slang.

**Technical names**
14. You can use technical names: field names, file names, settings, constants and
    numbers. Put them in `code` style, for example `REPORT_MAX_TOKENS` or
    `kubectl_readonly`.
15. Explain each technical name in plain words the first time a file uses it, or
    link to `glossary.md`.
16. If a rule has a number, give an example with real values. For example: "The
    entry expires after 5 accepted writes (patrol 41 → patrol 46)."

**Structure**
17. Put the plain rule first. Put technical detail (exact fields, exact limits)
    after it, or in a table.
18. Every rule must be testable. A reader must know how to see if the patroller
    obeys it.
19. Give 3 to 6 examples in the form "If the patrol does X, then Y" for each new
    behavior.
20. Write what the change will **not** do.

We do not have the official ASD-STE100 dictionary in the repo. Use the rules
above, and choose the simplest common word when you are not sure.

## Writing rules for how/

`how/` files are for AI agents and for human reviewers of code changes. They use
the same short sentences, but they use the exact names from the code.

1. Obey the `what/` rules 1–7 (sentences), 13 (no idioms), 17 (plain point first)
   and 18 (testable).
2. Do not obey the `what/` rules 8–12 (words). Use the exact names from `patrol.py`
   in `code` style, for example `shape_report` or `carry_last_notified`.
   `glossary.md` gives the plain word for each code name.
3. Tables of files and symbols do not have to obey the sentence rules. Keep each
   table cell short.
4. Tell the reader which `what/` rules a section implements, for example
   "(`reporting-notifications.md` rules 7b–7c)".

## Adoption of the writing rules

- A new rule or an edited rule must obey the writing rules above.
- When a spec PR touches a rule, it also simplifies that rule and its immediate
  neighbors in the same section.
- We do not migrate whole files for this change alone. Existing text that does not
  obey the rules stays until a change touches it.
- The PR body lists each changed `what/` rule in plain words, by file and number.
  For example: "reporting-notifications.md rule 7h: the empty-wiki reason now says
  when the attempt hit its completion bound."
- A rule that a reviewer can misread is a defect. Fix it when you find it.

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
