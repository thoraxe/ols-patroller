# Glossary

Domain vocabulary for OLS Patroller. General Kubernetes/OpenShift terms are not
redefined here.

- **Patrol** — one run of the agent loop: explore the cluster read-only, validate
  against the wiki, produce a `PatrolReport`, rewrite the wiki. Runs once or on an
  interval.
- **Wiki** (`cluster-wiki.md`) — the agent's persistent memory: a markdown file the
  agent reads and wholly rewrites each patrol. Hypotheses, not truth.
- **Wiki repo** — the local git repository in the wiki directory (the wiki
  file's parent dir; in-cluster, the `/wiki` PVC mount), owned by the patroller:
  one wiki-changing patrol, one commit. The wiki's irreversibility insurance and
  audit trail (#3).
- **Watch-list** — wiki section holding the refinement loop: rules patrol N writes
  for patrol N+1 (what to re-check, escalate, or expire) — each entry carrying
  its `recheck` command (#6); entries not re-verified within
  `PATROL_WIKI_STALE_AFTER` accepted writes are auto-expired by the patroller
  (#7).
- **Skeleton** — the wiki's machine-checkable structure (#5): YAML frontmatter
  (`wiki_version`, `cluster_id`, `patrol_count`, `last_patrol`), the five canonical
  sections, and the entry metadata line in Watch-list and Anomaly log. Structure
  frames free prose; it never replaces it (load-bearing constraint of #5).
- **Entry metadata line** — the first line of a Watch-list/Anomaly-log entry:
  `id · status · first_seen · last_verified · source · confidence` (plus optional
  additional pairs — e.g. `recheck`, #6) as `key: value` pairs separated by ` · ` —
  the machine-checkable part of an entry; free prose follows as its continuation
  lines.
- **Recheck** — the `recheck:` pair on an entry metadata line (#6): the exact
  `kubectl_readonly` argument string that re-verifies the entry — the wiki's
  citation made regenerable (the command that regenerates the evidence).
  Required on Watch-list entries, optional in the Anomaly log, and
  guard-bounded: a valid recheck is a command the guard would accept, never
  refuse (ADR 0013).
- **`verified_patrol`** — the process-stamped pair on an entry metadata line
  (#7): the frontmatter `patrol_count` of the accepted write that last
  verified the entry (the write where its `last_verified` last advanced). The
  staleness clock's state — maintained by the patroller on every accepted
  write, never model-trusted, never judged by the validator.
- **Expiry flip** — the process-enforced retirement of a Watch-list entry
  whose staleness reached `PATROL_WIKI_STALE_AFTER` (#7): `status` flips to
  `expired`, the whole item moves to the end of the Anomaly log with an
  `auto-expired` annotation line — history kept, never deleted (post-#40:
  kept in the working set until the anomaly-log budget prunes it — the
  **working set** entry; git history retains it either way); housekeeping
  logged to the console, never a finding or notification.
- **Working set** — the wiki's bounded present tense (#40): git history is
  the archive, the wiki is the working set. The Anomaly log keeps at most
  the last `WIKI_ANOMALY_KEEP` (20) resolved/expired entries — the
  patroller prunes older ones on every accepted write, whole items,
  recoverable from the wiki repo — and a soft whole-wiki character budget
  (`WIKI_CHAR_BUDGET`, 20,000) warns on overshoot (write path, repair
  pass, `--wiki-stats`) without ever failing validation. The wiki is what
  the next patrol reads; the repo is what the auditor reads.
- **Stats mode** — `--wiki-stats` (#8): the offline command that prints the
  wiki's health numbers — #8's four (verification rate, resolution ratio,
  confidence spread, churn) plus #40's size line — and exits; no model, no
  cluster, no `PATROL_*` env, no writes; the document numbers read the wiki
  file, churn reads the wiki repo's history.
- **Verification rate** — health number 1 (#8): the share of Watch-list
  entries whose `verified_patrol` equals the frontmatter `patrol_count` —
  verified by the most recent accepted write (rule 20's clock, surfaced).
- **Resolution ratio** — health number 2 (#8): the watching / resolved /
  expired counts over the entries of both metadata sections — expired entries
  read straight from the Anomaly log.
- **Confidence spread** — health number 3 (#8): the high / medium / low counts
  over both metadata sections, with a lopsided warning when one level holds
  ≥ 2/3 of ≥ 5 graded entries — a grade everyone gets is not a grade.
- **Churn** — health number 4 (#8): paraphrase-drift — entries whose prose
  changes in every revision of the examined git window with no status change
  (the photocopy-of-a-photocopy effect: the model rewording rather than
  learning). Prose-only comparison over the last `WIKI_CHURN_WINDOW` (5)
  commits; process stamps and date advances never count as rewording.
- **Stamp** — the patroller's deterministic post-validation rewrite of the
  process-owned frontmatter fields (`patrol_count`, `last_patrol`, `cluster_id`)
  before the atomic write (#5). Models are unreliable counters; the stamp makes
  the written values authoritative regardless of what the model emitted.
- **Repair pass** — the single allowed re-invocation of the shape pass when a
  report's `updated_wiki` fails skeleton validation; it carries the validation
  errors and the rejected wiki back to the model (#5).
- **Wiki-repair pass** — the shape pass's third mode (#60): after the
  #51 still-empty outcome, one structured call carrying the old on-disk wiki
  verbatim plus the observed report's findings, asking for a minimal merge —
  the old wiki rewritten with the findings folded in. Its own findings are
  never delivered (it never saw the cluster); only its wiki is used.
- **Minimal write** — the model-free fallback behind the wiki-repair pass
  (#60, ADR 0022) and behind total reporting failure (#66, ADR 0024, rule
  33): when both model attempts fail to emit a wiki — or the reporting
  pass fails wholesale — the
  patrol runs the accepted-write tail on the old wiki verbatim — content
  stale, `patrol_count`/`last_patrol`/`verified_patrol` clocks honest, so
  #7's expiry keeps working instead of freezing. No frontmatter block in the
  old wiki → no write at all.
- **Shape pass** — the tool-free structured-output pass (`shape_report`) that
  converts a finished patrol conversation into the typed `PatrolReport`. Same
  pattern as the OLS agentic sandbox.
- **Budget cut** — a patrol ended by exhausting `max_steps` LLM turns instead of
  self-terminating; the report then covers only what was observed.
- **Dry-cluster** — demo mode (`--dry-cluster`) where cluster commands are answered
  from canned fixtures instead of a real cluster.
- **Guard** (`kubectl_readonly`) — the single cluster tool: verb whitelist,
  secret-value refusal, raw-path (`--raw`) refusal, timeout, output cap.
- **`OUT_CAP`** — hard-coded 15,000-char cap on tool output fed back to the model.
- **AgenticRun** — OLS-side governed execution CR (sandbox + approval + derived RBAC
  + audit). The patrol reads in-flight runs for dedup [today: best-effort] and is
  planned to dispatch remediation to them (#12).
- **OLS** — OpenShift Lightspeed, the Red Hat AI assistant for OpenShift. This
  project shares its engine choice (deepagents) but imports no OLS code.
- **OLS-4439** — the Jira research spike tracking this PoC.
- **deepagents** — upstream langchain-ai library providing the agent graph
  (`create_deep_agent`) the patrol runs on.
- **GLM** — the model family used in real-run tuning evidence (GLM 5.3 via EnMaaS);
  source of the "never self-terminates under forced tool choice" finding.
- **EnMaaS** — the model-endpoint hosting service used for real-cluster runs.
- **Container image** — the ubi9-minimal image built from the repo-root
  `Containerfile` that runs `patrol.py` in-cluster (#1); dev mode stays
  `uv run patrol.py` via the PEP 723 header.
- **GHCR** — GitHub Container Registry (`ghcr.io`); merges to `main` publish
  the patrol image there (`main` rolling tag + `sha-<SHA>` pins) and the
  in-cluster manifests deploy it (#2); published `v*` releases retag the
  matching `sha-<SHA>` image with the release tag — a pointer, not a rebuild
  (#36).
- **cluster-reader** — built-in OpenShift ClusterRole (94 rules, zero Secret access)
  planned as the patrol pod's RBAC boundary in-cluster (#2).
- **Deploy key** — an SSH key pair registered with the git host (write access
  to one repo) whose private half lives in a server-side cluster Secret; the
  optional remote-push wiring (#39) mounts it read-only and points
  `GIT_SSH_COMMAND` at it. Opt-in and local-first: the push stays best-effort
  either way.
- **Severity** — finding vocabulary: `info` (note/baseline/expected), `warn`
  (drift/degradation/suspicious), `crit` (broken, needs a human now).
- **Milestones** — v0.1 in-cluster (#1–#4, #9, #11, #24, #25, #27, #28), v0.2
  compounding wiki (#5–#8, #20, #39, #40), v0.2.1 real-cluster validation
  fixes (#49: per-call-shape request bounds; #51: empty-report
  classification; #50: one event loop per loop-mode process), v0.2.3 more
  real-cluster validation fixes (#60: still-empty safety net — wiki-repair
  pass + minimal write; #61: `imagePullPolicy: Always` for the rolling tag;
  #62: the seed passes the gate), v0.3 operational
  hardening (#10 plus #35's digest; #9 re-scoped to apprise multi-sink; #63 — reporting token bound + visible
  empty-result reasons — was filed here but pulled forward and closed after
  the frozen-wiki diagnosis), future (#12–#16, #41). Plans live in
  GitHub issues; #9/#11 were promoted into v0.1 (#9's original dedup scope
  later moved to #35) and #20 slotted into v0.2
  after the first in-cluster acceptance run (PR #29 discussion); #39–#41
  were filed from the #5 spec review (PR #38 discussion); #49–#51 were
  filed from the v0.2 real-cluster validation runs; #60–#62 were filed
  from the same runs' follow-up soak; #63 was filed from the
  same runs and slotted into v0.3.
