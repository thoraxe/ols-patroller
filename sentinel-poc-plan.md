# Cluster Sentinel — Hacky PoC Plan

> Status: **PoC script started** (`patrol.py` exists; uv/GLM wired). Scratch doc,
> intentionally stored outside the `openshift/ols` git repo (`/home/thoraxe/ols-patroller/`)
> so it can't be committed by accident.

## 1. Goal

Prove the smallest possible version of a **proactive, non-deterministic SRE agent** that:

1. Launches on a schedule ("does its rounds"), not in response to an alert.
2. **Agentically explores** the live cluster read-only — it decides what to look at.
3. First maps **how the cluster is deployed and wired** (topology).
4. **Validates reality against a persistent "wiki"** (its memory), updates the wiki, and
   refines what it watches next time.
5. Checks **existing AgenticRuns / alerts** so it doesn't double-investigate.
6. **Notifies a human** (console print + optional Slack) when something looks wrong.

Explicitly *not* a rebuild of AgenticRun or a new control plane. The PoC is the
**dispatcher + patrol loop + wiki**; real remediation (if ever) is delegated to an
AgenticRun later.

## 2. What we proved earlier (carry-over decisions)

- **Investigation = non-deterministic** (LLM-driven, open-ended). Determinism only lives
  in cadence + dedup.
- **Continuity = a durable document, not a long-lived process.** The agent is disposable
  and reborn each patrol; the wiki is the source of truth. Crash-safe by construction.
- **Wiki is hypotheses, live cluster is ground truth.** Every patrol re-validates and
  expires stale claims — guard against the agent believing its own fiction.
- **Read-only by design.** The patrol never mutates. Anything actionable → notify, or
  (future) hand off to an approval-gated AgenticRun.
- OCE is overkill; this reuses the existing OLS stack conceptually.

## 3. Scope (ruthlessly minimal)

**In:**
- One standalone Python script (`patrol.py`), stdlib-only.
- One agentic loop with a **single read-only tool** (`kubectl_readonly`).
- One wiki file (`cluster-wiki.md`) read at start, rewritten at end.
- Notifications: print to stdout; optional Slack webhook.
- Run modes: `--once` (default) or `--loop --interval N`.

**Out (deferred):**
- Creating/▶running AgenticRuns for remediation.
- Multicluster / fleet.
- Proper CRD-based dispatcher, RBAC derivation, approval gates.
- Vector DB / MCP memory server (wiki is just a markdown file for now).
- Confidence scoring, notification dedup/throttling beyond a trivial check.

## 4. Environment notes (from initial development, 2026-10-12)

- Runs as a **uv inline-script** (PEP 723) — dependencies (`requests`) are declared in
  the script header; no venv management needed.
- Needs a machine with `oc`/`kubectl` + kubeconfig for real patrols; a `--dry-cluster`
  fixture mode lets the agent loop be demoed without a cluster (feeds canned `oc` output).
- The LLM endpoint is any **OpenAI-compatible chat-completions API**; configure via
  `PATROL_BASE_URL` / `PATROL_MODEL` / `PATROL_API_KEY` env vars.
  Developed against a GLM 5.3 model.

## 5. Architecture (one picture)

```
patrol.py  (run via `--once` or cron/`--loop`)
  │
  ├─ 1. preflight: kubeconfig ok? agentic enabled? (best-effort)
  ├─ 2. dedup: `oc get agenticruns -A` + active alerts → note what's already handled
  ├─ 3. load cluster-wiki.md  (topology + baselines + watch-list + anomaly log)
  ├─ 4. AGENTIC LOOP  (LLM + kubectl_readonly tool, max N steps):
  │        system: "you are an SRE doing your rounds…"
  │        context: wiki + in-flight runs/alerts
  │        tool:   kubectl_readonly(args)   ← whitelisted verbs only
  │        model explores → forms findings → proposes wiki update
  ├─ 5. parse final JSON: { findings[], notifications[], updated_wiki }
  ├─ 6. write updated_wiki → cluster-wiki.md
  └─ 7. emit notifications: print + optional Slack webhook
```

## 6. The agent loop (the only non-trivial part)

- **Transport:** stdlib `urllib.request` POST to the model API; manual tool-call loop.
- **Single tool:** `kubectl_readonly(args: string)`.
  - Enforce read-only by **whitelisting the first token**: `get`, `describe`, `logs`,
    `top`, `version`, `api-resources`, `explain`, `status`. Reject everything else
    (`apply/create/delete/edit/patch/scale/exec/cp/port-forward/replace/annotate/label/…`).
  - Hard-reject shell metacharacters (`;`, `|`, `&`, backticks, `$(`) — split args, no shell.
  - Cap output size (e.g. 20 KB/truncate) to control token cost.
- **Loop control:** max tool calls (e.g. 15) and a wall-clock budget; then force a final
  answer.
- **Final answer contract (strict JSON):**
  ```json
  {
    "findings": [{ "severity": "info|warn|crit", "summary": "...", "evidence": "..." }],
    "notifications": ["human-readable message", "..."],
    "updated_wiki": "full new markdown content of cluster-wiki.md"
  }
  ```
- **Prompt skeleton (system):** "You are a resident SRE doing a routine patrol. You cannot
  change anything. First map how the cluster is deployed and wired. Compare what you see
  against the WIKI below — the WIKI is your prior notes and may be wrong or stale; the live
  cluster is the truth. Correct/expire stale WIKI entries. Avoid re-investigating anything
  already covered by the listed in-flight AgenticRuns/alerts. Refine the watch-list for next
  time. Only notify about things a human should actually care about."

## 7. Config (all via env; all three LLM vars required)

| Var | Default | Purpose |
|---|---|---|
| `PATROL_API_KEY` | — | API key for the LLM endpoint (required) |
| `PATROL_BASE_URL` | — | any OpenAI-compatible chat-completions endpoint (required) |
| `PATROL_MODEL` | — | model id on that endpoint (required) |
| `KUBECTL` | `oc` (fallback `kubectl`) | cluster CLI |
| `WIKI_PATH` | `./cluster-wiki.md` | memory file |
| `SLACK_WEBHOOK_URL` | — | optional; if unset, print only |
| `PATROL_MAX_STEPS` | `15` | tool-call budget |
| `PATROL_INTERVAL` | `0` | seconds between rounds (`--loop`) |

## 8. The wiki schema (markdown sections the agent reads + rewrites)

```
# Cluster Wiki  (last patrol: <ts>)

## Topology            # components, namespaces, operators, wiring, ownership
## Baselines           # "normal for THIS cluster" (replicas, versions, restart rates)
## Watch-list          # things to keep an eye on next round  ← the refinement loop
## Open questions      # unresolved, pick up next time
## Anomaly log         # what was flagged, when, outcome, links  ← notify-dedup source
```
Every entry carries `(source, timestamp, confidence)` inline; the agent expires stale ones.

## 9. Build order (when we actually build)

1. Skeleton `patrol.py`: arg parsing, env load, read/write wiki file, print notifier.
2. `kubectl_readonly` with the whitelist + output cap (no shell). Unit-test the guard.
3. `--dry-cluster` fixture mode (canned outputs) to develop the loop without a cluster.
4. Anthropic tool-call loop over `urllib`; strict-JSON final parse (with one retry).
5. Wire the wiki in/out + notifications. First end-to-end `--once` run on a dev cluster.
6. Add the dedup pre-step (`oc get agenticruns -A`, active alerts) — best-effort, non-fatal.
7. Add `--loop` + Slack. Done for PoC.

## 10. Guardrails / risks to not forget

- **Read-only enforcement is the safety boundary** — whitelist first, no shell, no excuses.
- **Cost:** opus patrols add up; cap steps + output, start with `--once`, cheap cadence.
- **Wiki drift:** re-validate every round; treat wiki as hypotheses; expire stale entries.
- **Alert fatigue:** only notify on `warn`/`crit`; check anomaly log before re-notifying.
- **Non-reproducible by nature:** keep findings + wiki diffs as the audit trail.
- **Secrets:** never let the model read Secret *values*; exclude `get secret -o yaml` /
  consider stripping. (Add to the tool guard.)

## 11. Open questions for later

- Anthropic vs OpenAI-compatible as the default transport for portability?
- When does a patrol *escalate to an actual AgenticRun* vs just notify? (deferred)
- Wiki as a file vs a ConfigMap vs a real memory/MCP service? (file for PoC)
- How to scope a patrol (whole cluster vs per-namespace rounds) to bound cost?

## 12. Prior art / landscape (web search, 2026-10-13)

The "AI SRE agent" space is crowded and maturing fast. Characterizing the relevant work
so we don't reinvent it:

**Heavyweights — reactive / on-demand, no living memory:**
- **k8sgpt** (~8.2k⭐, CNCF) — analyzer-based cluster scanner + LLM explanation; operator
  can schedule scans. A *diagnostic scanner*, not an agent with memory/learning.
- **HolmesGPT** (~3.6k⭐, CNCF Sandbox, Robusta) — genuine *agentic* SRE investigation
  (toolsets + MCP, root-cause). Primarily **alert/trigger-driven**; no self-updating wiki.
  Closest analog to our AgenticRun analysis phase.
- **kagent** (~4k⭐, CNCF) — "Cloud Native Agentic AI": a framework for *running* agents in
  Kubernetes. Effectively the **open-source, CNCF-native "Kubernetes for agents"** — the
  real competitor to OCE (relevant to the earlier "is OCE overkill" conclusion).

**Incident/RCA agents — trigger-driven:** Arvo **aurora** (~432⭐, LangGraph, PagerDuty/
Datadog/Slack), **ongrid** (~1.1k⭐, Slack-driven ops agent), **nudgebee** (~399⭐, AI-SRE
platform). Commercial equivalents (reactive): Cleric, Resolve.ai, Parity, NeuBird.

**Doing almost exactly our thing, but hobby-scale (0–56⭐):**
`langchain-samples/sre-agent`, `KOB-Monitoring-Agent` (proactive monitor + RAG + alert/heal),
`corepulse-agent`, `KubeSentry-AI`, `mstrYoda/sre-ai-agent`.

### What the landscape tells us
1. **Investigation is solved/crowded** — HolmesGPT (CNCF) and our own AgenticRun already do
   agentic RCA. Don't build a bespoke investigation engine.
2. **Almost everyone is reactive** (alert/incident-triggered or on-demand). **Proactive
   "do your rounds on a schedule"** is only attempted by tiny repos → under-served.
3. **The self-refining "wiki"/memory is the real novelty** — an agent-maintained *living*
   knowledge base of this cluster's topology + baselines, corrected every patrol. Others use
   *static* RAG over docs or incident history. **This is our differentiator — lean into it.**
4. **Our unique structural advantage:** delegate *action* to a governed **AgenticRun**
   (sandbox + approval + derived RBAC + audit). Others only advise or act directly.

### Decisions driven by this
- **Investigation engine is pluggable.** The tiny `kubectl_readonly` loop is fine for the
  PoC; the design must allow swapping in **HolmesGPT** or an **AgenticRun** later rather than
  growing our own diagnoser.
- **Compete on continuity + proactivity, not diagnosis quality.** Don't try to beat
  k8sgpt/HolmesGPT at RCA.
- **If we ever want a general agent runtime, evaluate `kagent` (CNCF) before OCE.**
