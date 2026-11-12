# 0039. One wiki repo, one branch per cluster: the ref layout is the fleet index

Date: 2026-11-12 · Status: proposed (issue #89)

## Context

ADR 0003 made the wiki a local git repo. Rule 4e replicates it: the patroller
pushes the current branch to a same-named branch on `PATROL_WIKI_REMOTE`. That
works for one cluster and one repo. The fleet goal (#13) needs one remote repo
to hold many clusters' wikis. The spoke must push without contention, and the
hub must read every cluster without a merge protocol.

Two shapes were possible. Many files on one branch (`<id>/cluster-wiki.md`)
force every spoke to push `main`. The push path never fetches, rebases, or
merges, so concurrent spokes are non-fast-forward and fail. That shape works
only with one writer (a central hub) or a new pull/merge protocol. One branch
per cluster needs neither.

## Decision

Use one remote repo with one branch per cluster. Each branch is
`cluster/<CLUSTER_ID>` and carries that cluster's `cluster-wiki.md` at the
same path (`wiki-memory.md` rules 4a, 4a1, 4e, 4f1, 4f2).

A wiki repo initializes on its cluster branch. An adopted repo keeps its own
branch, typically `main`. The same-named push then follows that branch. The
patroller never renames a branch and never rewrites history.

`CLUSTER_ID` becomes a ref component, so it must be a slug. The rule rejects
uppercase, spaces, `..`, a leading `-`, a trailing `.`, a `.lock` suffix, and
every ref-special character. A bad value fails patrol startup with a config
error. The offline modes (`--wiki-stats`, `--egress-policy`) do not require a
valid value.

The hub reads every branch. It fetches the remote's branches and reads
`cluster-wiki.md` at each tip. A `git worktree add` per branch
is an operator convenience, never a requirement. The hub never pushes to a
spoke branch.

## Consequences

Two spokes push one remote path with no contention. Each branch is a
fast-forward of its own history only. No merge code exists, and no new
dependency appears. `/wiki`, `--wiki /wiki/cluster-wiki.md`, the manifests,
and the one-document constraint stay as they are. Rule 4d already stages only
the wiki file, and the frontmatter already carries `cluster_id`, so
attribution survives the move to one repo.

A pre-#89 repo on `main` keeps working. It stays on `main` and keeps pushing
`main`. The operator can run one `git branch -m cluster/<id>` by hand to join
the fleet layout. The patroller never does that automatically, because a
rename is a history change and the wiki repo is the audit trail (ADR 0003).
A pre-existing `PATROL_CLUSTER_ID` that is not a slug (for example
`MyCluster`) now fails startup with a config error. The operator renames the
branch by hand and fixes the value. This ADR adds no automatic migration.

The push failure mode stays best-effort. A remote branch that diverged (for
example, someone amended it) rejects the push as non-fast-forward. The local
commit stands, and the push retries the same best-effort way. The patroller
never force-pushes, so a hub edit cannot be silently overwritten.

Local behavior is single-branch. Expiry, compaction, stamps, churn, history,
and `--wiki-stats` read the local file and the current branch only. They never
fetch or read another cluster's branch, so a spoke cannot make a fleet-wide
assumption. Fleet-wide reads belong to the hub (#13).

Follow-ups. The committed sample remote in `manifests/secret.yaml.example`
still names a per-cluster repo. That stays valid (one branch in one repo), and
the README documents the shared-repo form at implementation. The hub's own
read-only patrol is #13, not this ADR.
