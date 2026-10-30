# kubectl Guard (`kubectl_readonly`)

The single tool the model gets for cluster access, and the primary read-only
enforcement point. Its refusal behavior is part of the agent's contract: the model
sees these messages and recovers based on them.

## Behavioral Rules

### Verb Whitelist

1. `kubectl_readonly` is the only path to the cluster. It takes a single string
   argument containing everything after the CLI name (e.g. `"get pods -A"`).
2. The first whitespace-separated token is treated as the verb. Only these verbs are
   permitted: `get`, `describe`, `logs`, `top`, `version`, `api-resources`,
   `api-versions`, `explain`, `status`, `cluster-info`, `whoami`.
3. Any other verb is refused with a `REFUSED:` message listing the allowed verbs.
   Mutation verbs (`apply`, `delete`, `edit`, `patch`, …) are refused by this rule.
4. An unparseable command (shlex error) or an empty command is refused.

### Secret Access Ban

5. A command is refused if, case-insensitively, the substring `secret` appears
   anywhere in the whitespace-joined parsed arguments — regardless of which
   whitelisted verb is used, the output format, or the syntactic position. **All**
   Kubernetes Secret access is off-limits, not just values: names/metadata reads (`get secrets`, `get secret
   <name>`, `get secret <name> -o name`, `describe secret <name>`, and resource
   forms after flags such as `get -n <ns> secret <name>`) are refused exactly like
   value-bearing forms (`-o yaml`, `-o json`, `-o jsonpath`, `-o go-template`,
   `-o custom-columns`, `--template`, and the `-o=`/`--output=` spellings). One
   rule covers every form; there is no output-format enumeration to bypass
   [PLANNED: #33]. The verb whitelist (rule 3) still runs first: a
   non-whitelisted verb with `secret` keeps its verb refusal.
   - The refusal message is exactly: `REFUSED: Secret access is off-limits — names and metadata included. Do not retry with another output format; inspect workload metadata (get pods, describe deployment, get events) instead.` It never suggests a Secret read as the recovery path.
6. This match is deliberately over-broad (substring on the whole command): it also
   refuses a command that merely contains the word — a workload named
   `my-secret-app`, a namespace/label value containing `secret`, or a jsonpath
   expression naming a `credentialsSecret` field on a non-Secret resource. Strict
   by design: a false refusal costs a recoverable retry, while a miss leaks a
   credential. Do not narrow this rule without updating `safety-model.md` and
   recording an ADR (weakening a safety layer). Scope is Kubernetes Secret
   resources only; broader sensitive-data filtering (ConfigMap values, literal
   env values, Route TLS material, other embedded credentials) is out of scope and
   tracked separately. Incidental Secret *names* inside otherwise-allowed workload
   output (e.g. a pod spec's `secretName`) are not Secret-resource reads and stay
   allowed. [PLANNED: #33].

### Raw-Path Refusal

6a. A command is refused if, case-insensitively, the substring `--raw` appears
    anywhere in the whitespace-joined parsed arguments — the same deliberately
    over-broad substring style as rule 5, so `--raw <path>`, `--raw=<path>`, and
    case variants are all caught. The verb is legal; the endpoint shape is not:
    raw proxy/stats paths (e.g.
    `get --raw /api/v1/nodes/<node>/proxy/stats/summary`) return multi-hundred-KB
    JSON dumps that truncate at `OUT_CAP` and get retried — a real patrol burned
    three turns in exactly that loop. The match is on `--raw`, not the bare word
    `raw`: names like `raw-worker-1` stay allowed. The check runs with the other
    pre-execution refusals — after the verb whitelist, before dry-mode fixture
    lookup and subprocess execution — in both dry and real mode; a command
    matching both the Secret-access ban (rules 5–6) and `--raw` is conformant
    with either refusal message. The message names `--raw`, says raw paths dump
    high-volume output that truncates, and steers recovery to typed read commands
    (`get`/`describe`/`logs`/`top`) instead of retrying raw paths. This rule only
    refuses more than before — a strictness increase, not a guard weakening; any
    future carve-out (an explicit raw-path allowlist) is a weakening and needs an
    ADR in `decisions/` [DONE: #11].

### Local-File & Credential Flags

6b. A command is refused if it uses a flag that makes kubectl read a local file
    or accept alternate credentials — same pre-execution slot as rules 5–6 and
    6a, in both dry and real mode [PLANNED: #33]. The refused forms, judged
    case-insensitively over the parsed arguments:
    - **File input:** `--filename` (space, `=`, or attached forms) and a
      single-dash token in which `f` is parsed as a shorthand *option letter* —
      not when `f` merely occurs in an attached value. Concretely, walk the
      token's leading option letters (after the single `-`, up to `=`): `A`,
      `h`, `w` are boolean and continue the walk; `n`, `o`, `l`, `c`, `p`, `L`,
      `v` take a value and end it; `f` is the file flag. So `-f`, `-f=…`,
      `-fpath`, `-Af`, `-wf`, and `-Afi` are refused, while `-nfoo`,
      `-lapp=frontend`, and `-o=jsonpath={.metadata.labels.foo}` are not.
      Exception: when the verb is `logs`, `f` is boolean `--follow` and
      continues the walk instead; `--filename` is still refused there.
    - **Credential/config input:** `--kubeconfig`, `--token`, `--username`,
      `--password`, `--client-key`, `--client-certificate`,
      `--certificate-authority` (space, `=`, or attached forms).
    - **Template-file output formats:** an `-o`/`--output` value naming a local
      file — `go-template-file`, `jsonpath-file`, `templatefile`,
      `custom-columns-file`. Inline `go-template`, `jsonpath`, and
      `custom-columns` remain allowed.
    The refusal message is exactly: `REFUSED: local-file and credential flags are off-limits — kubectl must use ambient configuration and inline resource names, never read local files or take alternate credentials.` Rationale:
    `get -f <file>` reads a local manifest (or `/proc/self/environ`) and can
    fetch or echo its contents; credential and template-file flags read local
    files too. Inline resource names and inline formatters are the read-only
    forms. A command matching both this rule and the Secret-access ban (rules
    5–6) or `--raw` (rule 6a) is conformant with either refusal message. This is
    a strictness increase, not a weakening; a future carve-out needs an ADR.

### Execution

7. Real commands run via a subprocess argument list (`[kubectl, *args]`) — never
   through a shell.
8. Each command has a timeout (`PATROL_TOOL_TIMEOUT`, default 60 s); a timeout
   returns an `ERROR:` message, not an exception.
9. A missing CLI binary returns a `REFUSED:` message suggesting `--dry-cluster` or
   installing oc/kubectl.
10. stderr is appended to stdout under a `[stderr]` header; empty output becomes
    `(no output)`.

### Output Cap

11. Tool output fed back to the model is capped at `OUT_CAP` = 15,000 chars
    (hard-coded). Truncated output is **head+tail, not head-only** [DONE:
    #10]: the model sees the first `OUT_HEAD` (9,000) chars, an elision
    marker, the last `OUT_TAIL` (5,000) chars, and — after the tail — the #20
    recovery hint naming the narrowing patterns (field selectors
    (`--field-selector`), client-side formatters (`-o jsonpath`,
    custom-columns), or a single-object drilldown). `OUT_HEAD` + `OUT_TAIL`
    (14,000) stays at or under `OUT_CAP`, and the whole appended suffix —
    marker plus hint — stays under 250 chars (a hint anywhere near the cap in
    size would defeat its purpose), so the capped result never exceeds
    14,250 chars. The marker names the true total and the elided middle:
    `...[truncated, {N} chars total: middle {M} chars elided, head+tail kept]`
    with `N` the true output length and `M = N - OUT_HEAD - OUT_TAIL`. The
    hint text is unchanged from #20 [DONE: #20]. Rationale (real-run
    evidence): kubectl tables carry their headers and oldest rows at the
    head; logs and events carry recency at the tail — head-only truncation
    keeps one informative end and destroys the other, and the model burns
    turns re-querying what it cannot see (patrol #6 fought node stats dumps
    in exactly that loop). Head+tail keeps both ends deterministically,
    without a summarization model call (ADR 0027). Dry-mode
    fixture capping stays head-only, marker-free and hint-free (fixture text
    is demo data, not cluster output — the dry-mode cap is this file's rule
    13, the fixture-set rationale is `patrol-engine.md` rules 13–13a).
    Before #10, truncation was head-only with the marker
    `...[truncated, N chars total]` plus the #20 hint [DONE: #20]. This is output shaping only — the refusal
    grammar (rules 2–6b) is untouched, so `guard_refusal` and the recheck
    validity it backs (ADR 0013) are unaffected; the guard never refuses a
    legal read for cost reasons (ADR 0016). The hint complements #10's
    smarter-truncation work and echoes the prompt's sweep teaching
    (`patrol-engine.md` rules 3a–3d): a model that hits the cap is told the
    same narrowing moves the prompt teaches upfront.

### CLI Resolution

12. The cluster CLI resolves as: `$KUBECTL` if set → `oc` if on PATH → `kubectl` if
    on PATH → `oc` (fails loudly at execution if truly absent).

### Dry-Cluster Mode

13. In dry mode no subprocess runs; the fixture table is matched by substring (first
    match wins) and the fixture text is capped at `OUT_CAP`.

## Configuration Surface

- `PATROL_TOOL_TIMEOUT` — per-command timeout in seconds (default 60)
- `KUBECTL` — CLI override (see rule 12)
- `OUT_CAP` — hard-coded 15,000 chars (not env-configurable): the overall cap
  envelope on tool output fed back to the model
- `OUT_HEAD` / `OUT_TAIL` — hard-coded 9,000 / 5,000 chars (not
  env-configurable): the head+tail split of rule 11's truncated output
  [DONE: #10]

## Constraints

- The guard is **defense-in-depth**, not the boundary itself: when deployed
  in-cluster, the pod ServiceAccount's RBAC is the server-side enforcement boundary
  [DONE: #2] (see `safety-model.md`).
- Guard behavior is unit-checked in-repo (`tests/test_kubectl_guard.py`): refusal
  rules 3–6b, execution rules 8–11, CLI resolution rule 12, dry-mode rule 13
  — see `safety-model.md` rule 6a [DONE: #4]. Rule 6a's tests land with its spec
  PR and pass with its implementation [DONE: #11]. The #33 Secret-access and
  local-file/credential-flag matrices live in `tests/test_secret_access.py`
  [PLANNED: #33].
- The refusal grammar (rules 2–6b: parseability, non-empty, verb whitelist,
  Secret-access ban, raw-path refusal, local-file/credential-flag refusal) is
  the single source of truth for
  "a command `kubectl_readonly` accepts". The wiki validator reuses it to
  judge `recheck` citations (`wiki-memory.md` rule 12a) through shared code,
  never by executing the command [DONE: #6].

## Planned Changes

| Ticket | Summary |
|---|---|
| ~~#6~~ | ~~Refusal grammar extracted as a shared pure predicate (`guard_refusal`); the wiki validator judges `recheck` citations with it — guard behavior itself unchanged (`wiki-memory.md` rule 12a)~~ |
| ~~#11~~ | ~~Refuse any command containing `--raw` (rule 6a) — proxy/stats endpoints dump multi-hundred-KB JSON that truncates and gets retried, burning patrol turns~~ |
| ~~#20~~ | ~~Truncation-marker recovery hint (rule 11): output capped at `OUT_CAP` appends, after the unchanged `[truncated, N chars total]` marker, the narrowing patterns (`--field-selector`, `-o jsonpath`/custom-columns, single-object drilldown) — output shaping only, the refusal grammar untouched (ADR 0016)~~ |
| ~~#10~~ | ~~Head+tail truncation (rule 11): truncated output keeps `OUT_HEAD` 9,000 head chars + `OUT_TAIL` 5,000 tail chars around a middle-elision marker naming the true total; the #20 hint unchanged after the tail; dry-mode fixture capping unchanged. Impl commit also syncs `AGENTS.md`'s test list~~ |
| #33 | Secret access is banned in full (rules 5–6): any command containing `secret` is refused regardless of verb/output format/position — names and metadata included, not only values. The local-file/credential channel is closed too (rule 6b): `-f`/`--filename`, `--kubeconfig`/token/cert flags, and template-file output formats are refused, closing the `get -f <secret-manifest>` bypass. The refusal names the ban and steers to workload metadata; over-broad by design (ADR 0028). The prompt teaching is `patrol-engine.md` rule 2a; the report contract teaches the recheck limit (`reporting-notifications.md` rule 5). Impl commit also syncs `README.md`'s guard bullets, `ARCHITECTURE.md`'s guard labels, `AGENTS.md`'s test list, and `REPORT_CONTRACT`'s recheck wording (the model-facing text must say rechecks may not read a Secret — names/metadata included, not only values) |
