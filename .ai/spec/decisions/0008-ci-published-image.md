# 0008. Post-merge CI publishes the image; the manifests deploy it

Date: 2026-10-14 · Status: proposed

## Context

A dev cluster needs the container image from somewhere. Options: (a) every
operator builds locally and pushes to the cluster's internal registry — works,
but the done-when depends on a laptop toolchain and there is no shared
artifact; (b) CI builds on merges to `main` and publishes to GHCR — the repo
is public, so the package can be public and any cluster pulls it without
secrets. PR-time builds were deliberately excluded: the workflow is a
post-merge artifact pipeline, not a merge gate, and CI stays free on
draft/spec work.

## Decision

`.github/workflows/image.yml` builds from the repo-root `Containerfile` on
`push` to `main` only and publishes `ghcr.io/thoraxe/ols-patroller` with two
tags: `main` (rolling, latest merged state) and `sha-<full commit SHA>`
(per-merge, traceable pin). The #2 manifests deploy the published `:main` by
default via the kustomize `images` override.

## Consequences

Merge to `main` is the release event. `:main` is rolling — a Deployment
consuming it does not restart when the tag's digest changes, so updating the
running patroller is apply + rollout restart (in-cluster-deployment.md rule
17), while `:sha-<SHA>` pins an exact merge. The first publish needs the GHCR
package flipped to public once (or an image pull secret / overridden ref). CI
is delivery, not verification: a broken merge can publish a broken `:main` —
the escape hatch is the sha tag and fix-forward.
