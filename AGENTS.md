# OLS Patroller

Proactive, read-only SRE patrol agent for OpenShift/Kubernetes clusters (deepagents
engine, persistent wiki memory). Hacky single-file PoC: `patrol.py`.

## Specs

All specifications live in `.ai/spec/`. Start with `.ai/spec/README.md` for project
overview, reading order, and structure guide.

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
