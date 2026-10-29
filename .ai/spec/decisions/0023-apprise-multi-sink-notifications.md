# 0023. apprise for multi-sink notifications

Date: 2026-10-30 · Status: proposed

## Context

Notifications today are the console line plus one hand-rolled
`requests.post` to a Slack incoming webhook (`SLACK_WEBHOOK_URL`) — a
single hard-coded channel, per-channel HTTP quirks written by us, delivery
untested. Every future channel (Discord, Telegram, ntfy, …) would mean more
hand-rolled POST code, against the "hacky everywhere except safety" grain:
delivery is not the safety-critical part, so a library is the right
roughness trade. Design space: (a) keep rolling per-channel POSTs; (b) a
notification abstraction library — apprise (100+ services via URL schemas,
severity-aware `notify_type`, mature) vs notifiers (effectively
unmaintained); (c) grow a bespoke notifier module. Separately, the report's
notifications are plain strings (rule 4 restricts them to warn/crit) with no
per-message severity, so apprise's INFO/WARNING/FAILURE vocabulary cannot be
applied per notification without a schema change.

## Decision

Use apprise. `PATROL_NOTIFY_URLS` (comma/newline-separated apprise URLs,
parsed by a pure helper) replaces `SLACK_WEBHOOK_URL`, which is removed — a
Slack incoming webhook is expressed as its apprise `slack://` URL. Delivery
maps severity at the patrol level (`report_notify_type`: any `crit` finding
→ FAILURE, else WARNING; INFO never occurs because notifications are
warn/crit only). Per-sink delivery is best-effort, logged to stderr, never
fatal. `requests` drops from the pinned top-level dependency set (apprise
brings it transitively).

## Consequences

One pip dependency buys every channel; config becomes URL strings that
embed credentials and slot into the existing gitignored `secret.yaml`
mechanism (`PATROL_NOTIFY_URLS` optional key). The breaking env-var removal
is accepted — the operator surface is small and documented in the spec.
Per-notification severity and per-severity sink routing (apprise tags) are
deferred to the #35 follow-up family; dedup on state change is #35 proper.
The configured sinks become the patrol's deliberate outbound notification
allowlist — network-level egress control stays #34's problem.
