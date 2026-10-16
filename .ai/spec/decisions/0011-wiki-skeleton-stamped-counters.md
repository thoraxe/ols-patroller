# 0011. Wiki skeleton: validated structure, stamped counters, one repair pass

Date: 2026-10-16 · Status: proposed (issue #5)

## Context

The wiki must become machine-checkable — #7 (expiry) and #8 (stats) need to find
entries and count patrols without parsing prose — but #5's load-bearing constraint
says over-structuring kills the natural-language hypotheses: the wiki stays a
document. Meanwhile the write path is wholly model-authored: nothing today stops a
malformed rewrite from replacing memory, and models are unreliable at mechanical
details (counters, dates, fixed vocabularies) — exactly the details downstream
consumers need to trust.

## Decision

The wiki gains a validated skeleton with a strict prose boundary: YAML frontmatter
(four closed keys), the five canonical sections exactly once each, and an entry
metadata line (`id · status · first_seen · last_verified · source · confidence`)
required only in Watch-list and Anomaly log — everything inside entries stays free
prose. Three frontmatter fields (`cluster_id`, `patrol_count`, `last_patrol`) are
process-stamped after validation, never model-trusted. On invalid structure the
write path makes exactly one repair attempt (a shape-pass retry carrying the
validation errors and the rejected wiki); a second failure keeps the old wiki and
warns — validation degrades, never blocks.

## Consequences

- Machine-checkable memory: #6 (`recheck:`) extends the metadata line; #7 expiry
  and #8 stats consume `status` / `last_verified` / `patrol_count` without parsing
  prose.
- `patrol_count` advances on every accepted write, so every accepted write changes
  the file and commits — #3's byte-identical no-commit case cannot fire for a
  skeleton wiki (empty update / failed report still skip the commit).
- Frontmatter parsing adds `pyyaml>=6` to the runtime dependency set (PEP 723
  header, `pyproject.toml` mirror, exact pin in `requirements.txt` — the
  in-cluster image gains it).
- Pre-#5 wikis need no migration tooling: the first skeleton write stamps
  `patrol_count: 1`; the legacy prose is re-authored in-skeleton by the model.
- Worst case adds one model call per patrol (the repair pass); the common case
  (valid first write) adds none.
