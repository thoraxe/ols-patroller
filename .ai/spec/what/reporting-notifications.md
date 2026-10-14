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
5. `updated_wiki` is the FULL new wiki content, structured per the canonical sections
   with `(source, timestamp, confidence)` tags and stale entries expired.

### Reporting Pass

6. The report is produced by a **tool-free structured-output pass** over the finished
   patrol conversation — never by an in-agent forced response format (see
   `patrol-engine.md` Constraints).
7. The pass retries once on an invalid or empty result (a corrective user message is
   appended); total failure means no report, a warning is logged, and the wiki is
   left unchanged.
8. A budget-cut report must cover only what was actually observed; the pass is told
   which commands never executed.

### Delivery

9. Findings print to the console with a severity column; each finding's evidence
   prints with them unless suppressed by `--quiet` [PLANNED: #27 — evidence used
   to print only in verbose mode].
9a. The patrol's per-turn output — model reasoning previews, tool invocations,
    the guard's expanded command line, tool-result previews, agent messages —
    prints to **stderr** as it happens, **on by default**; `-q/--quiet`
    suppresses it (findings and notifications always print). In a container,
    pod logs are the human surface and stderr is line-buffered, so the stream
    lands in `oc logs` line-by-line with no buffering workaround — a healthy
    patrol is never silent between its start banner and its findings block
    [PLANNED: #27].
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
| #27 | Per-turn output stream on by default (`-q/--quiet` opts out); evidence prints with findings (rule 9a) |
