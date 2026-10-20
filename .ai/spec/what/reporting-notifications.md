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
   [DONE: #6]. [PLANNED: #7 — the contract also carries the staleness
   clock's model-facing half: entry metadata lines may carry a
   `verified_patrol` pair that the patroller re-stamps after validation
   (copy or drop it freely, never compute it); `last_verified` advances only
   when the entry was actually re-verified this patrol (it is the signal the
   patroller's clock reads); and a watching Watch-list entry whose
   `last_verified` stops advancing is auto-expired by the patroller after
   `PATROL_WIKI_STALE_AFTER` accepted writes — re-verify what is worth
   keeping, resolve or expire the rest deliberately
   (`wiki-memory.md` rules 20–22).]

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
8. A budget-cut report must cover only what was actually observed; the pass is told
   which commands never executed.

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
10. Each notification prints with a `🔔 NOTIFY:` prefix; if `SLACK_WEBHOOK_URL` is
    set, it is also posted to Slack (with a `:rotating_light:` prefix, 15 s timeout);
    a Slack failure is logged to stderr and never fatal.
11. Token usage (input/output, summed across the patrol's model turns) prints at
    patrol end.

## Configuration Surface

- `SLACK_WEBHOOK_URL` — if set, notifications also go to Slack

## Constraints

- Notification filtering today is the model's judgment only (the report contract
  says "don't cry wolf"); there is no mechanical dedup — known gap.

## Planned Changes

| Ticket | Summary |
|---|---|
| #9 | Notification dedup: notify on state change, not every patrol — new entry, severity change/escalation, or resolution notify; standing unchanged entries stay wiki-only; optional periodic digest for long-unfixed entries |
| #7 | Report contract states the staleness clock's model-facing half: `verified_patrol` is process-stamped (never computed by the model), `last_verified` advances only on actual re-verification, and unverified watching entries auto-expire (`wiki-memory.md` rules 20–22) |
| ~~#6~~ | ~~Report contract asks for recheck commands: every Watch-list entry carries a guard-acceptable `recheck:` pair in tool-argument form (`wiki-memory.md` rule 12a)~~ |
| ~~#27~~ | Per-turn output stream on by default (`-q/--quiet` opts out); evidence prints with findings (rule 9a) |
| ~~#5~~ | Wiki-skeleton validation on `updated_wiki` with one shape-pass repair retry; final-report delivery ordering (rule 7a; contract in `wiki-memory.md` rules 15–19) |
