# 0010. Release tags are registry-side retags of the sha image, not rebuilds

Date: 2026-10-15 · Status: proposed

## Context

v0.1 shipped with no `v0.1` container image: `image.yml` triggers on pushes to
`main` only, so a GitHub release is invisible to GHCR. Making releases visible
has two candidate mechanisms: (a) rebuild the image from the tagged commit when
a release is published; (b) copy the registry manifest of the already-published
`sha-<full SHA>` image to the release tag name (a registry-side retag —
`docker buildx imagetools create`).

Rebuilding duplicates every release build and, worse, can drift from what
`main` published for the same commit: the base image tag and pip's transitive
resolution move between merge and release, so the release tag would not be
bit-identical to the image CI built — and #1 pins versions, not digests (PoC
grade). Retagging is a manifest copy: no build, no pull, and the release tag
is by construction the same digest the main-push workflow published for that
commit.

## Decision

On a published release whose tag starts with `v`, `image.yml` retags
`ghcr.io/thoraxe/ols-patroller:sha-<SHA of the tagged commit>` to the release
tag name via a registry-side manifest copy; it never rebuilds. A
`workflow_dispatch` input applies the same retag manually (retro-fitting
releases that predate the trigger, e.g. v0.1, and recovering from failed
runs). Contract: `container-image.md` rules 17–18.

## Consequences

A release tag is a pointer, not an artifact: it depends on the main-push
pipeline having succeeded for that commit — releasing an unbuilt commit fails
loudly rather than silently skipping the tag. Releases of non-`v` tags are
skipped. No `latest` tag is published (`main` stays the rolling tag). The
build job gains an event guard so release/dispatch events can never trigger a
build. Release tags become the natural pin for deployments that want "a known
release" rather than "latest main" or "exact merge".
