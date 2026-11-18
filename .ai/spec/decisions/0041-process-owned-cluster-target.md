# 0041. The cluster target is process configuration

Date: 2026-11-17 · Status: proposed (issue #122)

Numbered 0041 because the parallel #119 contract takes ADR 0040.

## Context

The patroller normally uses the pod service account and the in-cluster API.
A deployment may need to watch a different cluster.

The model must never choose the target. The guard already refuses `--server`
and every credential flag (rules 6b/6c). So the target must be process
configuration, not a tool argument.

## Decision

Add two environment values: `PATROL_API_SERVER` and `PATROL_API_TOKEN`.
A server without a token, or a token without a server, is a startup error.
With no server, behavior is unchanged.

At startup the process builds a private temporary kubeconfig from the values.
Every kubectl call receives `KUBECONFIG` for that file. The process removes the
file at exit. The token never enters argv, the prompt, or a log line.

The guard is unchanged. The model still cannot supply an endpoint or a
credential flag.

## Consequences

One process targets one cluster. There is no hub and no fleet read.
`PATROL_CLUSTER_ID` and the wiki layout do not change; the operator sets the
target cluster's identity.

The target cluster's RBAC is the server-side boundary. The base manifests still
grant `cluster-reader` locally only. The operator creates the same binding on
the target cluster.

CA handling stays outside this change. An operator who needs a private CA can
mount a kubeconfig later. This decision adds no CA flag and no manifest change.
