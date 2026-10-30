# 0026. Notification dedup is state-change keyed against the wiki

Date: 2026-10-30 · Status: proposed

## Context

Notifications were model-judged only: the report contract said "don't cry
wolf", but nothing compared one patrol's alerts with the last. Real-run
evidence (#35, patrol 7): all six standing catches re-notified every run —
the 19-day `wrker` typo had been notified five patrols in a row. Alert
fatigue is a stated risk; "only notify about things a human should actually
care about" must include *time*. The wiki already persists the standing
entries (`id`, `status`, `first_seen`, `last_verified`, `confidence`), so the
state to diff against exists — but notifications are plain human-readable
strings with no link to an entry, and entries carry no severity, so
"severity changed" is not observable.

Design space: (a) keep model-side dedup — tell the model "don't repeat
yourself" (today's contract; has visibly failed); (b) change the report
schema to structured notifications/findings carrying an entry id (a bigger
schema change, and per-message severity remains fragile to pair back); (c)
state-key the plain-string contract: require a leading `[<entry id>]` tag on
each notification, add an optional per-entry `severity` pair, and let the
patrol mechanically diff the old and new wiki. A periodic reminder is needed
either way for conditions that outlive the filter (`wrker` should nag, but
weekly, not every 15 min).

## Decision

Dedup is **state-change keyed against the wiki**, implemented process-side.
Notifications keep their plain-string shape but must open with a bracketed
entry id (`[W-1] …`); the pure `filter_state_change_notifications` diffs the
tag against `wiki_entry_states(old_wiki)` and `wiki_entry_states(new_wiki)`:
new entry, severity change, status change, or removal delivers; a standing
entry with equal status and severity is suppressed (wiki-only, count on
stderr). Untagged or unattributable messages deliver fail-open — dedup may
reduce noise, never lose news. Entries gain an optional
`severity: info|warn|crit` pair (judged when present) so severity is
diffable. A process-authored weekly digest
(`PATROL_NOTIFY_DIGEST_AFTER`, default 7 days, `0` disables) names standing
watching entries older than the threshold and not notified within it; the
write path stamps a process-owned `last_notified` date pair for every entry
a delivered notification named. Per-notification severity and apprise-tag
sink routing stay deferred.

## Consequences

- The wiki is the dedup baseline, so the accepted-write semantics carry:
  a patrol whose wiki write is rejected or empty has no new baseline and can
  repeat its state-change notifications next run. That is honest — the state
  did not persist — and is recorded in `reporting-notifications.md`'s
  Constraints rather than papered over.
- Untagged notifications bypass the filter by design; the contract requires
  the tag, and the process never silently drops an untaggable message. A
  model that omits tags degrades to today's behavior, not to lost alerts.
- `severity` is optional structurally (pre-#35 wikis and model omissions are
  legal) but judged when present; a missing value on one side of the diff
  reads as a change, so migration is noisy once per entry rather than silent.
- The digest adds one process-owned metadata pair (`last_notified`) and one
  write-path pass after compaction. It is housekeeping state, not a finding,
  and never changes the patrol-level `notify_type`.
- Splitting this from #9 held: delivery (apprise multi-sink) and message
  selection (this ADR) remain independent contracts. Per-severity routing can
  later read the entry's `severity` pair without a schema change.
