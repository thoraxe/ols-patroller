# Project Structure

## Module Map

The entire implementation is one file: `patrol.py` (~730 lines), a PEP 723 uv script
(`uv run patrol.py` resolves dependencies from inline metadata: `deepagents>=0.7`,
`langchain-openai>=1.0`, `pydantic>=2`, `requests>=2.31`; Python ≥ 3.11). Sections
are separated by banner comments:

| Region | Key Symbols | Responsibility |
|---|---|---|
| config | `BASE_URL`, `MODEL`, `API_KEY`, `KUBECTL`, `SLACK_URL`, `MAX_STEPS`, `TOOL_TIMEOUT`, `OUT_CAP`, `READONLY_VERBS`, `WIKI_REMOTE`, `CLUSTER_ID`, `PUSH_ATTEMPTS`, `PUSH_TIMEOUT`, `PUSH_BACKOFF` | Env-derived config + constants, read once at import (exception: the `PUSH_*` wiki-git bounds are read at call time — see Naming Conventions) |
| prompts | `SYSTEM_PROMPT`, `WIKI_SEED` | Patrol framing; wiki bootstrap content |
| fixtures | `FIXTURES`, `fixture_for` | Dry-cluster canned outputs (substring match, first wins) |
| report schema | `Finding`, `PatrolReport` | Pydantic output contract |
| guard | `kubectl_readonly`, `resolve_kubectl`, `make_kubectl_tool` | Read-only cluster CLI tool + its langchain wrapper |
| model/engine | `make_model`, `_disable_default_subagent` | `ChatOpenAI` construction; deepagents harness profile to kill the default subagent |
| output | `preview`, `notify`, `report_fields`, `REPORT_CONTRACT` | Console/Slack output; report normalization; the reporting-pass instructions |
| shape pass | `shape_report` | Tool-free structured-output pass over the finished conversation |
| wiki git | `ensure_wiki_repo(wiki_dir) -> bool` (True = git backing on), `write_wiki_atomic(wiki_path, content)` (raises on failure), `commit_wiki(wiki_path, findings, cluster_id) -> bool` (findings = the finding dicts produced by `report_fields`; False = nothing changed or no repo; never inits one), `push_wiki(wiki_dir, remote) -> bool` (False = push gave up) | Wiki durability: repo init at startup (file-only fallback), atomic write, one commit per wiki-changing patrol, bounded best-effort push [DONE: #3] |
| patrol | `run_patrol` | One patrol end-to-end (async) |
| main | `main`, `one` | CLI parsing + once/loop scheduling |

Other paths:

| Path | Role |
|---|---|
| `docs/prior-art.md` | Landscape survey + positioning (drives roadmap #12, #15) |
| `wiki/` | Runtime artifact — the wiki directory: `cluster-wiki.md` + its git repo; **gitignored**, never commit [DONE: #3 — was a bare `cluster-wiki.md` beside `patrol.py`] |
| `tests/` | `conftest.py` (repo-root import path) + `test_wiki_git.py` — wiki git persistence unit tests (#3 spec PR) + `test_container_image.py` — Containerfile/`requirements.txt` static contract tests [DONE: #1]. `test_manifests.py` (kustomize manifest contract) + `test_image_ci.py` (post-merge workflow contract) planned [PLANNED: #2]. Fake-model e2e still planned for `tests/test_patrol_deepagents.py` [PLANNED: #4] |
| `pyproject.toml`, `uv.lock` | Project + dev dependency groups for the test environment (`uv run --group dev pytest tests/`); mirrors `patrol.py`'s PEP 723 inline metadata (range constraints) — the image instead installs the exactly-pinned `requirements.txt` [DONE: #1] |
| `Containerfile` | In-cluster image build recipe: ubi9-minimal base, version-pinned + checksum-verified `oc`/`kubectl` client, pip install from `requirements.txt`, non-root random-UID, default command `--loop --interval 900 --wiki /wiki/cluster-wiki.md` [DONE: #1] (`what/container-image.md`) |
| `requirements.txt` | Exact pins (`name==version`) of the PEP 723 top-level runtime set; installed into the image at build time [DONE: #1] |
| `manifests/` | In-cluster deployment kustomize root (#2): namespace, ServiceAccount, ClusterRoleBinding to the built-in `cluster-reader`, RWO PVC `ols-patroller-wiki` (1Gi, default storage class, mounted at `/wiki`), gitignored `secret.yaml` (+ committed `secret.yaml.example` template), single-replica `Recreate` Deployment running `--loop --interval 900 --wiki /wiki/cluster-wiki.md`, kustomization with the `images:` override to the CI-published image [PLANNED: #2] (`what/in-cluster-deployment.md`) |
| `.github/workflows/image.yml` | Post-merge image CI (#2): on `push` to `main` only, builds from the repo-root `Containerfile` (`file: ./Containerfile`, `linux/amd64`) and publishes `ghcr.io/thoraxe/ols-patroller` with `main` + `sha-<SHA>` tags via the built-in `GITHUB_TOKEN` [PLANNED: #2] (`what/container-image.md` rules 13–16) |

## Key Entry Points

- `uv run patrol.py` → `main()` → argparse → env check → resolve kubectl →
  `ensure_wiki_repo(wiki_path.parent)` (git backing on/off for the process)
  [DONE: #3] → `one()` → `asyncio.run(run_patrol(...))`.
- `--loop` wraps `one(fatal=False)` in `while True` + `time.sleep(interval)`; a patrol
  exception is logged and the loop continues; `KeyboardInterrupt` stops cleanly.
  `--once` (default) re-raises patrol failures.
- Container: `ENTRYPOINT ["python3.12", "patrol.py"]` with default
  `CMD ["--loop", "--interval", "900", "--wiki", "/wiki/cluster-wiki.md"]`;
  dependencies were pip-installed from `requirements.txt` at build — nothing
  resolves or installs at container start [DONE: #1].
- `run_patrol()` is the whole patrol lifecycle: load wiki → in-flight AgenticRuns →
  build agent → stream exploration → shape pass → findings/notify/wiki write.

## Naming Conventions

- Single-file layout is intentional (hacky PoC); growth planned is additive
  (tests/, image, manifests) rather than a package split.
- Section banners (`# ----…---- name`) divide the file; keep new code in the
  matching section.
- Env vars are prefixed `PATROL_`; cluster-tool and wiki-git constants live in the
  config region. Wiki-git push constants (`PUSH_ATTEMPTS`, `PUSH_TIMEOUT`,
  `PUSH_BACKOFF`) are read at call time so tests can bound retries.
