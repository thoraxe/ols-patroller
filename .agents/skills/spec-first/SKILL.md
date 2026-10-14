---
name: spec-first
description: Draft spec changes plus failing tests for a GitHub issue or feature request, review them with a fresh independent AI reviewer until clean, and open a spec-only PR for human approval. Phase 1 of the spec-first PR workflow.
disable-model-invocation: true
---

# Spec First — draft the spec PR

Turn a feature request into an approved-ready spec PR: contract rules in `.ai/spec/`
plus failing tests that encode them. Implementation happens only after a human
approves this spec, by a context-clear agent running the `spec-implement` skill.

**Announce at start:** "I'm using the spec-first skill to draft the spec PR for
<target>."

## Input

The argument names the work:

- A GitHub issue reference (`#4`, `4`, `gh issue 4`, or a URL) → fetch it with
  `gh issue view <N> --json title,body,labels,milestone` and read the comments if
  they contain decisions.
- Free text → treat it as the feature description.
- Nothing → ask the user what feature is being specced before doing anything.

## Non-negotiables

- The PR contains **spec changes + failing tests only**. No implementation code
  beyond test scaffolding (conftest, dependency wiring, test fixtures).
- Never merge, never push to `main`. Human approval is the only merge path.
- Never add the `spec-approved` label yourself — that transition belongs to a
  human; the label is the gate `spec-implement` keys on.
- Write for a context-clear implementer: the implementing agent will read the
  approved spec, the failing tests, and the repo — nothing from this session. The
  spec must be complete and unambiguous on its own.

## Steps

### 1. Gather context

- Read `.ai/spec/README.md` (conventions), `.ai/spec/constraints.md`,
  `.ai/spec/glossary.md`.
- Read every spec file the change could touch, and the code those files describe.
- The issue informs the spec. The spec restates the contract as testable rules —
  it does not paste the issue body.

### 2. Draft the spec changes

- Expand `[PLANNED: #N]` markers into full behavioral rules in existing files, or
  create new files per the README's new-file-vs-extend convention.
- Rules are numbered, testable statements. Rule numbers are stable — never
  renumber existing rules; insert with sub-numbers (e.g. `16a`) if needed.
- Update the file's Planned Changes table; update README Quick Start /
  Cross-Reference tables if files are added.
- Document new configuration surface (env vars, CLI flags, file paths) in the
  relevant what/ file.
- If the design makes an architectural decision, record an ADR in
  `.ai/spec/decisions/` (format in its README).
- A spec that violates `constraints.md` is wrong, not clever.

### 3. Write the failing tests

- Encode the new rules as tests. They must fail now and pass after implementation.
- Run them and capture the failure output. Tests must fail for the right reason —
  an assertion about missing behavior, not an incidental error.
- If the repo has no test harness yet, scaffolding it is part of this PR: settle
  the dependency wiring, document how tests run in the relevant how/ file, and say
  so in the PR body.

### 4. Open the PR as a draft

- Branch: `spec/<N>-<slug>` (or `spec/<slug>` with no issue).
- Exactly one commit: `spec: <title> (#N)` — spec changes + failing tests. Keep it
  one commit while iterating: amend, never stack commits.
- Open a **draft** PR (`gh pr create --draft`). The body is a placeholder for now
  ("spec drafted; AI review loop in progress"); the final body lands in step 6.
  Put `Closes #N` in the body (not the commit) so merging closes the issue.
- Add the `ai-spec-review` label (`gh pr edit <N> --add-label ai-spec-review`) —
  the PR is now visibly under AI review.

### 5. Fresh-AI review loop

Dispatch an independent reviewer — a separate `pi` process with no session context
(procedure below). The reviewer gets, inline and labeled by path: the issue or
feature description, the full diff, the complete text of every spec file touched,
the failing tests with their captured output, and the conventions / constraints /
glossary. Build the prompt from `reviewer-prompt.md` in this skill's directory.

- Address every finding in the spec or tests, then re-dispatch. Repeat until the
  reviewer returns APPROVED with no BLOCKING findings.
- Do not argue with the reviewer inside the report — fix the spec, or record the
  justification in the spec/ADR where the next reader will find it.
- Save each round's report to `.ai/spec/verification/spec-<slug>-report.md`
  (directory is gitignored; the PR body carries the outcome).

### 6. Mark ready for human review

- Replace the placeholder PR body: what contract is added/changed (bullets), how
  tests were scaffolded if applicable, the review-loop outcome (rounds run +
  verdict), and the gate notice:

  > Human gate: review and approve this spec — add the `spec-approved` label.
  > Implementation (commit 2) begins only once that label is on the PR, via the
  > `spec-implement` skill in a fresh session.

- Flip the state: remove `ai-spec-review`, add `spec-review-required`
  (`gh pr edit <N> --remove-label ai-spec-review --add-label
  spec-review-required`), and mark the PR ready for review (`gh pr ready <N>`).
- Report the PR URL to the user and stop. `spec-approved` is the human's to add —
  `spec-implement` keys on it.

## Reviewer dispatch (Pi)

1. Build the reviewer prompt from `reviewer-prompt.md` with all inputs inline —
   full text, no summaries, no bare file paths in place of content.
2. Write it to a private temp file outside the repository (e.g. via `mktemp`,
   user-only permissions).
3. From the project root run:
   `pi --print --no-session --no-context-files --no-skills --tools read,grep,find,ls "@$prompt_file"`
4. Check the exit status; capture the complete output — that output IS the review
   report, never replace or paraphrase it. Remove the temp files afterward,
   including on failure.
5. If no independent reviewer can run in this environment, say so and stop. Do not
   substitute a self-review.

## What this skill does NOT do

- Implement anything — that is `spec-implement`, after human approval.
- Merge or push to `main`.
- Rewrite approved rules beyond what the new feature requires.
