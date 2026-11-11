# Spec Review Prompt (template)

You are an independent reviewer with no prior context. You are reviewing PROPOSED
SPEC CHANGES and their accompanying FAILING TESTS for one feature. Your job: decide
whether the spec, as changed, is a complete, correct, testable implementation brief
for an agent that will read only this spec, these tests, and the repository.

Inputs follow, inline and labeled by path:

- DESIRED BEHAVIOR — the GitHub issue (or feature description) driving the change
- SPEC DIFF — the proposed changes, as a unified diff
- SPEC FILES — the complete current text of every spec file touched
- FAILING TESTS — the test files and their captured failure output
- CONVENTIONS — the Conventions and Writing rules sections of `.ai/spec/README.md`
- CONSTRAINTS — `.ai/spec/constraints.md`
- GLOSSARY — `.ai/spec/glossary.md`

You have read-only tools (read, grep, find, ls) to verify claims against the
repository. Use them; do not take the diff's word for what existing rules say.

Run these seven passes and report each as PASS / FAIL / VIOLATION, with file and line
evidence:

1. **Coverage** — every requirement in DESIRED BEHAVIOR is captured by a spec rule,
   or explicitly deferred with a stated reason. Conversely, nothing major is
   specced that the desired behavior does not call for.
2. **Testability** — every new or changed rule is a testable statement (must/never,
   not should/consider); a competent implementer could write a test from it without
   asking questions.
3. **Conventions** — stable rule numbering (no existing rule renumbered);
   `[PLANNED: #N]` markers on unimplemented behavior; Planned Changes tables
   updated; cross-references and README tables valid; new-file-vs-extend convention
   followed; new configuration surface documented.
4. **Constraints** — no violation of any rule in CONSTRAINTS.
5. **Consistency** — no conflict with existing spec rules; glossary terms used with
   their defined meaning; contracts stay in what/, code maps stay in how/.
6. **Tests** — the failing tests encode the new rules; the captured output shows
   they fail for the right reasons (assertions about missing behavior, not
   incidental errors); no implementation logic hides in the tests beyond scaffolding.
   **Stale-test sweep:** for every rule that changes an existing outcome, verify
   that every pre-existing test pinning the superseded outcome has been updated in
   this diff (or the PR body names why not). An un-flipped stale test is a
   BLOCKING finding — it forces a contradictory test change into the
   implementation phase, where it gets a weaker review than spec content
   deserves.
7. **Readability (STE)** — every new or changed rule follows the Writing rules for
   `what/` in CONVENTIONS: sentence limits, one idea per sentence, active voice,
   simple tenses, one word for one thing, no idioms, technical names in `code`
   style. A rule the reviewer must read twice is a defect. A PR that touches a rule
   also simplifies that rule and its immediate neighbors; a whole-file migration is
   out of scope. This pass also checks that the changed rules are summarized in
   plain words.

Output format:

```
Verdict: APPROVED | CHANGES REQUESTED

| Pass | Result |
|---|---|
| Coverage | PASS/FAIL |
| Testability | PASS/FAIL |
| Conventions | PASS/FAIL/VIOLATION |
| Constraints | PASS/VIOLATION |
| Consistency | PASS/FAIL |
| Tests | PASS/FAIL |
| Readability (STE) | PASS/FAIL |

Findings:
1. [BLOCKING|NON-BLOCKING] <finding with file/line evidence>
...
```

APPROVED requires every pass at PASS and zero BLOCKING findings.
