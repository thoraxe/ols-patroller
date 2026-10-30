# System Overview

OLS Patroller is a proactive, non-deterministic SRE agent ("resident SRE") for
OpenShift/Kubernetes clusters: on a schedule, an LLM agentically explores a cluster
**read-only**, validates reality against a persistent markdown **wiki** (its memory),
rewrites the wiki, and notifies a human (console / apprise sinks) about anything it thinks is
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
   continues rather than exiting. When both `--once` and `--loop` are passed,
   loop mode engages — `--loop` wins. [DONE: #50 — in loop mode all patrols
   of the process run on **one process-lifetime event loop**: a fresh
   `asyncio.run` per patrol strands loop-bound engine state (cached async
   clients, keep-alive pools) on a closed loop, which real-cluster
   validation caught as a one-time "Event loop is closed" failure on the
   second patrol of a process. One loop for the whole process; a failed
   patrol is still logged and the loop continues; ctrl-c during a patrol
   or the inter-patrol wait still stops the process cleanly; `--once` is
   unchanged — one patrol per process run (ADR
   `decisions/0020-one-event-loop-per-process.md`).]
6. Startup requires `PATROL_API_KEY`, `PATROL_BASE_URL`, and `PATROL_MODEL`; the
   process exits with an error if any is unset — except in the offline
   `--wiki-stats` mode, which reads only the wiki and needs no model env
   [DONE: #8].

### Component Inventory

7. **Patrol engine** — deepagents agent loop, tool surface, step budget, termination,
   dry-cluster mode (`patrol-engine.md`).
8. **Read-only guard** — the single cluster tool `kubectl_readonly`: verb whitelist,
   secret-value refusal, raw-path (`--raw`) refusal, timeouts, output cap
   (`kubectl-guard.md`).
9. **Wiki memory** — persistent markdown memory, seeded on first run, wholly
   rewritten each patrol, git-backed: one wiki-changing patrol, one commit
   [DONE: #3]; skeleton-validated on write — frontmatter, fixed section set,
   entry metadata, a guard-acceptable `recheck` command on every Watch-list
   entry [DONE: #6] — with one repair pass and process-stamped counters
   [DONE: #5]; entries carry a process-stamped `verified_patrol` clock and
   unverified watch-list entries auto-expire into the Anomaly log
   [DONE: #7] (`wiki-memory.md`). `--wiki-stats` prints the wiki's four
   health numbers — verification rate, resolution ratio, confidence spread,
   churn — offline, from the document and its git history [DONE: #8]. The
   wiki is a bounded working set: the Anomaly log keeps the last
   `WIKI_ANOMALY_KEEP` (20) resolved/expired entries — older ones pruned by
   the patroller, recoverable from git history — and a soft
   20,000-character budget warns on overshoot without ever failing
   validation [DONE: #40]. The model is shown its own size: every shape-pass
   report invocation carries a deterministic size line (count, budget,
   over-budget excess or headroom) and the report contract names the
   consequence chain, so an over-budget wiki is pruned by the model instead
   of only warned about by the process [DONE: #67].
10. **Reporting & notifications** — typed report schema, severity vocabulary,
    console/apprise-sink delivery (`reporting-notifications.md`) [DONE: #9 —
    was console/Slack]. Notifications are state-change keyed: a `[<entry id>]`
    tag plus an old-vs-new wiki diff suppresses standing unchanged entries, and
    a weekly process digest names long-unfixed watching entries [DONE: #35].
11. **Safety model** — layered read-only enforcement spanning guard, engine, and
    (planned) in-cluster RBAC (`safety-model.md`).
12. **CLI & scheduling** — argument parsing, once/loop modes, wiki path
    selection, and the offline `--wiki-stats` mode [DONE: #8] (Configuration
    Surface below).
12a. **Container image** — ubi9-minimal image that runs `patrol.py` in-cluster:
     version-pinned OpenShift client, build-time pip install from
     `requirements.txt`, non-root random-UID, default command `--loop --interval
     21600 --wiki /wiki/cluster-wiki.md` [DONE: #1] [DONE: #25]
     (`container-image.md`).
12b. **In-cluster deployment** — kustomize manifests (`manifests/`):
     ServiceAccount bound to the built-in `cluster-reader`, RWO PVC at `/wiki`,
     single-replica non-root Deployment running `--loop --interval 21600`,
     Secret-sourced env, optional deploy-key remote-push wiring (committed
     example patch, opt-in copy) [DONE: #39]; merges to `main` build and
     publish the image to GHCR,
     and published `v*` releases retag the matching `sha-<SHA>` image with the
     release tag
     (`container-image.md` rules 13–18) [DONE: #2] [DONE: #25]
     [release retag: DONE: #36]
     (`in-cluster-deployment.md`).

### Lifecycle

13. First run with no wiki file seeds it from `WIKI_SEED` (sections: Topology,
    Baselines, Watch-list, Open questions, Anomaly log; the seed demonstrates the
    full skeleton — frontmatter + sections [DONE: #5]).
14. Each patrol: load wiki → gather in-flight AgenticRuns context (best-effort) →
    agentic exploration → reporting pass → validate the wiki skeleton (one repair
    pass on failure) [DONE: #5] → print findings, filter/emit notifications
    (state-change dedup + weekly digest) [DONE: #35], expire
    stale watch-list entries [DONE: #7], compact the Anomaly log to its keep
    budget [DONE: #40], stamp the per-entry notify clock [DONE: #35] and the
    frontmatter counters
    [DONE: #5], rewrite the wiki atomically, commit it to the wiki repo, push
    if `PATROL_WIKI_REMOTE` is set [DONE: #3].
15. If the reporting pass fails entirely, the patrol ends with a warning and the wiki
    is left unchanged. The same holds when the wiki update fails skeleton
    validation and its repair pass: the old wiki stands and the patrol's findings
    and notifications still deliver [DONE: #5]. [DONE: #51 — an
    empty-`updated_wiki` report is the third degradation: retried once
    inside the reporting pass (`reporting-notifications.md` rule 7b),
    then delivered findings-only with a stderr warning, the wiki
    unchanged.]
16. **Doc-surface consistency for the defaults.** Every committed surface that
    names the default cadence or the output flag states the post-#25/#27 truth:
    the `--interval` help text and `patrol.py`'s module docstring, the
    `Containerfile`'s loop comment, `ARCHITECTURE.md`'s diagram, `README.md`
    (quickstart commands and config table), and `AGENTS.md`'s run commands. No
    committed surface may carry a stale form: `--interval 900`, "every 15 min"
    or "every 900 s" prose, a `(default 900)` help text, a `PATROL_INTERVAL`
    default of `900` — or a `-v` flag on a `patrol.py` command line (verbose is
    the default now; `-q` suppresses) [DONE: #25, #27].

## Configuration Surface

Environment variables:

- `PATROL_API_KEY` — API key for the OpenAI-compatible endpoint (required)
- `PATROL_BASE_URL` — endpoint URL, e.g. `https://api.example.com/v1` (required)
- `PATROL_MODEL` — model id on that endpoint (required)
- `KUBECTL` — cluster CLI override; otherwise resolved `oc` → `kubectl` → `oc`
- `PATROL_NOTIFY_URLS` — if set, apprise sink URLs (comma/newline separated);
  notifications are also delivered to every configured sink [DONE: #9]
- `PATROL_NOTIFY_DIGEST_AFTER` — periodic-digest threshold in days: a standing
  watching entry older than this, and not notified within this window, is named
  by one process-authored digest; non-negative integer, default `7`, `0`
  disables the digest. Read once at import; invalid/negative fails startup
  [DONE: #35]
- `PATROL_MAX_STEPS` — LLM-turn exploration budget per patrol (default `30`)
- `PATROL_INTERVAL` — seconds between patrols in `--loop` mode (default
  `21600`, six hours) [DONE: #25 — was `900`]
- `PATROL_TOOL_TIMEOUT` — per-command timeout in seconds (default `60`)
- `PATROL_CLUSTER_ID` — cluster identity for wiki commit emails (default `local`)
  [DONE: #3]
- `PATROL_WIKI_REMOTE` — if set, wiki commits are pushed to this git remote
  (best-effort, bounded; the local commit is the source of truth) [DONE: #3];
  in-cluster, the SSH form rides the optional deploy-key wiring
  (`in-cluster-deployment.md` rules 16a–16d [DONE: #39])
- `PATROL_WIKI_STALE_AFTER` — accepted-write threshold for stale-entry expiry:
  a Watch-list entry whose `last_verified` has not advanced for this many
  accepted writes is flipped to `expired` and moved to the Anomaly log
  (default `5`; `0` disables the flip) [DONE: #7]

CLI flags: `--once` (default), `--loop`, `--interval`, `--dry-cluster`, `--wiki`
(default `wiki/cluster-wiki.md` next to `patrol.py` [DONE: #3; was
`cluster-wiki.md`]), `--max-steps`, `-q/--quiet` (suppress the per-turn output
stream, on by default [DONE: #27 — was the opt-in `-v/--verbose`]), and
`--wiki-stats` (print the wiki's four health numbers and exit — offline, no
`PATROL_*` env needed, never runs a patrol [DONE: #8]; plus the #40 size
line: the character count and over-budget warning [DONE: #40]).
In-cluster, the image's default command is `--loop --interval 21600 --wiki
/wiki/cluster-wiki.md` [DONE: #1] [DONE: #25] (`container-image.md`).

Hard-coded constants (not env-configurable): `OUT_CAP` = 15,000 chars of tool output
fed back to the model; model temperature 0.3, completion-token bounds —
`REQUEST_MAX_TOKENS` 16000 per exploration turn, `REPORT_MAX_TOKENS` 64000
per structured reporting/repair call
(`reporting-notifications.md` rule 8b) [DONE: #63 — was a flat
`max_tokens` 16000], request
timeout — `REQUEST_TIMEOUT` 300 s per exploration turn, `REPORT_TIMEOUT`
900 s per structured reporting/repair call
(`reporting-notifications.md` rule 8a) [DONE: #49 — was a flat 300 s on
the whole client]; wiki push bounds — 3 attempts, 30 s per attempt, 5 s
backoff between attempts [DONE: #3]; wiki skeleton schema version `WIKI_VERSION` = 1 [DONE: #5];
wiki stats — churn window `WIKI_CHURN_WINDOW` = 5 revisions, lopsided
thresholds 5 graded entries / 2/3 share [DONE: #8]; wiki working-set budgets —
anomaly-log keep `WIKI_ANOMALY_KEEP` = 20 entries, soft length
`WIKI_CHAR_BUDGET` = 20,000 chars [DONE: #40].

In-cluster wiring (Secret keys, the `PATROL_CLUSTER_ID` literal, kustomize image
override, the CI workflow) is deployment surface, not patrol surface —
`in-cluster-deployment.md` + `container-image.md` rules 13–18.

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
| v0.1 (#1–#4, #9, #11, #24, #25, #27, #28) | In-cluster: Containerfile, manifests (SA + cluster-reader + PVC + Deployment), git-backed wiki, e2e tests in-repo, deployment defaults & observability (6 h cadence, unbuffered + verbose logs, git-safe `/wiki`), guard `--raw` refusal [#9 was later re-scoped to apprise multi-sink (v0.3) with its original dedup scope moved to #35] |
| v0.2 (#5–#8, #20, #39, #40) | Compounding wiki: skeleton schema + validation-on-write, per-entry recheck commands, `stale_after` expiry, `--wiki-stats`, efficient-sweep guidance, deploy-key remote-push wiring, wiki length budget |
| v0.2.1 (#49, #50, #51) | Real-cluster validation fixes: per-call-shape request bounds (300 s exploration / 900 s structured reporting, #49), empty-`updated_wiki` classification — the contract requires the wiki every report, one named retry + stderr warning (#51), one event loop per `--loop` process (#50); in-cluster operational acceptance is deferred to the v0.2.1 release validation. Impl commits sync `AGENTS.md`'s test list |
| v0.3 (#10, #35, #63, #67) | Operational hardening: cost control, state-change notification dedup + periodic digest reminder for long-unfixed entries (#35), per-call-shape completion-token bounds + visible empty-result reasons (rules 7c + 8b), model-facing wiki size feedback so the model prunes the over-budget wiki (rule 29a) |
| future (#12–#16, #41) | Remediation handoff to AgenticRuns, fleet/multicluster, CRD dispatcher, pluggable investigation engine, MCP-memory decision record, external runbook referencing |
