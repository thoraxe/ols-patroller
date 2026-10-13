# OLS Patroller — Project Summary

> Written 2026-10-13 after the first successful real-cluster patrol.

## What the patrol project is

A **proactive, non-deterministic "resident SRE"** — the missing inverse of the
alerts-adapter. AlertManager and k8sgpt-style scanners only fire on *known, modeled*
conditions. The patroller instead does what a human SRE does between alarms: walks the
cluster on a schedule, explores whatever looks interesting, maintains notes, and pings a
human when something smells wrong — including things no one thought to write an alert for.

Three-layer design:

1. **Dumb deterministic scheduler** (cadence + dedup) — `--loop` today; a tiny
   adapter-style dispatcher later.
2. **Smart agentic patrol** — an LLM (GLM 5.3 via an OpenAI-compatible endpoint) with one
   whitelisted read-only
   `oc` tool, free to choose its own investigation path (~9–44 tool calls per patrol).
   Read-only by construction; it never mutates.
3. **Durable wiki as memory** — continuity is a *document*, not a process. Each patrol
   re-validates the wiki against live state (**wiki = hypotheses, cluster = truth**),
   rewrites it, and refines the watch-list its next patrol will follow. That's the "learning."

PoC status: single `uv` script (`patrol.py`), validated on a real single-node OCP 4.21
cluster — it found three real, silent, weeks-old failures (broken LVMS storage invisible
to ClusterOperator health, a `lightspeed-rhokp` rollout deadlock hidden behind
`Available=True`, and a `wrker` nodeSelector typo), all independently verified by direct
`oc` queries.

## Integration with AgenticRun

Division of labor: **patrol = eyes + memory (proactive discovery); AgenticRun = hands
(governed remediation)** inside the sandbox / approval / derived-RBAC / audit machinery.

Concrete integration points:

- **Patrol dispatches runs.** A patrol finding becomes an AgenticRun `spec.request`,
  templated exactly like the alerts-adapter templates alerts. Analysis-only mode
  (`Minimal`, no execution) for deeper read-only digging; full mode when remediation is
  warranted — the approval gate then puts a human in the loop. The patrol itself stays
  read-only forever.
- **Patrol dedups against runs.** Already reads `get agenticruns -A`; formalize with the
  alerts-adapter's label/fingerprint conventions.
- **Results feed the wiki.** `AnalysisResult` / `ExecutionResult` / `VerificationResult`
  CRs are structured findings; harvested into the wiki they become memory
  ("we tried X, verification said Y").
- **The patrol becomes a scheduled AgenticRun.** Mature end-state: a cron-style
  dispatcher creates "patrol" AgenticRuns; the `Agent` CR's per-step
  `systemPrompt`/`userPrompt` carries the patrol instructions, `spec.tools.mcpServers`
  carries the memory service, and the operator provides sandboxing, timeouts, `maxTurns`,
  OTel, and the kill switch — replacing the bare script.
- **Shared memory seam.** One MCP memory service wired into both the patrol's tools and
  AgenticRun's `spec.tools`, so conversations, patrols, and remediation runs all read and
  write the same brain.

## Pros / cons vs. the alternatives surveyed

| | What it is | vs. our patroller |
|---|---|---|
| **k8sgpt** (CNCF, ~8.2k⭐) | Deterministic analyzer scanner + LLM explanation | ✅ mature, fast, cheap, schedulable. ❌ only finds *pre-modeled* failure patterns ("alerts as code"); no open-ended exploration, memory, or topology understanding. **Complementary:** a signal source the patrol should read. |
| **HolmesGPT** (CNCF Sandbox, ~3.6k⭐) | Agentic RCA investigator (Robusta) | ✅ best-in-class investigation engine, rich toolsets/MCP. ❌ reactive (alert/on-demand-triggered), no persistent memory, no proactive cadence, no self-refining watch-list. **Complementary:** the pluggable investigation engine a patrol turn could invoke. |
| **kagent** (CNCF, ~4k⭐) | Framework for running agents in Kubernetes ("Kubernetes for agents"; the CNCF-native OCE alternative) | ✅ would give the patrol a proper declarative runtime. ❌ infrastructure, not an SRE product — patrol logic, memory, and safety still DIY; overlaps with what lightspeed-agentic-operator already provides. |
| **aurora / ongrid / nudgebee; Cleric / Resolve / Parity / NeuBird** | Incident-driven RCA agents and platforms | ✅ polished integrations (PagerDuty, Datadog, Slack). ❌ all reactive, mostly closed/SaaS, no living cluster wiki, no path to OLS-native governance or AgenticRun handoff. |
| **Hobby repos** (langchain-samples/sre-agent, KOB-Monitoring-Agent, corepulse-agent, KubeSentry-AI) | Conceptually similar proactive agents | ✅ proof the idea recurs independently. ❌ 0–56⭐, no safety model, no maintenance, no production path. |

**Our unique combination** — proactive cadence + open-ended agentic exploration +
self-refining persistent memory + strict read-only + delegation to governed remediation +
OpenShift/OLS-native — exists nowhere in that landscape.

**Our honest cons:** hacky single-file PoC; diagnosis quality is whatever the model gives
(don't try to beat HolmesGPT there); LLM patrols cost real tokens per cycle;
non-determinism means non-reproducibility (wiki + logs are the audit trail); wiki drift is
a live risk (one inline self-correction artifact already observed); no notify throttling
or fleet/multicluster story yet; and we own every line of it.

**Net positioning:** don't compete on diagnosis — be the *proactive memory layer* nobody
has, and let k8sgpt / HolmesGPT / AgenticRun do what they're best at underneath.
