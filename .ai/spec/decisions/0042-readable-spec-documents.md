# 0042. Readable spec documents: a 500-character line cap and short table rows

Date: 2026-11-24 · Status: proposed

## Context

The spec and `how/` docs grew into giant single-line table rows. A reviewer
cannot read or review them in a GitHub diff. The `tests/` row in
`how/project-structure.md` reached 13,899 characters (#123 split it into a row
plus a Test map section). The largest remaining rows stay above 1,500
characters. Several long rows also wrapped inside a table cell, so the rendered
table lost columns and cells ran together. Review readability now costs more
than feature work.

## Decision

`.ai/spec/README.md`'s Writing rules gain rules 11–15. They govern document
shape, not sentences:

- A table row is at most 500 characters. Longer detail moves to a bulleted list
  below the table (rule 11).
- A table cell stays on one line. A literal `|` is escaped. Every row of a table
  block carries the same number of cells. A pipe inside a code span still
  separates cells (rule 12).
- Every non-fenced line in `.ai/spec/` and the top-level docs stays at most 500
  characters. Fenced code blocks and the gitignored `verification/` reports are
  exempt (rule 13).
- Behavior stays in `what/`. Code names, signatures, and module internals stay in
  `how/`. History and rationale stay in `decisions/` (rule 14).
- A readability edit never changes a rule's meaning. A readability PR states what
  it moved and what it rewrote (rule 15).

`tests/test_spec_docs.py` pins rules 11–13. Rules 14–15 are review-checked. This
record and #124 apply ADR 0037's "new and changed text" rule to rules 14–15. The
line cap and the cell count are a corpus-wide ratchet.

This record amends ADR 0037. The sentence rules stay on new and changed text.
Rules 11–13 are corpus-wide, and the #124 cleanup is the one migration pass.

## Consequences

- Easier: a reviewer reads a table row in a diff. A broken table stays visible.
- Harder: a long history row is now a short row plus wrapped detail below the
  table. The file grows in line count.
- The test fails on a new oversized row or a missing cell. The cap is a ratchet:
  it holds for every `.ai/spec/` file, not only the touched files.
- No runtime behavior changes. The rules are process rules, not behavioral
  contracts. `what/` remains authoritative for behavior.
- A wording change that alters behavior still needs its own spec-first PR (#124
  forbids it in a readability pass).
- The cleanup touches more files than the issue's suggested list. The cap is
  corpus-wide by design, so every offender comes under it in one pass.
- Moving existing code-map detail out of `what/` is deferred. Rule 14 applies to
  new and changed text, and existing `what/` signatures are contract text.
