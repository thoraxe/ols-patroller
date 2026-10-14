# Project Structure

## Module Map

The entire implementation is one file: `patrol.py` (~640 lines), a PEP 723 uv script
(`uv run patrol.py` resolves dependencies from inline metadata: `deepagents>=0.7`,
`langchain-openai>=1.0`, `pydantic>=2`, `requests>=2.31`; Python ≥ 3.11). Sections
are separated by banner comments:

| Region | Key Symbols | Responsibility |
|---|---|---|
| config | `BASE_URL`, `MODEL`, `API_KEY`, `KUBECTL`, `SLACK_URL`, `MAX_STEPS`, `TOOL_TIMEOUT`, `OUT_CAP`, `READONLY_VERBS` | Env-derived config + constants, read once at import |
| prompts | `SYSTEM_PROMPT`, `WIKI_SEED` | Patrol framing; wiki bootstrap content |
| fixtures | `FIXTURES`, `fixture_for` | Dry-cluster canned outputs (substring match, first wins) |
| report schema | `Finding`, `PatrolReport` | Pydantic output contract |
| guard | `kubectl_readonly`, `resolve_kubectl`, `make_kubectl_tool` | Read-only cluster CLI tool + its langchain wrapper |
| model/engine | `make_model`, `_disable_default_subagent` | `ChatOpenAI` construction; deepagents harness profile to kill the default subagent |
| output | `preview`, `notify`, `report_fields`, `REPORT_CONTRACT` | Console/Slack output; report normalization; the reporting-pass instructions |
| shape pass | `shape_report` | Tool-free structured-output pass over the finished conversation |
| patrol | `run_patrol` | One patrol end-to-end (async) |
| main | `main`, `one` | CLI parsing + once/loop scheduling |

Other paths:

| Path | Role |
|---|---|
| `docs/prior-art.md` | Landscape survey + positioning (drives roadmap #12, #15) |
| `cluster-wiki.md` | Runtime artifact — the agent's memory; **gitignored**, never commit |
| `tests/` | Does not exist yet — fake-model e2e is planned for `tests/test_patrol_deepagents.py` [PLANNED: #4] |
| `Containerfile`, `manifests/` | Planned [PLANNED: #1, #2] |

## Key Entry Points

- `uv run patrol.py` → `main()` → argparse → `one()` → `asyncio.run(run_patrol(...))`.
- `--loop` wraps `one(fatal=False)` in `while True` + `time.sleep(interval)`; a patrol
  exception is logged and the loop continues; `KeyboardInterrupt` stops cleanly.
  `--once` (default) re-raises patrol failures.
- `run_patrol()` is the whole patrol lifecycle: load wiki → in-flight AgenticRuns →
  build agent → stream exploration → shape pass → findings/notify/wiki write.

## Naming Conventions

- Single-file layout is intentional (hacky PoC); growth planned is additive
  (tests/, image, manifests) rather than a package split.
- Section banners (`# ----…---- name`) divide the file; keep new code in the
  matching section.
- Env vars are prefixed `PATROL_`; cluster-tool constants live in the config region.
