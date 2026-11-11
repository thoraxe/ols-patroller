# 0037. Spec prose uses plain English and ASD-STE100

Date: 2026-11-10 · Status: proposed

## Context

A human approves each spec before implementation. The reviewer approves behavior
from the `what/` files. Dense prose blocks that review. Long sentences, nested
parentheses, and different words for one thing make a rule hard to check. A
reviewer who cannot read a rule quickly cannot approve it with confidence.

Two recent spec PRs show the problem: #101 and #106 are correct, but their rules
need several readings to check.

The sibling project `thoraxe/genpc-ai` already made this decision in its ADR 0004
and wrote a set of writing rules. We adopt the same rules here.

## Decision

Every `what/` file, `glossary.md`, `constraints.md`, the ADRs, and the Summary of
every spec PR uses extremely simple, plain English. Follow ASD-STE100
(Simplified Technical English) where possible. The rules are in `.ai/spec/README.md`
under "Writing rules for what/".

`how/` files use the same short sentences, but they use the exact names from the
code. Their rules are in `.ai/spec/README.md` under "Writing rules for how/".

The rules apply to new and changed text only:

- A new rule or an edited rule must obey the writing rules.
- When a spec PR touches a rule, it also simplifies that rule and its immediate
  neighbors in the same section.
- We do not migrate whole files for this change alone.

The spec-first reviewer gets a readability pass. The spec-first skill tells the
author to use the writing rules. Thus a rule that is hard to read is a review
finding, not only an author preference.

## Consequences

- Easier: a reviewer reads each rule once and checks it. The glossary becomes the
  one place for names.
- Harder: specs are longer because each sentence has one idea. Authors must check
  the glossary and the sentence limits.
- The official ASD-STE100 dictionary is not in the repo. Writers use the rules and
  choose the simplest common word when they are not sure.
- No runtime behavior changes. There is no test for prose, so the reviewer's
  readability pass is the enforceable check.
