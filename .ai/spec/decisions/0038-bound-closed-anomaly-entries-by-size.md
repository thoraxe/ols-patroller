# 0038. Bound closed anomaly entries by size: whole-line head+tail truncation, metadata line intact

Date: 2026-11-11 · Status: proposed (issue #86)

## Context

ADR 0017 bounded the Anomaly log by count. The last `WIKI_ANOMALY_KEEP` (20)
resolved/expired entries survive each accepted write. That decision rejected
truncation as structure-destroying. The count bound works. It does not bound
size. A live wiki held 20 closed entries. They held 16,402 characters: 59% of
a 27,773-character document. The compaction pass ran correctly on every
write. Each count slot cost about 820 characters of narrative prose. The soft
budget (`WIKI_CHAR_BUDGET`, 20,000) stayed over. It never gated, so the wiki
could sit permanently over its drift alarm while the mechanics were sound.

Four options were on the table (issue #86): lower the keep count; cap each
closed entry's prose; write a separate archive file before pruning; ask the
model to summarize closed entries in its rewrite.

## Decision

Cap the prose of each closed entry (`wiki-memory.md` rule 28a). On a
model-authored accepted write, a `resolved`/`expired` Anomaly-log entry keeps
at most
`WIKI_ANOMALY_ENTRY_CHARS` (400) characters of continuation prose. The pure
`truncate_closed_entries(content, cap)` pass keeps whole lines from the top
(240 characters) and from the bottom (160). It drops the lines between them.
It writes one marker line naming the dropped characters and git retention.
The metadata line is never touched. `watching` entries, the Watch-list, the
prose sections, and the frontmatter keep their characters. Rule 29 stays
soft.

The unit is characters, not bytes. The measurement is `len(line)`, the same
unit the tests use.

## Consequences

Closed history now costs a bounded number of characters. Twenty entries hold
at most 20 x 400 kept characters, plus their metadata and marker lines. Closed-history continuation prose
can no longer hold the wiki over the soft budget by itself; metadata and
marker lines stay uncapped. That is the lever the #86 evidence asked for.
The count bound plus the size bound make the Anomaly log a working set in
characters as well as in entries.

The rejected alternatives. A lower keep count (option 1) moves the same
characters into fewer slots. The budget problem returns as soon as entries
grow. A separate archive file (option 3) breaks `constraints.md` rule 3: the
wiki stays a document. It also touches the validator, the stamp, expiry,
compaction, the stats and churn parsers, and the report contract's full-wiki
requirement. File-only mode has no repo to archive into.
Model-written summaries (option 4) carry #67's failure mode: the model never
sees the characters it must cut. The git repo already is the archive, so the
marker line points there instead of inventing a second file.

Whole-line keeping, not mid-line cutting, makes truncation safe enough to
reverse ADR 0017's rejection. A mid-line cut can drop an indentation and grow
a stray entry. A half-dropped fence can swallow the rest of the document.
Whole lines plus whole fences keep the skeleton valid, so the truncated wiki
still passes `validate_wiki`. Head+tail, not head-only, because the tail
carries how the entry ended. That includes the `auto-expired` annotation
rule 21 appends as the entry's last line. The guard's output truncation uses
the same rationale.

Follow-ups. The first accepted write after adoption may truncate a burst of
legacy entries at once, with one console line each. Rule 26's churn number
sees that burst as prose change without a status change, once. The report
contract states the cap (`reporting-notifications.md` rule 5). The model then
writes closed entries tight, and the process cut is the net, never the plan.
