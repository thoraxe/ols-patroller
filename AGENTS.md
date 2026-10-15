# OLS Patroller

Proactive, read-only SRE patrol agent for OpenShift/Kubernetes clusters (deepagents
engine, persistent wiki memory). Hacky single-file PoC: `patrol.py`.

## Specs

All specifications live in `.ai/spec/`. Start with `.ai/spec/README.md` for project
overview, reading order, and structure guide.

## Workflow — spec-first PRs

All feature work follows the spec-first loop. Specs are the implementation
contract, written so a context-clear agent can implement from them alone; GitHub
issues inform the specs.

| Phase | Command | Gate |
|---|---|---|
| 1. Spec PR — contract rules + failing tests, fresh-AI review loop until clean | `/skill:spec-first <issue or feature>` | **Human adds `spec-approved`** |
| 2. Implementation — context-clear agent makes the tests pass, fresh-AI review loop | `/skill:spec-implement <PR>` (fresh session) | **Human merges** |

PR state labels track where a PR is in that loop. The skills manage the AI-review
and ready states; humans own the gate transitions (`spec-approved`, merging):

| Label | Meaning |
|---|---|
| `ai-spec-review` | PR is currently being reviewed by AI (spec phase) |
| `spec-review-required` | Spec passed all AI reviews — ready for human review |
| `spec-approved` | A human reviewed and approved the spec changes (gate to phase 2) |
| `ai-implementation-review` | PR is currently being reviewed by AI (implementation phase) |
| `implementation-complete` | Fully AI-reviewed — could be merged; optional human review |

- Every feature PR has exactly **two commits**: (1) specs + failing tests,
  (2) implementation.
- Review loops dispatch an independent reviewer — a separate `pi` process with no
  session context; reports land in `.ai/spec/verification/` (gitignored).
- Implementation never changes contract rules — only `[PLANNED: #N]` → `[DONE: #N]`
  flips. If the spec turns out wrong, stop and surface it; spec changes need their
  own review loop.
- Never merge without human approval.

## Development

- Run (no cluster needed): `uv run patrol.py --once --dry-cluster` — requires
  `PATROL_API_KEY`, `PATROL_BASE_URL`, `PATROL_MODEL` (any OpenAI-compatible
  endpoint). Run it **twice** to see the memory loop.
- Real cluster: `uv run patrol.py --once` (needs `oc`/`kubectl` + kubeconfig).
- Loop mode: `uv run patrol.py --loop --interval 21600`.
- Dependencies are PEP 723 inline metadata in `patrol.py`; `uv` resolves them — no
  separate install step. `pyproject.toml` mirrors them for the test environment
  only (`uv run patrol.py` still uses the PEP 723 script mode).
- Tests: `uv run --group dev pytest tests/ -q` — `tests/test_wiki_git.py` covers
  the wiki git persistence contract (#3); `tests/test_container_image.py` covers
  the image contract (#1); `tests/test_kubectl_guard.py` covers the guard
  contract, and `tests/test_patrol_deepagents.py` is the fake-model e2e over the
  real engine stack (#4).
- `wiki/` (the wiki directory: `cluster-wiki.md` + its git repo) is a runtime
  artifact (gitignored) — never commit it.
- Roadmap: GitHub issues + milestones, not documents. Spec files mark planned
  behavior as `[PLANNED: #N]`.
