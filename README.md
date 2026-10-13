# OLS Patroller PoC

Tracked in [OLS-4439](https://redhat.atlassian.net/browse/OLS-4439) (research spike).

A hacky proof-of-concept of a **proactive, non-deterministic SRE agent** ("resident SRE"):
on a schedule, an LLM agentically explores a cluster **read-only**, validates reality
against a persistent **wiki** (its memory), updates/refines the wiki, and notifies a
human (console / Slack) about anything it thinks is wrong.

Design rationale + prior-art landscape: see `sentinel-poc-plan.md` (same folder).

## Run it

Requires: `uv`, plus credentials for any **OpenAI-compatible** LLM endpoint.

```bash
export PATROL_API_KEY=<your API key>
export PATROL_BASE_URL=<endpoint>      # e.g. https://api.example.com/v1
export PATROL_MODEL=<model id>

uv run patrol.py --once --dry-cluster -v     # no cluster needed; canned fixtures
uv run patrol.py --once -v                   # real cluster (needs oc/kubectl + kubeconfig)
uv run patrol.py --loop --interval 900       # patrol every 15 min
```

Run `--once --dry-cluster` **twice** to see the memory loop: patrol #1 maps the
cluster + writes the wiki; patrol #2 consults the watch-list, tracks persistence
across patrols, and escalates per its own previously-written rules.

## What it does per patrol

1. Reads in-flight `AgenticRuns` (best-effort dedup context).
2. Loads `cluster-wiki.md` (seeded on first run).
3. Agentic loop: model explores via a single **`kubectl_readonly`** tool (it decides
   what to look at; ~5–15 steps typical).
4. Final report as **typed structured output** (`ToolStrategy`): `findings`,
   `notifications`, `updated_wiki` — parsed by schema, not hand-rolled JSON regex.
5. Writes the wiki back; prints/Sends notifications.

## Engine

The agent loop is [langchain-ai/deepagents](https://github.com/langchain-ai/deepagents)
(`create_deep_agent`) — the same upstream library the OLS agentic sandbox uses,
depending on it **directly** (no OLS code imported, keeps the patroller
standalone/open-source):

- **No backend** → no shell, no filesystem writes; built-in `ls/write_file/
  edit_file/delete/glob/grep/execute` are removed via a replacement
  `FilesystemMiddleware(tools=["read_file"])` (state-backed, in-memory).
- The default general-purpose `task` subagent is disabled via a registered
  `HarnessProfile`, keeping the patrol single-threaded.
- Effective tool surface: **`[read_file, kubectl_readonly]`** (verified in tests:
  `execute`/`task` calls are rejected as invalid tools).
- Model via `langchain-openai`'s `ChatOpenAI` — any OpenAI-compatible endpoint,
  same `PATROL_*` env contract as before.
- Step budget = `recursion_limit` (`max_steps * 2 + 12`); budget exhaustion is
  treated like the old JSON-parse failure: log, skip the wiki write, patrol again
  next cycle (the wiki is only hypotheses; a lost patrol is cheap).

## The wiki (`cluster-wiki.md`)

Sections the agent reads **and rewrites** every patrol: Topology, Baselines,
Watch-list, Open questions, Anomaly log — entries tagged `(source, timestamp,
confidence)`. The wiki is **hypotheses**; live cluster is **truth**; each patrol
re-validates and expires stale claims. The watch-list is the refinement loop:
patrol N writes the rules patrol N+1 follows.

## Safety model

- **Read-only by construction**: the only cluster tool is `kubectl_readonly`, which
  whitelist-checks the first verb (`get/describe/logs/top/version/api-resources/
  api-versions/explain/status/cluster-info/whoami`), runs via `subprocess` list
  (no shell), and refuses Secret value reads (`-o yaml/json/jsonpath` on secrets).
  The deepagents engine adds **no shell/filesystem/execute tools** on top (see
  Engine above), and when deployed in-cluster the pod SA's RBAC (e.g.
  `cluster-reader`, which grants no Secret access) is the server-side enforcement
  boundary — the tool guard is defense-in-depth, not the boundary itself.
- The patrol **never mutates the cluster**. Remediation would be delegated to a
  governed AgenticRun (not implemented in the PoC).
- Guard was unit-checked (mutation verbs + secret reads → all refused).

## Config (env)

| Var | Default | |
|---|---|---|
| `PATROL_API_KEY` | — | API key (required) |
| `PATROL_BASE_URL` | — | any OpenAI-compatible endpoint, e.g. `https://api.example.com/v1` (required) |
| `PATROL_MODEL` | — | model id on that endpoint (required) |
| `KUBECTL` | `oc`→`kubectl` | cluster CLI |
| `SLACK_WEBHOOK_URL` | — | if set, notifications also go to Slack |
| `PATROL_MAX_STEPS` | `30` | LLM-turn budget per patrol (raised from 15 after real-cluster runs; GLM needs >15 turns even single-node — budget exhaustion triggers the salvage pass) |
| `PATROL_INTERVAL` | `900` | seconds (`--loop`) |
| `PATROL_TOOL_TIMEOUT` | `60` | per-command timeout |

## Smoke-test results (2026-10-13, dry-cluster)

Raw-requests engine, GLM 5.3:

- Patrol #1: 13 steps / 44 tool calls; found all planted fixture issues
  (NotReady node, CrashLoopBackOff pod, Degraded monitoring CO, Progressing
  ingress), flagged the fixture cluster's implausibly small footprint as
  "restricted visibility", wrote a fully-structured wiki.
- Patrol #2: 5 steps; consulted the watch-list, tracked persistence across
  patrols, escalated worker-1 per its own rule, *declined* to over-escalate
  ingress (only ~3 min elapsed between patrols), tracked restart-count delta.

deepagents engine (scripted fake-model e2e, no endpoint needed):

- Full graph runs: tool calls dispatched, guard refusals (mutation verb, secret
  read), `execute`/`task` rejected as invalid tools (surface is exactly
  `[read_file, kubectl_readonly]`), `PatrolReport` structured output parsed,
  wiki written, notifications emitted.

Real-cluster runs (2026-10-13, ols-demo-2 / OCP 4.21, EnMaaS GLM 5.3):

- Patrol #4 (fresh wiki): 53 tool calls, 16 findings, 0 guard refusals;
  independently rediscovered the 19-day `wrker` nodeSelector typo plus new
  catches (DiskPressure↔OLS-restart correlation, all-ephemeral OLS state with
  unused lvms-vg1, personal-quay RAG source, 4 unapplied errata). Budget
  exhausted at 15 turns → salvage pass forced the final report (508k in /
  25k out tokens).
- Patrol #5 (memory loop): 45 tool calls, 17 findings; watch-list carried
  forward with per-item added/updated/unchanged tracking — "still unfixed at
  2nd patrol", DiskPressure downgraded to FYI after ~4.6h non-recurrence,
  emptyDir risk marked "materialized once, non-fatally" (rhokp Solr rebuild).
  One guard refusal: jsonpath extracting `credentialsSecret` from OLSConfig
  (strict by design; model recovered via a plain `get -o yaml`).

## Known limitations (it's a hacky PoC)

- Fixtures are static; a real cluster will exercise the loop differently.
- No per-scope cadence, no cost control beyond step budget, no notify-dedup
  beyond the anomaly log, wiki is a plain file (not MCP/vector memory).
- Investigation engine is intentionally dumb-pluggable: swap in HolmesGPT or an
  AgenticRun later rather than growing this (see plan §12).
