# Reporting & Notifications

The output contract of every patrol: the typed `PatrolReport`, the severity
vocabulary, and how notifications reach a human. Implementation (the shape pass) is
in `how/reporting-pipeline.md`.

## Behavioral Rules

### Report Schema

1. Every completed patrol yields a `PatrolReport` with exactly three fields:
   `findings` (list of `Finding`), `notifications` (list of strings), and
   `updated_wiki` (full new markdown content of the wiki).
2. A `Finding` is: `severity` + `summary` (short, human-readable) + `evidence`
   (short citation of what was actually observed; defaults to empty).
3. Severity vocabulary: **info** = note / baseline / expected state; **warn** =
   drift / degradation / something suspicious; **crit** = broken, needs a human now.
4. `notifications` contains **only** the warn/crit items a human should actually see
   (don't cry wolf), as human-readable messages.
5. `updated_wiki` is the FULL new wiki content in the skeleton format
   (`wiki-memory.md` rules 10–13): frontmatter with the four keys, the five
   canonical sections, entry metadata lines in Watch-list and Anomaly log, a
   guard-acceptable `recheck` command on every Watch-list entry (rule 12a)
   [DONE: #6], free prose inside entries, stale entries expired. The report
   contract (`REPORT_CONTRACT`) instructs the model in this format — the
   frontmatter keys, the section set, and the entry metadata line [DONE: #5],
   and the recheck requirement with its tool-argument form (no leading
   `kubectl`/`oc`; validated against the guard's refusal grammar)
   [DONE: #6]. [DONE: #7 — the contract also carries the staleness
   clock's model-facing half: entry metadata lines may carry a
   `verified_patrol` pair that the patroller re-stamps after validation
   (copy or drop it freely, never compute it); `last_verified` advances only
   when the entry was actually re-verified this patrol (it is the signal the
   patroller's clock reads); and a watching Watch-list entry whose
   `last_verified` stops advancing is auto-expired by the patroller after
   `PATROL_WIKI_STALE_AFTER` accepted writes — re-verify what is worth
   keeping, resolve or expire the rest deliberately
   (`wiki-memory.md` rules 20–22).]
   [DONE: #40 — the contract also carries the working-set expectations:
   the wiki is a working set and git history is the archive, so summarize
   and prune rather than accumulate — rewrite entries tighter instead of
   appending new prose on top of old; keep the whole wiki within the soft
   budget (`WIKI_CHAR_BUDGET` characters, stated in the contract); the
   Anomaly log keeps only the last `WIKI_ANOMALY_KEEP` resolved/expired
   entries (the patroller prunes older ones) — never hoard closed history
   there (`wiki-memory.md` rules 28–29). The contract's existing “expire
   stale entries rather than deleting history” sentence is reworded
   accordingly: prose-section entries are still expired, never deleted, by
   the model — pruning closed Anomaly-log history is the patroller's job.]
   [DONE: #51 — the contract also requires the wiki on every report.
   The contract sentence, verbatim (the executable pins live in
   `test_empty_report.py`):
   "updated_wiki is required on every report: return the FULL wiki
   content even when nothing needs changing — copy unchanged sections
   verbatim. An empty updated_wiki is never a valid answer: a patrol with
   nothing to change returns the wiki unchanged."
   The schema field keeps its lenient default (an omitted field parses
   to `""`), so a wiki-omitting response still parses and its findings
   survive: the process, not the parser, enforces the requirement
   (rule 7b).]

### Reporting Pass

6. The report is produced by a **tool-free structured-output pass** over the finished
   patrol conversation — never by an in-agent forced response format (see
   `patrol-engine.md` Constraints).
7. The pass retries once on an invalid or empty result (a corrective user message is
   appended); total failure means no report, a warning is logged, and the wiki is
   left unchanged.
7a. A report whose `updated_wiki` fails skeleton validation triggers **one repair
    pass**: the shape pass re-invoked with the validation errors and the rejected
    wiki. The repaired report replaces the first wholesale when its wiki
    validates; otherwise the first report's findings and notifications stand and
    the wiki is left unchanged (`wiki-memory.md` rules 15–19). Delivery — findings
    printing, notification — runs after the validation/repair decision resolves
    and before the physical wiki write, so exactly one report, the final one, is
    delivered [DONE: #5].
7b. **Empty-wiki classification** [DONE: #51]. A parsed result whose
    `updated_wiki` is empty is a **failed reporting attempt**, not a valid
    report — the patrol cannot distinguish an omitted field from a
    deliberate no-op, so the contract removes the no-op meaning entirely
    (rule 5: "nothing changed" is expressed by returning the wiki
    unchanged, never by omitting it). The pass retries once with a
    corrective user message that names the empty `updated_wiki` and
    demands the full content — a message distinct from rule 7's generic
    invalid-report corrective. A retry that returns a wiki recovers
    normally — and that retry's report is the patrol's report: it replaces
    the first attempt wholesale (findings, notifications, wiki), the same
    wholesale replacement a successful rule-7a repair uses. When the retry
    also comes back wiki-less the report is
    **not discarded**: its findings and notifications still deliver (a
    wiki omission must never cost the patrol's observations), the wiki is
    left unchanged, and one stderr warning names the empty-wiki condition
    — verbatim: `[patrol] WARNING: reporting pass returned an empty
    updated_wiki; keeping the previous wiki, writing nothing` — the no-op
    is visible and countable, never silent. The
    classification keys on the wiki field alone: zero findings is a legal
    outcome (a clean cluster) and never triggers it. The repair pass
    (rule 7a) shares the classification — a repaired report with an empty
    wiki is retried the same way before the keep-old-wiki outcome
    applies. A repair that stays wiki-less falls to that keep-old-wiki
    outcome: the first report's findings and notifications deliver and
    the wiki-rejection warning fires (`wiki-memory.md` rule 16) — the
    first report's wiki was non-empty (it failed validation, not
    emission), so the empty-wiki warning does not. Across failure
    compositions the pass returns the **last
    result that parsed** — a wiki-less report included — and `None` only
    when no attempt parsed (rule 7's total failure): an empty-wiki
    attempt followed by an exception still delivers the parsed report's
    findings with the empty-wiki warning.
    [DONE: #60 — the still-empty outcome is no longer the end of the
    write path: the patrol tail runs the wiki-repair pass (rule 7d) and,
    behind it, the minimal-write fallback (`wiki-memory.md` rules 30–32).
    The warning above still fires first, verbatim.]
7c. **Visible empty-result reasons** [DONE: #63]. A failed attempt's
    log line names its cause. The pass invokes the structured output with
    `include_raw=True`, so a `None`/unparsed result carries the raw model
    response — and the reason string in the attempt-failure log line
    (`[patrol] reporting pass attempt N failed (<reason>)`, rules 7/7b)
    is derived from it by a pure classifier,
    `classify_empty_result(raw, *, max_tokens=REPORT_MAX_TOKENS)`:
    - `empty result (truncated at max_tokens)` — the raw response's
      `finish_reason` is `"length"`, or its completion-token count
      (`usage_metadata`'s output tokens, reasoning included) reached the
      bound: the model ran out of budget before emitting the tool call
      (the #63 frozen-wiki root cause — on a reasoning model the entire
      combined reasoning+output budget can be spent thinking).
    - `empty result (no tool call)` — the model finished
      (`finish_reason` anything else, tokens under the bound) without
      emitting the PatrolReport tool call.
    The classifier is pure (no I/O, no model calls) and duck-typed over
    the raw message (`response_metadata["finish_reason"]`,
    `usage_metadata["output_tokens"]`). Exceptions keep today's behavior
    — the reason is the exception's string — and a structured-output
    parsing error reported in the raw envelope (rather than raised) is
    treated as the exception it replaces. The empty-wiki classification
    (rule 7b) is untouched: a parsed report with an empty wiki keeps its
    `empty wiki` reason.
7d. **Wiki-repair pass** [DONE: #60]. The shape pass's third
    invocation mode: `wiki_repair=(old_wiki, findings, notifications)`.
    Its user message states that the reporting pass could not emit the
    wiki and carries the old on-disk wiki verbatim plus the observed
    report's findings and notifications, asking for a corrected
    `PatrolReport` whose `updated_wiki` is the old wiki rewritten
    **minimally** — the findings merged in, everything else preserved.
    The mode shares everything with the pass's other modes: the
    `PatrolReport` binding, the reporting bounds (rules 8a/8b), and the
    internal parse/empty retry (rules 7/7b — a wiki-repair result with
    an empty wiki is retried with the named corrective before the
    caller's fallback applies). When the tail runs this mode, the rule
    that the call's own findings and notifications are never delivered
    (it never saw the cluster), and the minimal-write fallback behind
    it, are `wiki-memory.md` rules 30–32.
8. A budget-cut report must cover only what was actually observed; the pass is told
   which commands never executed.
8a. **Per-call-shape request bounds** [DONE: #49]. The two call shapes
    carry different HTTP request bounds, both hard-coded constants (not
    env-configurable): exploration turns are bounded at `REQUEST_TIMEOUT`
    (300 s — turns are small and fast, so a hung endpoint fails snappily),
    while this pass and its repair pass (rule 7a) are bounded at
    `REPORT_TIMEOUT` (900 s) — the single tool-free structured call
    carries the entire patrol conversation (real-run evidence: ~100k
    tokens in, up to 16k out at the time — 64k since #63, rule 8b) and
    legitimately runs minutes on
    reasoning-capable models. A reporting pass that cannot finish under
    the bound never writes the wiki, so the next patrol re-explores just
    as hard and the patroller never converges its memory — a fresh
    cluster never bootstraps; the shape pass therefore gets the headroom
    the exploration turns do not need (ADR
    `decisions/0018-reporting-pass-own-request-bound.md`).
8b. **Per-call-shape completion-token bounds** [DONE: #63]. The two
    call shapes carry different `max_tokens` bounds too, both hard-coded
    constants (not env-configurable): exploration turns keep
    `REQUEST_MAX_TOKENS` (16000 — turns are small), while this pass and
    its repair pass (rule 7a) are bounded at `REPORT_MAX_TOKENS`
    (64000). The shape pass must emit the entire wiki — ~15 KB of
    markdown — in one structured response, and on a reasoning model the
    reasoning tokens come out of the same `max_tokens` allowance
    (real-run evidence, the frozen-wiki root cause: a 43.5k-token report
    prompt spent all 16000 completion tokens reasoning,
    `finish_reason=length`, zero content, twice per patrol — no wiki
    write for ~14 patrols; the same run with a 64k reporting budget
    completed at ~20k completion tokens and committed). `max_tokens` is
    a client-level property like the timeout, so it parametrizes the
    same `make_model` seam (default the exploration bound; the shape
    pass passes the reporting bound) — the reporting model carries both
    reporting bounds (ADR
    `decisions/0021-reporting-pass-own-token-bound.md`).

### Delivery

9. Findings print to the console with a severity column; each finding's evidence
   prints with them unless suppressed by `--quiet` [DONE: #27 — evidence used
   to print only in verbose mode].
9a. The patrol's per-turn output — model reasoning previews, tool invocations,
    the guard's expanded command line, tool-result previews, agent messages —
    prints to **stderr** as it happens, **on by default**; `-q/--quiet`
    suppresses it (findings and notifications always print). In a container,
    pod logs are the human surface and stderr is line-buffered, so the stream
    lands in `oc logs` line-by-line with no buffering workaround — a healthy
    patrol is never silent between its start banner and its findings block
    [DONE: #27].
10. Each notification prints with a `🔔 NOTIFY:` prefix; if `PATROL_NOTIFY_URLS` is
    set, notifications are also delivered to every configured sink via
    [apprise](https://github.com/caronc/apprise) (rules 10a–10c) [PLANNED: #9].
10a. **Sink configuration** [PLANNED: #9]. `PATROL_NOTIFY_URLS` holds one or
    more apprise sink URLs (`slack://…`, `discord://…`, `tgram://…`,
    `ntfys://…`, … — any scheme apprise supports), separated by commas and/or
    newlines. Parsing is a pure function,
    `parse_notify_urls(value) -> list[str]`: split on `,` and `\n`, strip
    whitespace, drop empty entries. Unset or empty → zero sinks (console
    only — today's no-`SLACK_WEBHOOK_URL` behavior). The variable replaces
    `SLACK_WEBHOOK_URL`, which is **removed**: an operator migrating a Slack
    incoming webhook expresses it as its apprise `slack://` URL (apprise's
    documented webhook form). The parsed list is read once at import (the
    same env-derived-config convention as the other `PATROL_*` vars).
10b. **Severity → notify type** [PLANNED: #9]. Delivery maps the report's
    severities onto apprise's `notify_type` at the patrol level, via the pure
    helper `report_notify_type(findings) -> "FAILURE" | "WARNING"`:
    `FAILURE` when the delivered report contains at least one `crit` finding,
    `WARNING` otherwise. `INFO` is never emitted — rule 4 restricts
    notifications to warn/crit, and notifications are plain strings with no
    per-message severity (pairing them back to findings would be fragile), so
    the patrol's worst severity is the honest mapping. Per-notification
    severity and per-severity sink routing (apprise tags) are deferred to the
    #35 follow-up family.
10c. **Delivery semantics** [PLANNED: #9]. Per-sink best-effort, never fatal
    (the rule-10 status quo generalized). `notify(messages, *,
    notify_type="WARNING")` builds one apprise instance per invocation that
    has at least one configured URL (an empty message list still constructs
    it and sends nothing; zero configured URLs constructs none — console
    only), adds
    each configured URL (a URL apprise refuses to load is logged to stderr
    and skipped — the other sinks still deliver), and calls
    `apprise.notify(title="Cluster patrol", body=<message>,
    notify_type=notify_type)` once per message. A sink delivery failure
    (exception or `False` return) is logged to stderr and never raises: a
    patrol with zero reachable sinks still delivers its findings, writes the
    wiki, and exits zero. The console `🔔 NOTIFY:` line (rule 9a's pod-log
    surface) is unchanged and unconditional. The `:rotating_light:` Slack
    emoji prefix is dropped — apprise renders `notify_type` per sink.
11. Token usage (input/output, summed across the patrol's model turns) prints at
    patrol end.

## Configuration Surface

- `PATROL_NOTIFY_URLS` — if set, apprise sink URLs (comma/newline separated);
    notifications are also delivered to every configured sink [PLANNED: #9]
- ~~`SLACK_WEBHOOK_URL`~~ — removed by #9; superseded by `PATROL_NOTIFY_URLS`
  (a Slack incoming webhook is expressed as its apprise `slack://` URL)

## Constraints

- Notification filtering today is the model's judgment only (the report contract
  says "don't cry wolf"); there is no mechanical dedup — known gap.
- The configured apprise sinks are the patrol's deliberate outbound notification
  allowlist — the egress question (network-level exfiltration controls) is #34's,
  not this contract's [PLANNED: #9].

## Planned Changes

| Ticket | Summary |
|---|---|
| ~~#60~~ | ~~Wiki-repair pass + minimal-write fallback (rule 7d here; the tail contract, bounds, and fallback are `wiki-memory.md` rules 30–32, ADR 0022): the shape pass gains a `wiki_repair=(old_wiki, findings, notifications)` mode — old wiki verbatim plus the observed findings, minimal-merge ask, shared binding/bounds/retry; its findings are never delivered. Impl commit also syncs `AGENTS.md`'s test list~~
| ~~#63~~ | ~~Per-call-shape completion-token bounds + visible empty-result reasons (rules 7c + 8b): exploration `REQUEST_MAX_TOKENS` 16000, structured reporting/repair calls `REPORT_MAX_TOKENS` 64000 through the `make_model` seam; the attempt-failure log line names its cause (`truncated at max_tokens` vs `no tool call`) via a pure classifier over the raw response. Impl commit also syncs `AGENTS.md`'s test list and the stale `max_tokens` prose in `patrol.py`'s code comments (e.g. the working-set comment near the shape-pass budget)~~ |
| ~~#51~~ | ~~Empty PatrolReport (rules 5 + 7b): the report contract requires `updated_wiki` on every report; an empty one is a failed attempt — one named corrective retry, then findings-only delivery with a stderr warning, wiki unchanged. Impl commit also syncs `AGENTS.md`'s test list~~ |
| ~~#49~~ | ~~Per-call-shape request bounds (rule 8a): exploration turns `REQUEST_TIMEOUT` 300 s, structured reporting/repair calls `REPORT_TIMEOUT` 900 s — two `make_model` instances through the same seam; the specced constants in `agent-engine.md` / `system-overview.md` updated to the split, bounds pinned by test. Impl commit also syncs `AGENTS.md`'s test list~~ |
| #9 | apprise multi-sink notifications (rules 10a–10c): `PATROL_NOTIFY_URLS` (comma/newline-separated apprise URLs, pure `parse_notify_urls`) replaces the removed `SLACK_WEBHOOK_URL`; patrol-level `notify_type` via pure `report_notify_type` (crit anywhere → FAILURE, else WARNING); per-sink best-effort delivery, failures logged to stderr, never fatal; apprise joins the pinned dependency set (`container-image.md` rules 6–7), `requests` drops from the top-level set (transitive via apprise); manifest Secret optional key becomes `PATROL_NOTIFY_URLS`. Impl commit also syncs `AGENTS.md`'s test list, the repo `README.md`'s env/config table (`SLACK_WEBHOOK_URL` → `PATROL_NOTIFY_URLS`) and any remaining Slack prose, and the `run_patrol_cli` test docstring's env pin list |
| #35 | Notification dedup: notify on state change, not every patrol — new entry, severity change/escalation, or resolution notify; standing unchanged entries stay wiki-only; optional periodic digest for long-unfixed entries; possible per-severity sink routing (apprise tags) once #9's multi-sink exists |
| ~~#40~~ | ~~Report contract states the working-set expectations: the wiki is a working set (git history is the archive), summarize and prune rather than accumulate, the soft character budget, the anomaly-log keep budget (`wiki-memory.md` rules 28–29)~~ |
| ~~#7~~ | ~~Report contract states the staleness clock's model-facing half: `verified_patrol` is process-stamped (never computed by the model), `last_verified` advances only on actual re-verification, and unverified watching entries auto-expire (`wiki-memory.md` rules 20–22)~~ |
| ~~#6~~ | ~~Report contract asks for recheck commands: every Watch-list entry carries a guard-acceptable `recheck:` pair in tool-argument form (`wiki-memory.md` rule 12a)~~ |
| ~~#27~~ | Per-turn output stream on by default (`-q/--quiet` opts out); evidence prints with findings (rule 9a) |
| ~~#5~~ | Wiki-skeleton validation on `updated_wiki` with one shape-pass repair retry; final-report delivery ordering (rule 7a; contract in `wiki-memory.md` rules 15–19) |
