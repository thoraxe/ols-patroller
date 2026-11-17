# 0040. A pre-report patrol failure notifies without a write

Date: 2026-11-17 · Status: proposed

## Context

ADR 0022 and ADR 0024 built the minimal-write net for two failure families.
One family is a still-empty wiki. The other is a reporting pass that returns
`None`. Both families stop the wiki from advancing after exploration. In both,
the patrol observed the cluster, and the write keeps the staleness clock honest,
so unverifiable memory expires instead of freezing. (A report whose wiki fails
skeleton validation and its repair keeps the old-wiki outcome. ADR 0022
deferred extending the net to that leg.)

A third family stays outside the net, and it is the blindest one. A patrol
that raises an exception on the way to a report never reaches the total-failure
leg. A redeploy with an expired `PATROL_API_KEY` produced `Error code: 401` on
the first exploration turn. The rule-33 leg fires on `shape_report` returning
`None`, never on a raise. So the exception escaped `run_patrol` to the loop's
log-only `except`: `[patrol] patrol failed (Error code: 401); continuing loop`
(issue #119). No console notification, no apprise delivery, no wiki change.
The patroller was blind for the next six hours. The only trace was a stderr
line in pod logs. That contradicts rule 33's own rationale: a blind patrol is
always wolf-worthy. A `None` report was never the only way to go blind.

Design space offered three options. (a) Keep the loop's log line. (b) Deliver
one FAILURE notification, re-raise, and leave the wiki alone. (c) Deliver the
notification and extend the rule-31 minimal write to this leg too. (c) is
wrong on two counts. The wiki-repair pass exists to merge *observed findings*
into a wiki the patrol accepted. This leg delivers no report, so it accepts
nothing to merge. Also, an accepted
write advances `patrol_count` and `last_patrol`. Rules 20–22 would then expire
Watch-list entries off a clock that verified nothing, so a credential failure
would silently retire memory. Honest staleness is ADR 0022's answer to a
patrol that *looked*. A patrol with no delivered report must not move the clock.

## Decision

(b): deliver one FAILURE notification, then re-raise the same exception. A
patrol that raises before it delivers a report delivers exactly one FAILURE
notification through the standard `notify` path (`notify_type="FAILURE"`).
The message is `patrol failed before the report — <error> (see pod logs)`.
The patrol leaves the wiki write path and the minimal-write net untouched: no
write, no commit, no clock advance. The patrol never swallows the exception, so
`--once` still exits nonzero. Loop mode keeps its catch-and-continue line and
continues to the next patrol.

## Consequences

A blind patrol now says so on both surfaces the patrol owns. One is the console
`🔔 NOTIFY` line in pod logs and the configured apprise sinks. The other is the
loop's unchanged error line. The scope is the pre-report phase. Inside an
attempt the reporting pass classifies a failure. It returns the last parsed
result or `None`, so the total-failure leg keeps its own one FAILURE
notification and gains no duplicate. A raise from the pass's own setup is
pre-report too, and this rule owns it. An exception raised after the report
lands (the wiki write, the commit, the push) keeps today's log-only behavior.
The human already received the report's findings, and extending the leg there
would give one patrol a second FAILURE notification.
Deferred: loop-mode backoff or exit after repeated patrol failures (the #10
cost-control family). Also deferred: an operator-facing "patrols since last
model-written wiki" counter, still derivable from git history (ADR 0022).
Key-material rotation itself stays an operator concern; this ADR only makes the
failure visible.
