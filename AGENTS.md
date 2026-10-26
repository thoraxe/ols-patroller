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
| 2. Implementation — context-clear agent makes the tests pass, fresh-AI review loop | `/skill:spec-implement <PR>` (fresh session) | **AI merges** (after clean review + `implementation-complete`; current policy, revocable) |

PR state labels track where a PR is in that loop. The skills manage the AI-review
and ready states. Humans own the `spec-approved` gate; merging is agent-owned
under current policy (clean implementation review + `implementation-complete`):

| Label | Meaning |
|---|---|
| `ai-spec-review` | PR is currently being reviewed by AI (spec phase) |
| `spec-review-required` | Spec passed all AI reviews — ready for human review |
| `spec-approved` | A human reviewed and approved the spec changes (gate to phase 2) |
| `ai-implementation-review` | PR is currently being reviewed by AI (implementation phase) |
| `implementation-complete` | Fully AI-reviewed — the implementing agent merges the PR at this point (current policy, revocable) |

- Every feature PR has exactly **two commits**: (1) specs + failing tests,
  (2) implementation. A **sync merge** of `main` may sit between them when the
  branch is cut before its siblings merge: it is mechanical and content-neutral
  (append-only conflicts resolved to the union of both sides, commit 1 never
  rewritten), and the suite after it must still fail only the PR's own new
  tests — the spec→impl pair stays the PR's diff against `main`.
- Review loops dispatch an independent reviewer — a separate `pi` process with no
  session context; reports land in `.ai/spec/verification/` (gitignored).
- Implementation never changes contract rules — only `[PLANNED: #N]` → `[DONE: #N]`
  flips. If the spec turns out wrong, stop and surface it; spec changes need their
  own review loop.
- Merging: the implementing agent merges once the implementation review is
  APPROVED with zero blocking findings and the PR carries `implementation-complete`
  (merge commit only — squash/rebase would collapse the two-commit structure).
  Current policy, revocable by the human at any time; the `spec-approved` gate
  stays human-only either way.

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
  contract; `tests/test_patrol_deepagents.py` is the fake-model e2e over the
  real engine stack (#4, incl. the wiki-skeleton wiring of #5, the recheck
  repair path of #6, the stale-entry expiry wiring of #7, the working-set
  budget wiring of #40 — expire → compact → stamp, prune console lines, the
  soft-budget stderr warning, the repair-pass budget inclusion, and prune
  recoverability from git history — and the request-bound wiring of #49: the
  agent's model built at the exploration bound, the shape pass's at the
  reporting bound, the repair reusing the shape instance — and the
  empty-wiki classification wiring of #51: retry-and-recover with the named
  corrective, the still-empty stderr warning with findings preserved, and
  the repair-still-empty fall to the keep-old-wiki outcome — and the
  one-event-loop loop-mode wiring of #50: two patrols through `main()
  --loop` on a single process-lifetime event loop, the failed-patrol
  catch-and-continue on that same loop, and a clean ctrl-c exit);
  `tests/test_wiki_schema.py` covers the
  wiki skeleton schema + validation-on-write contract (#5) plus the
  per-entry recheck-command contract (#6); and `tests/test_wiki_expiry.py`
  covers the stale-entry auto-expiry contract (#7: the `verified_patrol`
  clock matrix and the expiry flip); and `tests/test_wiki_stats.py` covers
  the wiki health-stats contract (#8: the `wiki_stats` and `wiki_churn`
  matrices, the `wiki_history` window over a real repo, and the offline
  `--wiki-stats` mode); and `tests/test_sweep_efficiency.py` covers the
  efficient-sweep contract (#20: the layered-sweep and field-selection
  teaching in the system prompt, and the dry-cluster fixtures that model the
  taught sweep — the truncation-marker recovery hint is covered in
  `test_kubectl_guard.py`); and `tests/test_wiki_budget.py` covers the wiki
  length-budget contract (#40: the `compact_anomaly_log` and
  `wiki_budget_warnings` matrices, the `wiki_stats` size keys, the
  `--wiki-stats` size line, and the report contract's working-set pins); and
  `tests/test_request_bounds.py` covers the per-call-shape request-bound
  contract (#49: the two bound constants and the `make_model` timeout
  passthrough); and `tests/test_empty_report.py` covers the empty-report
  contract (#51: the report contract's wiki-required pins and the
  empty-wiki classification matrix over `shape_report` — the named
  corrective retry, the double-empty return, the composition and repair
  sharing, and the generic-retry and zero-findings guards).
- `wiki/` (the wiki directory: `cluster-wiki.md` + its git repo) is a runtime
  artifact (gitignored) — never commit it.
- Roadmap: GitHub issues + milestones, not documents. Spec files mark planned
  behavior as `[PLANNED: #N]`.
