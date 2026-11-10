# 0035. Release-pinned manifests; local override for latest

Date: 2026-11-10 · Status: proposed

## Context

ADR 0008 made merges to `main` publish `ghcr.io/thoraxe/ols-patroller:main`
and had the #2 manifests deploy that rolling tag by default; ADR 0010 made
published `v*` releases registry-side retags of the matching `sha-<SHA>` image,
calling release tags "the natural pin for deployments that want a known
release". The manifests never acted on that: the committed default stayed
`:main`, so `oc apply -k manifests/` always deployed the latest merge, and
cutting a release changed nothing about what a fresh apply installs.

The v0.3.2 release exposed the inversion on the local test cluster
(`ols-demo-2`): the Deployment ran `:main` from the committed manifest, the
release had to be pinned by hand (`oc set image …:v0.3.2`), and re-applying the
base manifest would silently revert the pin to `:main`. "Test the latest merge"
should be the deliberate act; "deploy a release" should be the default.

Design space:

1. **Keep `:main` as the committed default** (status quo): simplest flow, but
   every apply is an unversioned upgrade and releases are invisible to the
   deployment surface.
2. **Release tag as the committed default, with a documented local override**
   for `:main` / a `sha-<SHA>` / another registry: releases deploy by default;
   testing latest stays one local edit away.
3. **Digest pin as the committed default**: maximally reproducible, but a
   digest cannot be written before the image exists for the release commit, and
   it makes the operator-facing manifest unreadable; `sha-<SHA>`/digest stays
   the *operator's* hardening option (rule 8a), not the repo default.

## Decision

Option 2. `manifests/kustomization.yaml`'s `images:` entry names the **current
release tag** (`newTag: vX.Y.Z`), the newest published release
(`in-cluster-deployment.md` rule 8). Overriding the image — to `:main` for a
"latest" test, to a `sha-<SHA>`, or to another registry — is a supported local
operation performed in the operator's working tree, via a kustomize overlay, or
with `oc set image`; it never changes the committed default (rule 8b).

Because the tagged tree is the deployable artifact, the release flow pins
**before** tagging (`container-image.md` rule 19): merge the pin commit, wait
for the main-push build to publish `sha-<SHA>` for that commit, then publish the
release whose tag points at it; the existing rule-17 retag applies `vX.Y.Z` to
the image. A published release tag is never moved, retargeted, or force-updated —
a wrong pin is fixed forward by the next release, and a pre-rule release (such
as v0.3.2) is corrected by a follow-up pin commit on `main`, never by moving its
tag. `image.yml` stays build-and-retag only; it never writes `manifests/`,
because the pin is reviewed content, not release automation.

`imagePullPolicy: Always` is unchanged: the release tag's registry manifest is
written by CI (and a local override can be `:main`), so the tag is not a
`sha-<SHA>`/digest pin under rule 8a's invariant.

## Consequences

- A plain `oc apply -k manifests/` upgrades a cluster from release to release,
  and a deployment's image tag identifies the release it runs.
- Merges between releases no longer reach a cluster by default. Testing a merge
  now requires the explicit override (`:main`, or the merge's `sha-<SHA>`), and
  the README documents it; that is the intended friction.
- Cutting a release gains one ordering step: the pin commit must merge (and its
  main-push build must succeed) before the release is published. A release
  published from a commit whose manifests name another tag is a contract
  violation, caught by review and by the static manifest test, which pins the exact
  current release tag (a pin commit updates the manifest and that expectation
  together).
- `:main` remains the rolling tag for local override/testing and for forks that
  want latest; no `latest` tag is published (ADR 0010 unchanged).
- v0.3.2 predates this record: its tag is not moved. The implementation commit
  pins `v0.3.2` on `main` (so the default apply installs the current release)
  and this rule applies to v0.3.3 onward.
