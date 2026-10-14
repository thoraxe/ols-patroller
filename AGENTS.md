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
| 1. Spec PR — contract rules + failing tests, fresh-AI review loop until clean | `/skill:spec-first <issue or feature>` | **Human approves the spec** |
| 2. Implementation — context-clear agent makes the tests pass, fresh-AI review loop | `/skill:spec-implement <PR>` (fresh session) | **Human approves and merges** |

- Every feature PR has exactly **two commits**: (1) specs + failing tests,
  (2) implementation.
- Review loops dispatch an independent reviewer — a separate `pi` process with no
  session context; reports land in `.ai/spec/verification/` (gitignored).
- Implementation never changes contract rules — only `[PLANNED: #N]` → `[DONE: #N]`
  flips. If the spec turns out wrong, stop and surface it; spec changes need their
  own review loop.
- Never merge without human approval.

## Development

- Run (no cluster needed): `uv run patrol.py --once --dry-cluster -v` — requires
  `PATROL_API_KEY`, `PATROL_BASE_URL`, `PATROL_MODEL` (any OpenAI-compatible
  endpoint). Run it **twice** to see the memory loop.
- Real cluster: `uv run patrol.py --once -v` (needs `oc`/`kubectl` + kubeconfig).
- Loop mode: `uv run patrol.py --loop --interval 900`.
- Dependencies are PEP 723 inline metadata in `patrol.py`; `uv` resolves them — no
  separate install step.
- No tests in-repo yet; the fake-model e2e is planned for `tests/` (#4).
- `cluster-wiki.md` is a runtime artifact (gitignored) — never commit it.
- Roadmap: GitHub issues + milestones, not documents. Spec files mark planned
  behavior as `[PLANNED: #N]`.
