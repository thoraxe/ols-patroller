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

### Secret-Value Protection

5. A command is refused if, case-insensitively, the string `secret` appears anywhere
   in it **and** any of `-o yaml`, `-o json`, `jsonpath`, `-o go-template` appears
   anywhere in it. Reading Secret *values* is off-limits; `get secrets`
   (names/metadata only) remains allowed.
6. This match is deliberately over-broad (substring on the whole command): it also
   catches e.g. jsonpath extraction of a `credentialsSecret` field from a non-Secret
   resource. Observed in a real patrol — the model recovered via a plain
   `get -o yaml`. Strict by design; do not narrow without updating `safety-model.md`.

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
    matching both a secret-value pattern (rules 5–6) and `--raw` is conformant
    with either refusal message. The message names `--raw`, says raw paths dump
    high-volume output that truncates, and steers recovery to typed read commands
    (`get`/`describe`/`logs`/`top`) instead of retrying raw paths. This rule only
    refuses more than before — a strictness increase, not a guard weakening; any
    future carve-out (an explicit raw-path allowlist) is a weakening and needs an
    ADR in `decisions/` [PLANNED: #11].

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
    (hard-coded); truncated output appends `...[truncated, N chars total]`.

### CLI Resolution

12. The cluster CLI resolves as: `$KUBECTL` if set → `oc` if on PATH → `kubectl` if
    on PATH → `oc` (fails loudly at execution if truly absent).

### Dry-Cluster Mode

13. In dry mode no subprocess runs; the fixture table is matched by substring (first
    match wins) and the fixture text is capped at `OUT_CAP`.

## Configuration Surface

- `PATROL_TOOL_TIMEOUT` — per-command timeout in seconds (default 60)
- `KUBECTL` — CLI override (see rule 12)
- `OUT_CAP` — hard-coded 15,000 chars (not env-configurable)

## Constraints

- The guard is **defense-in-depth**, not the boundary itself: when deployed
  in-cluster, the pod ServiceAccount's RBAC is the server-side enforcement boundary
  [DONE: #2] (see `safety-model.md`).
- Guard behavior is unit-checked in-repo (`tests/test_kubectl_guard.py`): refusal
  rules 3–6 and 6a, execution rules 8–11, CLI resolution rule 12, dry-mode rule 13
  — see `safety-model.md` rule 6a [DONE: #4]. Rule 6a's tests land with its spec
  PR and pass with its implementation [PLANNED: #11].

## Planned Changes

| Ticket | Summary |
|---|---|
| #11 | Refuse any command containing `--raw` (rule 6a) — proxy/stats endpoints dump multi-hundred-KB JSON that truncates and gets retried, burning patrol turns |
