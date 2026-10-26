---
name: spec-implement
description: Implement an approved spec PR — a context-clear agent makes the failing tests pass exactly as the approved spec specifies, gets fresh independent AI review, adds the single implementation commit, and merges once the review is clean and the PR is labeled. Phase 2 of the spec-first PR workflow.
disable-model-invocation: true
---

# Spec Implement — land the implementation

Implement an already-approved spec: make the failing tests pass, exactly as the spec
specifies. Run this in a **fresh session** — no context from the spec authoring —
that context-clear property is the point of the workflow.

**Announce at start:** "I'm using the spec-implement skill to implement <target>."

## Input

The argument names the PR to implement: a PR number, a branch (`spec/…`), or an
issue number. Resolve with `gh pr view` / `gh pr diff` as needed.

## Preconditions — all must hold, else stop and report

1. The PR exists with exactly one commit (specs + failing tests). If it has none or
   more than one, something is off — stop and report.
2. The spec has human approval: the PR carries the `spec-approved` label — a
   human adds it after reviewing the spec (`gh pr view <N> --json labels`). If
   not: "Human gate not passed — approve the spec first (add `spec-approved` to
   the PR)." Do not proceed on the user's say-so alone if the label is missing;
   ask them to record the approval on the PR.
3. The failing tests currently fail. Run them and confirm — if they already pass,
   the work may be done or the tests are wrong; report instead of implementing.

## Non-negotiables

- The spec is the contract. What/ files win over how/ files on conflict; both win
  over your preferences. Do not redesign.
- Do not change contract rules. The only spec edits allowed in this commit are
  marker flips (`[PLANNED: #N]` → `[DONE: #N]`) and Planned Changes table
  strikethroughs.
- Exactly one additional commit on the PR branch. Never amend, rebase, or otherwise
  touch commit 1. A **sync merge** of `main` may sit between commit 1 and the impl
  commit when the branch is CONFLICTING because it was cut before its siblings
  merged (see step 2b) — it does not count against the one-impl-commit rule, and it
  is the only permitted merge into the branch.
- Never add the `spec-approved` label — that gate stays human-only. Merging is
  agent-owned under current policy, but only in step 5 and only when the review
  loop returned APPROVED with zero BLOCKING findings and the PR carries the
  `implementation-complete` label. If that policy is revoked, merging reverts to
  a human action.
- If the spec is wrong or impossible to implement as written: STOP. Report the
  conflict in the PR conversation and to the user. Spec changes need their own
  `/skill:spec-first` review loop — do not fix the spec inline.

## Steps

### 1. Load the contract

Read the PR's spec diff, then the complete text of every spec file it touches, plus
`.ai/spec/constraints.md`, `.ai/spec/glossary.md`, the failing tests, and the code
you will change. You get no authoring context — if something is genuinely ambiguous,
ask the user rather than guessing.

### 2. Implement

- Make the failing tests pass. The tests do not exhaust the contract — satisfy
  every rule the spec adds, not just the assertions.
- Update how/ files to match reality (module map entries, key symbols, data flow)
  and `ARCHITECTURE.md` if structure changed.
- Stay within `constraints.md` (read-only cluster access, no OLS code imports,
  wiki stays a document, …).

### 2b. Sync with `main` when the branch conflicts

If the PR is CONFLICTING (siblings merged after this branch was cut): `git merge
main`, resolve only append-only conflicts to the **union** of both sides (index
rows, list rows, docstring bullets, and separately-appended test blocks — the
sibling's additions plus this PR's, never dropping either), commit that merge on
its own, then re-run the suite. The gate: after the sync merge, exactly the PR's own
new tests must fail — anything else means a resolution lost content. Never resolve a
conflict by rewriting commit 1 or by taking one side wholesale for a file both sides
touched. If the conflict is *not* append-only (a sibling changed the same contract
text), stop and surface it.

### 3. Verify locally

- The previously failing tests pass; the full test suite passes.
- Walk each new spec rule one by one and confirm the implementation satisfies it.
  Record the walk (rule → evidence) for the reviewer prompt.

### 4. Fresh-AI review loop

Add the `ai-implementation-review` label to the PR before dispatching
(`gh pr edit <N> --add-label ai-implementation-review`) — the PR is now visibly
under AI review.

Dispatch an independent reviewer — a separate `pi` process with no session context
(procedure below; template: `reviewer-prompt.md` in this skill's directory). Inputs,
inline and labeled by path: the approved spec files in full, the complete PR diff
(commit 1 + commit 2), the passing test output, the rule-by-rule walk, constraints,
glossary. When step 2b added a sync merge, say so in the prompt and give both the
net diff against `main` (what merging adds) and the impl-only diff (for the
spec-integrity pass) — otherwise the reviewer cannot tell the merge from a
contract change.

- Address every finding in the code (or, if the finding reveals a spec defect, stop
  per the non-negotiables). Repeat until the reviewer returns APPROVED with zero
  BLOCKING findings.
- Save each round's report to `.ai/spec/verification/impl-<slug>-report.md`
  (gitignored; the PR body carries the outcome).

### 5. Commit, label, merge, and report

- One commit on the PR branch: `impl: <title> (#N)` — implementation + how/ sync +
  `[DONE: #N]` marker flips + Planned Changes strikethroughs.
- Push to the PR branch.
- Flip the state: remove `ai-implementation-review`, add
  `implementation-complete` (`gh pr edit <N> --remove-label
  ai-implementation-review --add-label implementation-complete`). This label is
  the merge precondition — never merge without it on the PR.
- Merge: `gh pr merge <N> --merge` — a merge commit, matching repo history; never
  `--squash` or `--rebase` (both collapse the two-commit spec→impl structure),
  never `--auto`. Merge only when the review loop ended APPROVED with zero
  BLOCKING findings and the label flip succeeded. If the merge fails (branch
  protection, conflicts, anything else), stop and report — never force it.
- Report to the user: what was implemented, test results, review outcome, and the
  merge result (merged, or not merged and why). Flag anything a human may still
  want to look at post-merge.

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

- Change contract rules, bend the spec, or "improve" the design.
- Rebase, amend, or otherwise rewrite history; and no merging except the labeled,
  review-clean merge of step 5 (`--merge` only, never squash/rebase/auto).
- Skip the review loop because the tests pass — tests passing is necessary, not
  sufficient.
