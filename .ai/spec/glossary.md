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
  audit trail (#3). A new repo initializes on its **cluster branch**. An adopted
  repo keeps the branch it has (#89).
- **Cluster branch** — `cluster/<CLUSTER_ID>`, the branch a new wiki repo
  initializes on and pushes to (#89, ADR 0039). `CLUSTER_ID` (from
  `PATROL_CLUSTER_ID`) is a slug. An adopted repo keeps its own branch, for
  example `main`, and pushes to that branch. One remote repo holds one branch
  per cluster; a read-only hub reads each branch's `cluster-wiki.md` at the
  branch tip.
- **Spoke** — a cluster's own patroller process and its wiki repo. A spoke
  writes only its own wiki branch and pushes only that branch (#89).
- **Hub** — a fleet-level reader of the spoke branches in one remote wiki repo
  (#13). The hub is read-only for spoke branches; its own patrol is not
  implemented yet.
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
  recoverable from the wiki repo — and each closed Anomaly-log entry keeps
  at most
  `WIKI_ANOMALY_ENTRY_CHARS` (400) characters of prose, the rest replaced
  by a `_truncated:` marker line that names git history (the **closed-entry
  character cap** [DONE: #86]). A soft whole-wiki character budget
  (`WIKI_CHAR_BUDGET`, 20,000) warns on overshoot (write path, repair
  pass, `--wiki-stats`) without ever failing validation. The wiki is what
  the next patrol reads; the repo is what the auditor reads.
- **Patrol-history prose** — document-level prose replaying what each
  patrol did and when (#85, `wiki-memory.md` rule 29b): a per-patrol
  HEADLINE log, a hand-maintained `History:` list, an "as of patrol #N"
  chronology. Banned — git history is the record of what changed when, so
  the document carries current state only. Deliberately **not** a
  synonym for per-entry hypothesis state: an entry's `first_seen`,
  `last_verified`, `status`, and its persistence notes ("unfixed through
  5th patrol") are current-entry state and stay (rules 7, 12). Taught by
  the report contract (`reporting-notifications.md` rule 5) and the
  patrol prompt (`patrol-engine.md` rule 3f), never validated (ADR 0032).
- **Size note** — the model-facing feedback line (#67): a deterministic
  one-liner appended to the shape pass's user message by
  `wiki_size_note(content, *, budget=None)` (`wiki-memory.md` rule 29a) naming the
  wiki-in-play's character count, the soft budget, and the over-budget
  excess (with a prune target) or the headroom. The process's rule-29
  warning tells the human; the size note tells the model, whose rewrite
  decides.
- **Notification dedup** — notification hygiene on state change, not every
  patrol (#35; `reporting-notifications.md` rules 4a–4e): each proposed
  notification carries a `[<entry id>]` tag and is diffed against the
  old-vs-new wiki — a new entry, severity change, status change, or
  removal delivers; a standing unchanged entry is wiki-only. Untagged or
  unattributable messages deliver fail-open. Process-authored weekly
  digest for long-unfixed watching entries.
- **Severity pair** — the optional `severity: info|warn|crit` pair on an
  entry metadata line (#35, `wiki-memory.md` rule 12b): the current grade
  of the tracked condition, judged when present and compared across
  patrols by notification dedup — a missing value on one side of the diff
  reads as a change (fail-open).
- **Last-notified clock** — the process-owned `last_notified: YYYY-MM-DD`
  pair on an entry metadata line (#35, `wiki-memory.md` rule 12c): the
  patrol start date on which the entry was last named by a delivered
  notification, stamped by `stamp_last_notified` on accepted writes and
  **carried** across rewrites by `carry_last_notified` — the old on-disk
  value restored onto every entry present in both wikis, any model-written
  pair replaced or dropped (#93, ADR 0033). The digest cadence's durable
  state; never model-trusted, never judged by the validator.
- **Digest** — the periodic reminder notification (#35, `reporting-notifications.md`
  rule 4c): at most one process-authored message per patrol naming standing
  watching entries older than `PATROL_NOTIFY_DIGEST_AFTER` days (default 7)
  and not notified within that window — the human surface for a condition
  that outlives the state-change filter.
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
- **Ceiling-aware retry** — the shape pass's attempt 2 when attempt 1 failed at
  the model gateway's ~900 s request ceiling (#88, `reporting-notifications.md`
  rule 7g): it discards the attempt-1 message list, rebuilds its base from the
  raw conversation at the single `SHAPE_TOOL_KEEP` compaction cap, and appends
  a corrective asking for the same `PatrolReport` stated without padding — the
  FULL `updated_wiki`, findings at one line each, no prose around the report —
  `REPORT_CONTRACT` re-stated after it, the rule-29a size note last. Since #97
  the first attempt already carries that cheap shape (500-character tool
  results plus the contract's concision ask), so the retry rebuilds rather than
  shrinks, and its corrective asks for the FULL `updated_wiki` *without
  padding* — never a shorter wiki (rule 5's completeness clause). Non-gateway
  failures keep the append-only retry.
- **First-attempt-cheap shape** (#97, ADR 0034; bound retuned by #101, ADR 0036) —
  the reporting pass's default
  request, not just its recovery: one `SHAPE_TOOL_KEEP` cap (500 characters)
  for every attempt, the `REPORT_CONTRACT` concision ask, and
  `REPORT_MAX_TOKENS` 28000 (4000 above the 24000 bound the v0.3.2 soak
  exhausted, and ~700–800 s of generation at the ~35–40 output tok/s the
  #97 retry measured — inside the gateway's ~900 s window; still above
  #63's ~20k observed completion).
- **Budget cut** — a patrol ended by exhausting `max_steps` LLM turns instead of
  self-terminating; the report then covers only what was observed.
- **Token cut** — the cumulative input-token soft-stop (#10,
  `patrol-engine.md` rule 8b): exploration stopped because the patrol's
  summed input tokens (process-counted from `usage_metadata`) exceeded
  `PATROL_TOKEN_BUDGET` (default 500,000; `0` disables). Shares the budget
  cut's landing exactly — same reporting pass, report from observations so
  far.
- **Soft target** — the prompt's turn target `SOFT_TARGET_TURNS` (20, #10),
  decoupled from the hard `max_steps` budget: the model aims for the target,
  the process enforces the budget. GLM keys off the stated target
  (self-terminated at 34/36 with "~30" stated), so the target is the tuning
  surface and the budget the backstop.
- **Dry-cluster** — demo mode (`--dry-cluster`) where cluster commands are answered
  from canned fixtures instead of a real cluster.
- **Guard** (`kubectl_readonly`) — the single cluster tool: verb whitelist,
  Secret-access ban (all Secret access, names and metadata included),
  raw-path (`--raw`) refusal, local-file/credential-flag refusal, timeout,
  output cap [DONE: #33].
- **Secret-access ban** — the guard's rule that any command containing `secret`
  is refused, regardless of which whitelisted verb is used, output format, or
  position: Secret names and metadata are off-limits just like values
  (`kubectl-guard.md` rules 5–6, ADR 0028) [DONE: #33].
- **Local-file/credential-flag ban** — the guard refuses kubectl flags that
  read local files (`-f`/`--filename`, template-file output formats) or take
  alternate credentials (`--kubeconfig`, token/cert flags), closing the
  `get -f <secret-manifest>` and local-file/credential bypasses
  (`kubectl-guard.md` rule 6b) [DONE: #33].
- **Endpoint-override refusal** — the guard's companion rule for the egress
  channel: `--server`/`-s` (every spelling) is refused, so a guard-accepted
  command always talks to the ambient in-cluster API endpoint
  (`kubectl-guard.md` rule 6c) [DONE: #34].
- **Egress destination** — one outbound host the patroller's own configuration
  names: the model endpoint's host, an apprise sink's host, or the wiki
  remote's host (`egress-control.md` rule 4). Derived by the pure
  `egress_destinations`, never hand-listed — a hand-kept CIDR list beside it
  is a contract violation [DONE: #34].
- **Egress allowlist** — the opt-in default-deny egress `NetworkPolicy`
  (`ols-patroller-egress`) rendered from the egress destinations plus the
  cluster-DNS and kube-apiserver allowances (#34, ADR 0030): a **snapshot** of
  resolved addresses, re-rendered and re-applied after any egress-config
  change (`egress-control.md` rules 8–8c) [DONE: #34].
- **`OUT_CAP`** — hard-coded 15,000-char cap envelope on tool output fed back to
  the model; the truncated view itself is head+tail (`OUT_HEAD` 9,000 /
  `OUT_TAIL` 5,000 around a middle-elision marker) [DONE: #10].
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
  in-cluster manifests deploy the current release tag by default (#2, #102);
  published `v*` releases retag the matching `sha-<SHA>` image with the release
  tag — a pointer, not a rebuild (#36).
- **Release-pinned default** — the manifests' committed image tag is the newest
  published `vX.Y.Z` release, so `oc apply -k manifests/` installs a known
  release (#102; `in-cluster-deployment.md` rules 8/8b, ADR 0035). The pin
  commit merges before the tag is published, and a published tag is never
  moved. `:main` is the local override for testing the latest merge;
  `sha-<SHA>`/digest remains the operator's hardening pin.
- **cluster-reader** — built-in OpenShift ClusterRole (94 rules, zero Secret access)
  planned as the patrol pod's RBAC boundary in-cluster (#2).
- **Deploy key** — an SSH key pair registered with the git host (write access
  to one repo) whose private half lives in a server-side cluster Secret; the
  optional remote-push wiring (#39) mounts it read-only and points
  `GIT_SSH_COMMAND` at it. Opt-in and local-first: the push stays best-effort
  either way.
- **Host-portable text I/O** — the project-wide encoding pin (#77, ADR 0029):
  every file and stream the process owns is UTF-8 without a BOM with LF
  newlines, read and written explicitly rather than through the locale. The
  wiki is the load-bearing case (UTF-8/LF write, explicit UTF-8 read — legacy
  bytes replaced rather than fatal) and stdout/stderr are reconfigured to
  UTF-8/LF at startup. Development runs on Linux and Windows; the suite must be
  green on both.
- **Severity** — finding vocabulary: `info` (note/baseline/expected), `warn`
  (drift/degradation/suspicious), `crit` (broken, needs a human now).
- **Milestones** — v0.1 in-cluster (#1–#4, #9, #11, #24, #25, #27, #28), v0.2
  compounding wiki (#5–#8, #20, #39, #40), v0.2.1 real-cluster validation
  fixes (#49: per-call-shape request bounds; #51: empty-report
  classification; #50: one event loop per loop-mode process), v0.2.3 more
  real-cluster validation fixes (#60: still-empty safety net — wiki-repair
  pass + minimal write; #61: `imagePullPolicy: Always` for the rolling tag;
  #62: the seed passes the gate), v0.3 operational
  hardening (#10; #33's full Secret-access ban in the guard + prompt;
  #35's state-change notification dedup + weekly digest; #9 re-scoped to
  apprise multi-sink; #34's data-exfiltration prevention — the derived
  default-deny egress allowlist and the guard's endpoint-override refusal; #63 — reporting token bound +
  visible empty-result reasons — was filed here but pulled forward and
  closed after the frozen-wiki diagnosis), v0.3.1 reporting reliability
  (#88: the shape pass inside the model gateway's ~900 s ceiling; #85: the
  wiki prose/history payload the pass reads and re-emits), v0.3.2 patch
  (#93: the process-owned notify clock carried across rewrites, so the
  digest cadence holds; #97: the first reporting attempt takes the #88
  retry's cheap shape and `REPORT_MAX_TOKENS` drops to 24000), v0.3.3 patch
  (#101: `REPORT_MAX_TOKENS` retunes to 28000 after the first attempt
  exhausted 24000 completion tokens while the gateway window was not
  binding), v0.4.0 fleet
  memory & wiki usability (#89: one repo, one branch per cluster; #13: fleet
  spoke push/hub read; #26: wiki viewer; #86: anomaly-log character cap; #87:
  process-generated topology diagram; #106: the empty-wiki reason names bound
  truncation), future (#12, #14–#16, #31, #41). Plans live
  in GitHub issues; #9/#11 were promoted into v0.1 (#9's original dedup scope
  later moved to #35) and #20 slotted into v0.2
  after the first in-cluster acceptance run (PR #29 discussion); #39–#41
  were filed from the #5 spec review (PR #38 discussion); #49–#51 were
  filed from the v0.2 real-cluster validation runs; #60–#62 were filed
  from the same runs' follow-up soak; #63 was filed from the
  same runs and slotted into v0.3; #67 — model-facing wiki size feedback
  so the model prunes the over-budget wiki — was filed from the same soak
  and slotted into v0.3; #77 — dev/test host portability (UTF-8/LF text I/O,
  a green suite on Windows) — was filed from the Windows dev environment and
  slotted into v0.3. #85–#89 were filed from the v0.3.0 release soak and the
  #88 recurrence: the reporting-pass gateway ceiling, the over-budget wiki
  (prose/history and anomaly-log size), the multi-wiki single-repo layout,
  and the mermaid-topology question; #85/#88 gate v0.3.1, the rest follow
  in v0.4.0.
