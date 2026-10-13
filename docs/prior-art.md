# Prior Art & Positioning

> Surveyed 2026-10-13 (web search). Extracted from the original PoC planning docs when
> they were retired in favor of [GitHub issues + milestones](../../milestones) — the
> conclusions below still drive several roadmap items (#12, #15).

## The landscape

**Heavyweights — reactive / on-demand, no living memory:**
- **k8sgpt** (~8.2k⭐, CNCF) — deterministic analyzer scanner + LLM explanation;
  operator can schedule scans. Finds *pre-modeled* failure patterns ("alerts as code");
  no open-ended exploration, memory, or topology understanding.
- **HolmesGPT** (~3.6k⭐, CNCF Sandbox, Robusta) — genuine agentic SRE investigation
  (toolsets + MCP, root-cause). Alert/on-demand-triggered; no persistent memory, no
  proactive cadence, no self-refining watch-list.
- **kagent** (~4k⭐, CNCF) — framework for *running* agents in Kubernetes ("Kubernetes
  for agents", the CNCF-native OCE alternative). Infrastructure, not an SRE product.

**Incident/RCA agents — trigger-driven:** Arvo **aurora** (~432⭐, LangGraph),
**ongrid** (~1.1k⭐, Slack-driven ops), **nudgebee** (~399⭐). Commercial (reactive):
Cleric, Resolve.ai, Parity, NeuBird.

**Doing almost exactly our thing, hobby-scale (0–56⭐):** `langchain-samples/sre-agent`,
`KOB-Monitoring-Agent`, `corepulse-agent`, `KubeSentry-AI`, `mstrYoda/sre-ai-agent` —
proof the idea recurs independently; none have a safety model, maintenance, or a
production path.

## What the landscape tells us

1. **Investigation is solved/crowded** — HolmesGPT and AgenticRun already do agentic
   RCA. Don't build a bespoke investigation engine; keep it pluggable (#15).
2. **Almost everyone is reactive** — proactive "do your rounds on a schedule" is
   under-served.
3. **The self-refining wiki/memory is the real novelty** — an agent-maintained *living*
   knowledge base of this cluster's topology + baselines, corrected every patrol.
   Others use static RAG over docs or incident history. **This is the differentiator —
   lean into it.**
4. **Our structural advantage:** delegate *action* to a governed AgenticRun (sandbox +
   approval + derived RBAC + audit) (#12). Others only advise or act directly.

## Net positioning

**Don't compete on diagnosis — be the proactive memory layer nobody has**, and let
k8sgpt / HolmesGPT / AgenticRun do what they're best at underneath. Concretely:
k8sgpt output is a cheap input signal the patrol should read; HolmesGPT is a
candidate pluggable RCA engine for flagged anomalies (#15).

Honest cons: hacky single-file PoC; diagnosis quality is whatever the model gives;
LLM patrols cost real tokens per cycle; non-determinism means non-reproducibility
(wiki + logs are the audit trail); wiki drift is a live risk (mitigations: #5, #7, #8);
no fleet story yet (#13).
