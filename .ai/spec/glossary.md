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
  audit trail (planned, #3).
- **Watch-list** — wiki section holding the refinement loop: rules patrol N writes
  for patrol N+1 (what to re-check, escalate, or expire).
- **Shape pass** — the tool-free structured-output pass (`shape_report`) that
  converts a finished patrol conversation into the typed `PatrolReport`. Same
  pattern as the OLS agentic sandbox.
- **Budget cut** — a patrol ended by exhausting `max_steps` LLM turns instead of
  self-terminating; the report then covers only what was observed.
- **Dry-cluster** — demo mode (`--dry-cluster`) where cluster commands are answered
  from canned fixtures instead of a real cluster.
- **Guard** (`kubectl_readonly`) — the single cluster tool: verb whitelist,
  secret-value refusal, timeout, output cap.
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
- **cluster-reader** — built-in OpenShift ClusterRole (94 rules, zero Secret access)
  planned as the patrol pod's RBAC boundary in-cluster (#2).
- **Severity** — finding vocabulary: `info` (note/baseline/expected), `warn`
  (drift/degradation/suspicious), `crit` (broken, needs a human now).
- **Milestones** — v0.1 in-cluster (#1–#4), v0.2 compounding wiki (#5–#8), v0.3
  operational hardening (#9–#11), future (#12–#16). Plans live in GitHub issues.
