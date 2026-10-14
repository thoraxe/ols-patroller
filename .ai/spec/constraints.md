# Constraints

Cross-cutting project rules that span multiple components and don't belong to any
single what/ file. Component-specific constraints are co-located in the relevant
what/ file.

1. **Standalone isolation.** No OLS code is imported, ever. Any integration with
   OLS-side machinery is API-surface-only (e.g. `oc create agenticrun`). The
   patroller must remain standalone and open-source-able.
2. **Read-only forever.** The patrol never mutates the observed cluster; remediation
   is delegated to governed AgenticRuns (#12), never grown locally. Weakening any
   safety layer requires an ADR (see `what/safety-model.md`).
3. **The wiki stays a document.** Storage is plain markdown files (git-backed per
   #3) — not a database, vector store, or memory service. Structure (skeleton,
   metadata) must preserve free prose inside entries; over-structuring is rejected
   as killing the natural-language hypotheses (load-bearing constraint of #5).
4. **Investigation stays pluggable.** Don't grow bespoke RCA/diagnosis — the
   prior-art survey (`docs/prior-art.md`) positions this project on continuity +
   proactivity + memory, with HolmesGPT/AgenticRun as candidate engines (#15).
5. **Issues inform; specs are the contract.** Work is tracked in GitHub issues +
   milestones (planning docs were retired in db5344b), but a feature's behavioral
   contract is fully specified in spec files before implementation
   (`/skill:spec-first`) so a context-clear agent can implement from the spec alone
   (`/skill:spec-implement`). Write the contract — don't paste issue bodies, and
   don't leave the plan only in the issue.
6. **Hacky everywhere except safety.** The single-file PoC roughness is accepted and
   intentional; the read-only safety model is the part held to production standards
   (documented, unit-checked, e2e-verified, tests landing in-repo via #4).
