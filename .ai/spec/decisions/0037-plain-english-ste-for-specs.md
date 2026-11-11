# 0037. Spec prose uses plain English and ASD-STE100

Date: 2026-11-10 · Status: proposed

## Context

A human approves each spec before implementation. Dense prose makes a rule hard to
check. A reviewer who cannot read a rule quickly cannot approve it with
confidence. The sibling project `thoraxe/genpc-ai` made this decision in its ADR
0004.

## Decision

Spec prose follows the Writing rules in `.ai/spec/README.md`. Those rules use
plain English and ASD-STE100 (Simplified Technical English) where possible, for
both `what/` and `how/` text.

## Consequences

- Easier: a reviewer reads each rule once and checks it.
- Harder: specs are longer because each sentence has one idea.
- The rules apply to new and changed text only. We do not migrate whole files.
- No runtime behavior changes. The rules are process rules, not behavioral
  contracts.
- The wiki's own prose is a separate change (#111). This record does not govern
  it.
- The spec-first drafting step and the reviewer pass are a follow-up change
  (#112). This record sets the rules only.
