# 0028. Secret access is banned in full, not just Secret values

Date: 2026-10-30 · Status: proposed (issue #33)

## Context

#33 requires the patroller wrapper to ensure access to secrets is blocked, with
broader sensitive-data filtering called out as future work. The guard
(`what/kubectl-guard.md`) refused a Secret read only when the command contained
both `secret` and one of four output-format substrings (`-o yaml`, `-o json`,
`jsonpath`, `-o go-template`), and deliberately allowed `get secrets` /
`describe secret` as names/metadata-only reads.

That value-format enumeration is not an access boundary. A Secret's data can be
serialized through spellings the old check missed — `-o custom-columns`,
`--template`, `-o=json`, `--output=yaml` — and metadata reads still disclose
Secret names, which the patrol does not need. Enumerating formats is also a
maintenance trap: every kubectl formatter is a potential hole, and a miss leaks
a credential. The deployed RBAC boundary (`cluster-reader`, zero Secret access)
is defense-in-depth, not a reason to leave the standalone/kubeconfig case weak.

The agent's other tool, `read_file`, is the read-file-only in-memory middleware
(`what/safety-model.md` rule 3), so local files, process environment, and the
patrol's own credentials are not reachable through it. But
the kubectl subprocess can read local files: `get -f <file>` reads a local
manifest and can fetch or echo `/proc/self/environ` or a mounted deploy key, and
`--kubeconfig`/token/cert flags and template-file output formats read local
files or alternate credentials. The cluster tool is therefore a second
secret-access channel, and this decision closes both the Secret-resource and
local-file/credential forms.

## Decision

Ban **all** Kubernetes Secret access in `guard_refusal`: any command whose
whitespace-joined parsed arguments contain the case-insensitive substring
`secret` is refused, regardless of which whitelisted verb is used, output
format, or position (resource before or after flags). Names and metadata are
refused exactly like values. Local-file and credential flags are refused too:
`-f`/`--filename` (except `logs -f`, which is follow; the shorthand is parsed as
an option letter, so attached values containing `f` like `-nfoo` are not file
flags), `--kubeconfig`, `--token`/`--username`/`--password`/cert flags, and the
`go-template-file` / `jsonpath-file` / `templatefile` /
`custom-columns-file` output formats. The
match is deliberately over-broad — a workload named `my-secret-app` or a
`credentialsSecret` field in a non-Secret jsonpath is refused too — because a
false refusal is a recoverable retry while a miss is a credential leak. The
refusal message states the ban (names/metadata included), forbids format
retries, and steers the model to workload metadata (`get pods`,
`describe deployment`, `get events`). The system prompt teaches the same rule so
the model does not waste turns discovering it (`what/patrol-engine.md` rule 2a).

Scope is Kubernetes Secret resources only. Incidental Secret *names* that appear
inside otherwise-allowed workload output (e.g. a pod spec's `secretName`) are
not Secret-resource reads and remain allowed. Broader sensitive-data filtering —
ConfigMap values, literal environment values, Route TLS material, and other
embedded credentials — is explicitly deferred; it needs its own spec and cannot
be smuggled into this ban by widening the substring.

## Consequences

- `get secrets`, `get secret <name>`, `get secret <name> -o name`, and
  `describe secret` now return `REFUSED:`; the old names/metadata allowance and
  the tests that pinned it flip in the spec PR.
- A wiki `recheck:` citation that reads a Secret becomes invalid (the validator
  shares `guard_refusal`, ADR 0013). That is the intended coupling: memory can
  no longer teach a command outside the guard.
- The refusal fires before dry-mode fixture lookup and subprocess execution, so
  neither a canned fixture nor a real CLI can answer a Secret command or read a
  local file through a file/credential flag.
- Inline `go-template`, `jsonpath`, and `custom-columns` formatters stay allowed;
  only their `*-file` variants are refused. `logs -f` (follow) stays allowed.
- The rule only refuses more than before — a strictness increase, not a guard
  weakening. Narrowing it (e.g. re-allowing metadata reads) is a safety-layer
  weakening and requires a new ADR.
- Broader sensitive-data filtering remains future work; the substring ban is not
  an attempt to classify sensitive fields in non-Secret resources.
