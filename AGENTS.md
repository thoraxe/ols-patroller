# OLS Patroller

Proactive, read-only SRE patrol agent for OpenShift/Kubernetes clusters (deepagents
engine, persistent wiki memory). Hacky single-file PoC: `patrol.py`.

## Specs

All specifications live in `.ai/spec/`. Start with `.ai/spec/README.md` for project
overview, reading order, and structure guide. Spec prose follows the Writing rules
there. When you touch a rule, simplify it and its immediate neighbors.

## Workflow — spec-first PRs

All feature work follows the spec-first loop. Specs are the implementation
contract, written so a context-clear agent can implement from them alone; GitHub
issues inform the specs.

| Phase | Command | Gate |
|---|---|---|
| 1. Spec PR — contract rules + failing tests, fresh-AI review loop until clean | `/skill:spec-first <issue or feature>` | **Human adds `spec-approved`** |
| 2. Implementation — context-clear agent makes the tests pass, fresh-AI review loop | `/skill:spec-implement <PR>` (fresh session) | **AI merges** (after clean review + `implementation-complete`; current policy, revocable) |

PR state labels track where a PR is in that loop. The skills manage the AI-review
and ready states. Humans own the `spec-approved` gate; merging is agent-owned
under current policy (clean implementation review + `implementation-complete`):

| Label | Meaning |
|---|---|
| `ai-spec-review` | PR is currently being reviewed by AI (spec phase) |
| `spec-review-required` | Spec passed all AI reviews — ready for human review |
| `spec-approved` | A human reviewed and approved the spec changes (gate to phase 2) |
| `ai-implementation-review` | PR is currently being reviewed by AI (implementation phase) |
| `implementation-complete` | Fully AI-reviewed — the implementing agent merges the PR at this point (current policy, revocable) |

- Every feature PR has exactly **two commits**: (1) specs + failing tests,
  (2) implementation. A **sync merge** of `main` may sit between them when the
  branch is cut before its siblings merge: it is mechanical and content-neutral
  (append-only conflicts resolved to the union of both sides, commit 1 never
  rewritten), and the suite after it must still fail only the PR's own new
  tests — the spec→impl pair stays the PR's diff against `main`.
- Review loops dispatch an independent reviewer — a separate `pi` process with no
  session context; reports land in `.ai/spec/verification/` (gitignored).
- Implementation never changes contract rules — only `[PLANNED: #N]` → `[DONE: #N]`
  flips. If the spec turns out wrong, stop and surface it; spec changes need their
  own review loop.
- Merging: the implementing agent merges once the implementation review is
  APPROVED with zero blocking findings and the PR carries `implementation-complete`
  (merge commit only — squash/rebase would collapse the two-commit structure).
  Current policy, revocable by the human at any time; the `spec-approved` gate
  stays human-only either way.

## Development

- Run (no cluster needed): `uv run patrol.py --once --dry-cluster` — requires
  `PATROL_API_KEY`, `PATROL_BASE_URL`, `PATROL_MODEL` (any OpenAI-compatible
  endpoint). Run it **twice** to see the memory loop.
- Real cluster: `uv run patrol.py --once` (needs `oc`/`kubectl` + kubeconfig).
- Loop mode: `uv run patrol.py --loop --interval 21600`.
- Dependencies are PEP 723 inline metadata in `patrol.py`; `uv` resolves them — no
  separate install step. `pyproject.toml` mirrors them for the test environment
  only (`uv run patrol.py` still uses the PEP 723 script mode).
- The dev Python is pinned in `.python-version` (3.12). Leave it there: `uv` will
  otherwise pick the newest interpreter it can find, and `zstandard` (via
  `deepagents` → `langsmith`) publishes no cp315 wheel — the resolve falls back
  to a source build that needs MSVC, so a bare `uv run` fails before any test
  runs. With the pin, every command below works as written on Linux and Windows.
- Host portability (#77): the suite must be green on Linux **and** Windows with the
  same command and no `-k` exclusions. No test may fail by accident on one host:
  scaffolding is host-portable (fake cluster CLIs are `.cmd`/Python launchers
  rather than POSIX shell scripts; test subprocess envs start from `os.environ`
  and delete `PATROL_*` keys — never a minimal env, because Windows needs
  `SystemRoot`; test wiki fixtures write UTF-8). A test that genuinely asserts
  POSIX-only semantics is explicitly skipped with `pytest.mark.skipif` and a
  reason — the issue permits marking a platform difference, not accidental red.
  The patroller itself owns UTF-8/LF text I/O (`wiki-memory.md` rules 4c/4g,
  `reporting-notifications.md` rule 9b), so contract tests run under a hostile
  locale to prove the pin.
- Tests: `uv run --group dev pytest tests/ -q` — `tests/test_wiki_git.py` covers
  the wiki git persistence contract (#3); `tests/test_container_image.py` covers
  the image contract (#1); `tests/test_kubectl_guard.py` covers the guard
  contract; `tests/test_secret_access.py` covers the Secret-access and
  local-file/credential-flag bans of #33 (every Secret form refused with the
  exact message, the over-broad substring cases, pre-execution/dry-fixture
  preemption, the `-f`/`--filename`/credential/template-file matrix — including
  the combined-shorthand output-value matrix of #79, where the option-letter
  walk ends on `o` and the next-argument or attached value names a
  template-file format — and the per-verb boolean walk of #83, where `p` is
  `--previous` on `logs` so `-po`/`-pfo` reach the `o` while `logs -p`/
  `logs -pf` stay accepted — and the system-prompt + report-contract teaching);
  `tests/test_patrol_deepagents.py`
  is the fake-model e2e over the
  real engine stack (#4, incl. the wiki-skeleton wiring of #5, the recheck
  repair path of #6, the stale-entry expiry wiring of #7, the working-set
  budget wiring of #40 — expire → compact → stamp, prune console lines, the
  soft-budget stderr warning, the repair-pass budget inclusion, and prune
  recoverability from git history — and the request-bound wiring of #49: the
  agent's model built at the exploration bound, the shape pass's at the
  reporting bound, the repair reusing the shape instance — and the
  token-bound wiring of #63: the same two `make_model` calls carrying the
  per-call-shape `max_tokens` pair, agent at the exploration bound, shape
  pass at the reporting bound — and the
  empty-wiki classification wiring of #51: retry-and-recover with the named
  corrective, the still-empty stderr warning with findings preserved, and
  the repair-still-empty fall to the keep-old-wiki outcome — and the
  one-event-loop loop-mode wiring of #50: two patrols through `main()
  --loop` on a single process-lifetime event loop, the failed-patrol
  catch-and-continue on that same loop, and a clean ctrl-c exit — and the
  model-facing wiki size feedback of #67: the report pass's prompt carrying
  the on-disk wiki's deterministic size note after `REPORT_CONTRACT` — and the
  notification-dedup wiring of #35: the four-patrol lifecycle (new → quiet
  standing → escalation → resolution), the digest delivery + `last_notified`
  stamp, and fail-open delivery on the minimal-write leg — and the
  notify-clock carry wiring of #93: the write path restoring the old on-disk
  `last_notified` onto every entry present in both wikis before the stamp (a
  quiet patrol's rewrite carries the OLD date forward, never a fresh stamp),
  and three patrols over dropping rewrites digesting once instead of on every
  other patrol — and the cost-control
  wiring of #10: the user prompt's decoupled soft turn target alongside the
  hard budget, the token soft-stop ending exploration through the budget-cut
  path (report still lands, early stop, dangling-tool-call salvage), and
  `--token-budget 0` disabling the stop — and the current-state prompt
  teaching of #85: the system prompt the model actually receives carrying
  the record-current-state-only clause — no per-patrol headline, no
  `History:` list, no "as of patrol #N" chronology, git history already
  recording what changed when);
  `tests/test_wiki_schema.py` covers the
  wiki skeleton schema + validation-on-write contract (#5) plus the
  per-entry recheck-command contract (#6) plus the process-owned-value
  gate contract of #62 (any value for `cluster_id`/`patrol_count`/
  `last_patrol` passes; `WIKI_SEED` itself passes `validate_wiki`); and
  `tests/test_manifests.py` covers the kustomize manifest contract
  (incl. the deploy-key wiring of #39, the pull-policy invariant of
  #61: a mutable kustomization tag requires `imagePullPolicy: Always`,
  and the storage-class override of #68: the pvc-patch template's
  shape, the gitignored operator copy, and the README procedure; and the
  release-pinned default and local-override procedure of #102: the committed
  default's exact current release tag, the README `Test the latest merge`
  tokens, and the `## Releasing` pin-before-tag order plus the never-move
  rule, and the image-CI side of #102 — the workflow never writes
  `manifests/`);
  and `tests/test_wiki_expiry.py`
  covers the stale-entry auto-expiry contract (#7: the `verified_patrol`
  clock matrix and the expiry flip); and `tests/test_wiki_stats.py` covers
  the wiki health-stats contract (#8: the `wiki_stats` and `wiki_churn`
  matrices, the `wiki_history` window over a real repo, and the offline
  `--wiki-stats` mode); and `tests/test_sweep_efficiency.py` covers the
  efficient-sweep contract (#20: the layered-sweep and field-selection
  teaching in the system prompt, and the dry-cluster fixtures that model the
  taught sweep — the truncation-marker recovery hint is covered in
  `test_kubectl_guard.py`); and `tests/test_wiki_budget.py` covers the wiki
  length-budget contract (#40: the `compact_anomaly_log` and
  `wiki_budget_warnings` matrices, the `wiki_stats` size keys, the
  `--wiki-stats` size line, and the report contract's working-set pins;
  plus the model-facing size-note contract of #67 — the `wiki_size_note`
  matrix (within/over budget, empty content, exactly-at-budget boundary,
  call-time `WIKI_CHAR_BUDGET`), the contract's consequence-chain/shrink
  pins, and the shape-pass prompt wiring across modes with retry
  persistence; plus the no-patrol-history contract of #85 — the
  `REPORT_CONTRACT`'s three pinned clauses and the `SYSTEM_PROMPT` clause
  with its placement before the closing summary guidance; plus the
  plain-prose contract of #111 — the `REPORT_CONTRACT`'s verbatim
  plain-English clause (short sentences, one idea per sentence, the active
  voice, simple tenses, no idioms, one word for one thing, the same word on
  the next patrol, and the excluded machine fields) and its absence from
  `SYSTEM_PROMPT`); and
  `tests/test_request_bounds.py` covers the per-call-shape request-bound
  contract (#49 and #63, plus #88's lowered reporting bound and #101's
  28000 bound — retuned from #97's 24000: the two timeout
  constants, the two completion-token bound constants, the #101
  headroom/gateway-window inequality guard,
  and the `make_model` timeout + `max_tokens` passthrough);
  `tests/test_notifications.py` covers the apprise multi-sink notification
  contract (#9: the `parse_notify_urls` and `report_notify_type` matrices,
  and the `notify` delivery semantics over a fake apprise — the
  unconditional console line, the one-instance-per-invocation and
  per-message `notify` calls with the mapped `notify_type`, the skipped
  refused URL with its stderr line, the never-fatal failure modes, and the
  module-level `NOTIFY_URLS` env wiring); and `tests/test_notification_dedup.py`
  covers the state-change notification-dedup contract (#35: the
  `notification_entry_id` matrix, the `wiki_entry_states` reader (both
  metadata sections, optional severity/last_notified, duplicate-first,
  fence-aware), the `filter_state_change_notifications` matrix (new entry,
  severity change, status change, removal, standing-unchanged suppression,
  untagged/unattributable fail-open, order and `notified_ids`), the
  `digest_notifications` matrix (disabled, age/cadence boundaries,
  resolved/excluded/soon-to-expire, ordering, singular/plural),
  the `stamp_last_notified` matrix, the `carry_last_notified` matrix of #93
  (old clock restored over a dropped or model-invented value, pairs dropped
  where the old wiki carries no clock, present/new/removed/unparseable,
  section move keyed by id, fence awareness, first-occurrence-only, byte
  preservation, idempotence), the `NOTIFY_DIGEST_AFTER` config
  surface, `validate_wiki`'s severity-pair matrix, and the `REPORT_CONTRACT`
  dedup pins); and `tests/test_empty_report.py` covers the empty-report
  contract (#51: the report contract's wiki-required pins and the
  empty-wiki classification matrix over `shape_report` — the named
  corrective retry, the double-empty return, the composition and repair
  sharing, and the generic-retry and zero-findings guards, and the wiki-repair
  mode of #60 (the prompt carrying the old wiki verbatim plus the observed
  findings, the minimal-merge ask, the shared empty-wiki retry) — and the
  #85 narrowing: the contract's narrowed copy-verbatim ask with the
  unqualified ask absent, the empty-wiki corrective repeating the narrowed
  wording, and the wiki-repair prompt's patrol-history drop clause with the
  unqualified preservation ask gone — and the
  empty-result reason classification of #63: the pure
  `classify_empty_result` matrix over fake raw responses and the shape
  pass's attempt-failure log lines carrying the classified reasons and
  (#88) the attempt's token counts via the
  include_raw envelope, with the envelope parsing-error path, plus #101's
  28000 default — retuned from #97's 24000 (and the 20k-floor regression
  guard) — and the `bound 28000 tokens` field on every attempt-failure
  line — and the #106 bound-truncation qualifier of rule 7h: the
  `hit_completion_bound` and `empty_wiki_reason` matrices, the rule-7c
  agreement pin, and the qualified attempt-failure lines across the report,
  validation-repair, and wiki-repair modes); and
  `tests/test_shape_input.py` covers the shape-pass input-compaction and
  failure-visibility contract (#66: the `compact_messages` matrix — tool-result
  truncation with marker over langchain and dict forms, order/roles/non-tool
  text preserved, input unmutated, the `keep_chars` parameter — the
  `SHAPE_TOOL_KEEP` constant pin (500 since #97, with the retired retry cap
  asserted absent), the shape-pass prompt compaction across all
  modes with the retry corrective appended after, the `classify_exception`
  matrix over 5xx/timeout vs 4xx/plain, and the elapsed-seconds log-line pin;
  the total-failure tail wiring — one FAILURE notification with the pinned
  message plus the rule-33 minimal write, fresh-deployment and on-disk legs —
  and the flipped attempt-failure log-line pins land in
  `test_patrol_deepagents.py` and `test_empty_report.py`), plus the
  ceiling-aware-retry contract of #88 as narrowed by #97 (one `SHAPE_TOOL_KEEP`
  cap, 500 characters, governs every attempt of every mode; the gateway-failure
  retry rebuilds rather than shrinks, at that same cap, and appends the reworded
  pinned ceiling corrective before the contract with the size note last — across
  the report, validation-repair, and wiki-repair modes — while the non-gateway
  retry stays append-only at the same 500-char base; the contract's pinned
  concision ask on every attempt; and the `?` token counts + `bound 28000
  tokens` field of the exception leg); and
  `tests/test_cost_control.py` covers the cost-control contract (#10:
  the `SOFT_TARGET_TURNS`, `TOKEN_BUDGET`, and `OUT_HEAD`/`OUT_TAIL`
  constants, the `--token-budget` CLI flag + `PATROL_TOKEN_BUDGET` env
  fallback, and the guard's head+tail truncation matrix — both ends keep
  their markers, the elision marker names the true total and the elided
  middle, the #20 hint follows the tail, and the length bound); and
  `tests/test_host_portability.py` covers the host-portable text-I/O
  contract (#77: the `write_wiki_atomic` UTF-8/LF/no-BOM byte pin under a
  hostile locale, the `--wiki-stats` UTF-8 read + UTF-8/LF stdout under a
  hostile ASCII locale, the lenient decode of a legacy non-UTF-8 wiki,
  `wiki_history`'s empty-list degradation on an undecodable blob, the
  `patrol_free_env` no-`PATROL_*` guarantee, and the `.python-version`
  3.12 dev-pin guard; `wiki-memory.md` rules 4c/4g/27,
  `reporting-notifications.md` rule 9b, ADR 0029). The e2e adds the two
  legacy legs: a cp1252 wiki loads at patrol start and a total-failure patrol
  over one writes nothing (`wiki-memory.md` rule 31); and
  `tests/test_egress_control.py` covers the data-exfiltration-prevention
  contract (#34, ADR 0030): the `egress_destinations` matrix (per-channel host
  derivation — the model URL, host-like vs token-in-netloc apprise sinks, the
  scp-like and `scheme://` wiki remotes — plus dedup/order/notes), the
  `resolve_hosts` matrix (literals passed through, `ValueError` naming an
  unresolvable host, `localhost` resolving without a network), the
  `render_egress_policy` shape (podSelector, `policyTypes`, the DNS rule over
  `EGRESS_DNS_NAMESPACES`, apiserver peer present/absent, `/32`+`/128`,
  byte-stability), the `--egress-policy` subprocess contract (stdout is YAML
  only, `PATROL_BASE_URL` the one required input, the `--loop`/`--wiki-stats`
  argparse conflicts, empty stdout on failure, no repo init), the guard's
  endpoint-override refusal (every `--server`/`-s` spelling, dry-mode and
  pre-execution preemption, `--server-print` still accepted — including the
  #83 per-verb boolean walk, where `logs -ps`/`-fps`/`-pfs=` are refused like
  `logs -s`), and the static
  pins (committed template shape, gitignored operator copy, README procedure,
  no `egress-networkpolicy` reference in `patrol.py`).
- `wiki/` (the wiki directory: `cluster-wiki.md` + its git repo) is a runtime
  artifact (gitignored) — never commit it.
- Roadmap: GitHub issues + milestones, not documents. Spec files mark planned
  behavior as `[PLANNED: #N]`.
