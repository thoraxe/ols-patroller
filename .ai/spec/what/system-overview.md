# System Overview

OLS Patroller is a proactive, non-deterministic SRE agent ("resident SRE") for
OpenShift/Kubernetes clusters: on a schedule, an LLM agentically explores a cluster
**read-only**, validates reality against a persistent markdown **wiki** (its memory),
rewrites the wiki, and notifies a human (console / Slack) about anything it thinks is
wrong. It is a hacky single-file PoC (`patrol.py`, deepagents engine) tracked in
[OLS-4439](https://redhat.atlassian.net/browse/OLS-4439).

## Behavioral Rules

### System Role

1. On each patrol, the system runs an agentic loop in which the model autonomously
   decides which read-only cluster commands to execute; it is not a scripted
   checklist.
2. Each patrol validates observed cluster state against the wiki (its own prior
   notes), corrects or expires stale wiki claims, and produces findings and
   human-facing notifications.
3. The system never mutates the observed cluster (see `safety-model.md`); remediation
   is delegated to a governed AgenticRun [PLANNED: #12].
4. Every completed patrol produces a typed `PatrolReport` — findings, notifications,
   updated wiki (see `reporting-notifications.md`).
5. The system runs either once (`--once`, the default) or on a fixed interval
   (`--loop --interval N`); in loop mode a failed patrol is logged and the loop
   continues rather than exiting.
6. Startup requires `PATROL_API_KEY`, `PATROL_BASE_URL`, and `PATROL_MODEL`; the
   process exits with an error if any is unset.

### Component Inventory

7. **Patrol engine** — deepagents agent loop, tool surface, step budget, termination,
   dry-cluster mode (`patrol-engine.md`).
8. **Read-only guard** — the single cluster tool `kubectl_readonly`: verb whitelist,
   secret-value refusal, timeouts, output cap (`kubectl-guard.md`).
9. **Wiki memory** — persistent markdown memory, seeded on first run, wholly
   rewritten each patrol (`wiki-memory.md`).
10. **Reporting & notifications** — typed report schema, severity vocabulary,
    console/Slack delivery (`reporting-notifications.md`).
11. **Safety model** — layered read-only enforcement spanning guard, engine, and
    (planned) in-cluster RBAC (`safety-model.md`).
12. **CLI & scheduling** — argument parsing, once/loop modes, wiki path selection
    (Configuration Surface below).

### Lifecycle

13. First run with no wiki file seeds it from `WIKI_SEED` (sections: Topology,
    Baselines, Watch-list, Open questions, Anomaly log).
14. Each patrol: load wiki → gather in-flight AgenticRuns context (best-effort) →
    agentic exploration → reporting pass → print findings, send notifications,
    rewrite wiki.
15. If the reporting pass fails entirely, the patrol ends with a warning and the wiki
    is left unchanged.

## Configuration Surface

Environment variables:

- `PATROL_API_KEY` — API key for the OpenAI-compatible endpoint (required)
- `PATROL_BASE_URL` — endpoint URL, e.g. `https://api.example.com/v1` (required)
- `PATROL_MODEL` — model id on that endpoint (required)
- `KUBECTL` — cluster CLI override; otherwise resolved `oc` → `kubectl` → `oc`
- `SLACK_WEBHOOK_URL` — if set, notifications are also posted to Slack
- `PATROL_MAX_STEPS` — LLM-turn exploration budget per patrol (default `30`)
- `PATROL_INTERVAL` — seconds between patrols in `--loop` mode (default `900`)
- `PATROL_TOOL_TIMEOUT` — per-command timeout in seconds (default `60`)

CLI flags: `--once` (default), `--loop`, `--interval`, `--dry-cluster`, `--wiki`
(default `cluster-wiki.md` next to `patrol.py`), `--max-steps`, `-v/--verbose`.

Hard-coded constants (not env-configurable): `OUT_CAP` = 15,000 chars of tool output
fed back to the model; model temperature 0.3, `max_tokens` 16000, request timeout
300 s.

## Constraints

- Any OpenAI-compatible chat-completions endpoint works. Real-run tuning evidence is
  from GLM 5.3 via EnMaaS, which needed >15 turns even on a single-node cluster —
  hence the 30-turn default budget.
- The wiki is a plain local file: single writer, no concurrency control.
- Project-wide constraints spanning all components live in `constraints.md`.

## Planned Changes

Full roadmap lives in GitHub issues + milestones (plans are tracked there, not in
documents). Milestone shape:

| Ticket | Summary |
|---|---|
| v0.1 (#1–#4) | In-cluster: Containerfile, manifests (SA + cluster-reader + PVC + Deployment), git-backed wiki, e2e tests in-repo |
| v0.2 (#5–#8) | Compounding wiki: skeleton schema + validation-on-write, per-entry recheck commands, `stale_after` expiry, `--wiki-stats` |
| v0.3 (#9–#11) | Operational hardening: notification dedup on state change, cost control, guard refuses `--raw` |
| future (#12–#16) | Remediation handoff to AgenticRuns, fleet/multicluster, CRD dispatcher, pluggable investigation engine, MCP-memory decision record |
