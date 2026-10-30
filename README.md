# OLS Patroller PoC

Tracked in [OLS-4439](https://redhat.atlassian.net/browse/OLS-4439) (research spike).

A hacky proof-of-concept of a **proactive, non-deterministic SRE agent** ("resident SRE"):
on a schedule, an LLM agentically explores a cluster **read-only**, validates reality
against a persistent **wiki** (its memory), updates/refines the wiki, and notifies a
human (console / apprise sinks) about anything it thinks is wrong.

Prior-art landscape and positioning: see [`docs/prior-art.md`](docs/prior-art.md).
Plans are tracked in [GitHub issues and milestones](../../milestones), not in a
document — v0.1 *in-cluster*, v0.2 *compounding wiki*, v0.3 *operational
hardening*, plus a `future` milestone for deferred direction.

## Run it

Requires: `uv`, plus credentials for any **OpenAI-compatible** LLM endpoint.

```bash
export PATROL_API_KEY=<your API key>
export PATROL_BASE_URL=<endpoint>      # e.g. https://api.example.com/v1
export PATROL_MODEL=<model id>

uv run patrol.py --once --dry-cluster     # no cluster needed; canned fixtures
uv run patrol.py --once                   # real cluster (needs oc/kubectl + kubeconfig)
uv run patrol.py --loop --interval 21600  # patrol every 6 h (the default cadence)
uv run patrol.py --wiki-stats             # the wiki's health numbers (incl. size) —
                                          # offline, no PATROL_* env or cluster needed
```

Per-turn output — model reasoning previews, tool invocations, the guard's expanded
command line, tool-result previews — streams to **stderr** as it happens, **on by
default**; add `-q`/`--quiet` to suppress it (findings and notifications always
print).

Run `--once --dry-cluster` **twice** to see the memory loop: patrol #1 maps the
cluster + writes the wiki; patrol #2 consults the watch-list, tracks persistence
across patrols, and escalates per its own previously-written rules.

`--wiki-stats` reads only the wiki (and its git history) and prints the health
numbers — **verification rate** (Watch-list entries whose process-stamped
`verified_patrol` equals the frontmatter `patrol_count`), **resolution ratio**
(watching/resolved/expired counts), **confidence spread** (with a warning when
one level holds ≥ 2/3 of ≥ 5 graded entries — a grade everyone gets is not a
grade), **churn** (entries whose prose changed in every one of the last 5
wiki commits with no status change — the photocopy-of-a-photocopy effect),
and **size** (the character count against the soft 20,000-char budget, with a
warning over it — the wiki is a working set; git history is the archive).
Hints on the human surface, never gates.

## What it does per patrol

1. Reads in-flight `AgenticRuns` (best-effort dedup context).
2. Loads the wiki (`wiki/cluster-wiki.md`, seeded on first run).
3. Agentic loop: model explores via a single **`kubectl_readonly`** tool (it decides
   what to look at; ~5–15 steps typical).
4. The agent ends on its own with a plain-text summary (auto tool choice — no
   forced tool calls); a tool-free **shape pass** (`with_structured_output`) then
   converts the conversation into the typed report: `findings`,
   `notifications`, `updated_wiki`.
5. Writes the wiki back atomically and commits it to the wiki repo (one
   wiki-changing patrol, one commit; optional `PATROL_WIKI_REMOTE` replication);
   prints/Sends notifications.

## Engine

The agent loop is [langchain-ai/deepagents](https://github.com/langchain-ai/deepagents)
(`create_deep_agent`) — the same upstream library the OLS agentic sandbox uses,
depending on it **directly** (no OLS code imported, keeps the patroller
standalone/open-source):

- **No backend** → no shell, no filesystem writes; built-in `ls/write_file/
  edit_file/delete/glob/grep/execute` are removed via a replacement
  `FilesystemMiddleware(tools=["read_file"])` (state-backed, in-memory).
- The default general-purpose `task` subagent is disabled via a registered
  `HarnessProfile`, keeping the patrol single-threaded.
- Effective tool surface: **`[read_file, kubectl_readonly]`** (verified in tests:
  `execute`/`task` calls are rejected as invalid tools).
- **No in-agent response format.** An earlier revision passed
  `ToolStrategy(PatrolReport)` to `create_deep_agent`, but that forces
  `tool_choice="any"` on every turn — under it GLM never self-terminated
  (every real patrol ran to the budget). Now the agent explores with natural
  tool choice and writes a plain-text summary when done; a separate tool-free
  reporting pass (`with_structured_output`, function-calling) shapes the
  finished conversation into the typed `PatrolReport` — the same "shape pass"
  pattern as the OLS agentic sandbox.
- Model via `langchain-openai`'s `ChatOpenAI` — any OpenAI-compatible endpoint,
  same `PATROL_*` env contract as before.
- Step budget = `recursion_limit` (`max_steps * 2 + 12`) caps exploration.
  Whether the patrol ends naturally or is cut off by the budget, the same
  reporting pass produces the report — one code path, no salvage special-case.

## The wiki (`wiki/cluster-wiki.md`)

Sections the agent reads **and rewrites** every patrol: Topology, Baselines,
Watch-list, Open questions, Anomaly log — entries tagged `(source, timestamp,
confidence)`. The wiki is **hypotheses**; live cluster is **truth**; each patrol
re-validates and expires stale claims. The watch-list is the refinement loop:
patrol N writes the rules patrol N+1 follows.

The wiki directory is a git repo (initialized at startup if absent): each
wiki-changing patrol stages exactly the wiki file and commits
`patrol N: X findings, Y warn` — irreversibility insurance, since a bad rewrite
is always revertible. Set `PATROL_WIKI_REMOTE` to replicate the repo off-cluster
(best-effort, bounded, never blocks the patrol; the local repo is the source of
truth). If git is unavailable, the patroller degrades to file-only. The wiki is
a bounded working set: every watching Anomaly-log entry plus the last 20
resolved/expired ones survive each accepted write (older closed entries are
pruned by the patroller — one console line each — and stay recoverable from
the repo's history), and a soft 20,000-character budget warns on overshoot
without ever rejecting a write.

## Safety model

- **Read-only by construction**: the only cluster tool is `kubectl_readonly`, which
  whitelist-checks the first verb (`get/describe/logs/top/version/api-resources/
  api-versions/explain/status/cluster-info/whoami`), runs via `subprocess` list
  (no shell), refuses Secret value reads (`-o yaml/json/jsonpath` on secrets), and
  refuses `--raw` proxy/stats paths (multi-hundred-KB dumps that would truncate).
  The deepagents engine adds **no shell/filesystem/execute tools** on top (see
  Engine above), and when deployed in-cluster the pod SA's RBAC (e.g.
  `cluster-reader`, which grants no Secret access) is the server-side enforcement
  boundary — the tool guard is defense-in-depth, not the boundary itself.
- The patrol **never mutates the cluster**. Remediation would be delegated to a
  governed AgenticRun (not implemented in the PoC).
- Guard is unit-checked (mutation verbs + secret reads + `--raw` paths → all refused).

## Config (env)

| Var | Default | |
|---|---|---|
| `PATROL_API_KEY` | — | API key (required) |
| `PATROL_BASE_URL` | — | any OpenAI-compatible endpoint, e.g. `https://api.example.com/v1` (required) |
| `PATROL_MODEL` | — | model id on that endpoint (required) |
| `KUBECTL` | `oc`→`kubectl` | cluster CLI |
| `PATROL_NOTIFY_URLS` | — | if set, apprise sink URLs (comma/newline separated); notifications are also delivered to every configured sink |
| `PATROL_NOTIFY_DIGEST_AFTER` | `7` | periodic-digest threshold in days: a standing watching wiki entry older than this many days, and not notified within this many days, is named by one process-authored digest; non-negative integer, `0` disables the digest (state-change notifications are unaffected) |
| `PATROL_MAX_STEPS` | `30` | LLM-turn exploration budget per patrol (raised from 15 after real-cluster runs; GLM needs >15 turns even single-node — if exhausted, the reporting pass reports from observations so far) |
| `PATROL_INTERVAL` | `21600` | seconds (`--loop`, 6 h) |
| `PATROL_TOOL_TIMEOUT` | `60` | per-command timeout |
| `PATROL_CLUSTER_ID` | `local` | cluster identity for wiki commit emails (`patrol@<id>`) |
| `PATROL_WIKI_REMOTE` | — | if set, wiki commits are pushed there (best-effort replication) |
| `PATROL_WIKI_STALE_AFTER` | `5` | auto-expire a watch-list entry — flip to `expired`, move it to the Anomaly log with an `auto-expired` annotation — when its `last_verified` has not advanced for this many accepted wiki writes; `0` disables the flip (the per-entry clock is still kept) |
| `--wiki-stats` (CLI flag) | — | print the wiki's health numbers (verification rate, resolution ratio, confidence spread, churn, size against the soft 20,000-char budget) and exit — fully offline: no `PATROL_*` env, no cluster, no repo initialization; a missing wiki file is an error, everything else degrades to `n/a`. Churn examines the last 5 wiki commits (prose-only — process stamps and date advances never count as rewording) and the lopsided-confidence warning fires at ≥ 2/3 of ≥ 5 graded entries; both thresholds are hard-coded, not configurable |

## Deploy in-cluster

The manifests in [`manifests/`](manifests/) deploy the patrol as a single-replica
Deployment on the cluster it watches (OpenShift with `oc`, or any Kubernetes with
`kubectl`):

```bash
cp manifests/secret.yaml.example manifests/secret.yaml   # fill in the values
oc apply -k manifests/
```

`secret.yaml` is gitignored — real credentials are never committed — and the
kustomization lists it, so the apply fails loudly until you create it. The pod
runs read-only (ServiceAccount bound to the built-in `cluster-reader`), keeps
its wiki on a PVC at `/wiki`, and needs the image pullable from
`ghcr.io/thoraxe/ols-patroller` (flip the GHCR package to public once, or edit
the `images:` entry in `kustomization.yaml` to a registry your cluster pulls).

Check it worked:

```bash
oc auth can-i get pods -A --as=system:serviceaccount:ols-patroller:ols-patroller   # yes
oc -n ols-patroller logs deploy/ols-patroller                                      # patrol output
oc -n ols-patroller exec deploy/ols-patroller -- git -C /wiki log --oneline       # wiki commits
```

### No default storage class?

The committed PVC sets no `storageClassName` — the cluster's default storage
class applies, which is the most portable form. If your cluster has **no**
default class, the claim needs an explicit one. Check with `oc get sc`: a
class is the default only when annotated
`storageclass.kubernetes.io/is-default-class: "true"`.

Copy the committed template to the gitignored operator copy, set your class
name in its `spec.storageClassName`, wire it into your local kustomization,
and re-apply:

```bash
cp manifests/pvc-patch.yaml.example manifests/pvc-patch.yaml
$EDITOR manifests/pvc-patch.yaml        # set spec.storageClassName: <your-class>
# add to your (gitignored) kustomization.yaml:
#   patches:
#     - path: pvc-patch.yaml
oc apply -k manifests/
```

Do this at **first deployment** time on a no-default-class cluster — the
class must be set before the claim binds. Once bound,
`spec.storageClassName` is immutable: a live class the committed manifest
doesn't declare makes every `oc apply -k` fail on the PVC (the other
objects still apply, but the exit code breaks scripted use — the patch
resolves it by making the manifest match the bound claim).

### Remote wiki replication (optional)

The wiki replicates off-cluster if `PATROL_WIKI_REMOTE` is set in the patrol
Secret (best-effort: the push never blocks a patrol; the local repo on the PVC
is the source of truth). For an SSH remote (e.g.
`git@github.com:you/cluster-wiki.git`) the push needs a deploy key — wired in
as an opt-in patch, so the base manifests stay self-contained (`oc apply -k`
works with zero git wiring):

1. Generate an ed25519 key pair for the wiki remote:

   ```bash
   ssh-keygen -t ed25519 -f wiki-deploy-key -C ols-patroller-wiki -N ""
   ```

2. Register the public key (`wiki-deploy-key.pub`) with your git host as a
   **write** deploy key on the wiki repo.

3. Create the Secret server-side only — never as a committed or gitignored
   manifest. The patch references it by name, so a missing Secret blocks pod
   creation visibly rather than silently skipping replication:

   ```bash
   oc -n ols-patroller create secret generic ols-patroller-deploy-key \
     --from-file=id_ed25519=wiki-deploy-key
   ```

4. Copy the committed patch template in and enable it — the copy is gitignored
   (like `secret.yaml`):

   ```bash
   cp manifests/deploy-key-patch.yaml.example manifests/deploy-key-patch.yaml
   ```

   and add one entry to your `manifests/kustomization.yaml`:

   ```yaml
   patches:
     - path: deploy-key-patch.yaml
   ```

5. Set `PATROL_WIKI_REMOTE` to the SSH remote in `manifests/secret.yaml` —
   uncomment the `PATROL_WIKI_REMOTE` line and point it at your repo, e.g.
   `git@github.com:you/cluster-wiki.git`.

6. Re-apply: `oc apply -k manifests/`.

The patch mounts the key read-only at `/wiki-deploy-key` (mode `0440` — the
non-root pod reads it via group-read; the template's comments explain why) and
sets `GIT_SSH_COMMAND` to use it with `StrictHostKeyChecking=accept-new`
(trust-on-first-use). To pin the host key instead, see the commented
`known_hosts` variant in `deploy-key-patch.yaml.example`. Verify replication by
watching the remote's copy advance one commit per patrol — `git ls-remote
<remote-url>` from anywhere shows the remote head.

## Smoke-test results (2026-10-13, dry-cluster)

Raw-requests engine, GLM 5.3:

- Patrol #1: 13 steps / 44 tool calls; found all planted fixture issues
  (NotReady node, CrashLoopBackOff pod, Degraded monitoring CO, Progressing
  ingress), flagged the fixture cluster's implausibly small footprint as
  "restricted visibility", wrote a fully-structured wiki.
- Patrol #2: 5 steps; consulted the watch-list, tracked persistence across
  patrols, escalated worker-1 per its own rule, *declined* to over-escalate
  ingress (only ~3 min elapsed between patrols), tracked restart-count delta.

deepagents engine (scripted fake-model e2e, no endpoint needed):

- Full graph runs: tool calls dispatched, guard refusals (mutation verb, secret
  read), `execute`/`task` rejected as invalid tools (surface is exactly
  `[read_file, kubectl_readonly]`), natural termination and budget-cut paths
  both flow through the same shape pass (`PatrolReport` parsed), wiki written,
  notifications emitted.

Real-cluster runs (2026-10-13, ols-demo-2 / OCP 4.21, EnMaaS GLM 5.3):

- Patrol #4 (fresh wiki): 53 tool calls, 16 findings, 0 guard refusals;
  independently rediscovered the 19-day `wrker` nodeSelector typo plus new
  catches (DiskPressure↔OLS-restart correlation, all-ephemeral OLS state with
  unused lvms-vg1, personal-quay RAG source, 4 unapplied errata). Budget
  exhausted at 15 turns → salvage pass forced the final report (508k in /
  25k out tokens).
- Patrol #5 (memory loop): 45 tool calls, 17 findings; watch-list carried
  forward with per-item added/updated/unchanged tracking — "still unfixed at
  2nd patrol", DiskPressure downgraded to FYI after ~4.6h non-recurrence,
  emptyDir risk marked "materialized once, non-fatally" (rhokp Solr rebuild).
  One guard refusal: jsonpath extracting `credentialsSecret` from OLSConfig
  (strict by design; model recovered via a plain `get -o yaml`).
- Patrol #6 (30-turn budget): 36 turns / 63 tool calls, ~1.02M input tokens —
  still ran to the budget under the then-forced tool choice; this motivated
  the shape-pass switch.
- Patrol #7 (shape pass, auto tool choice): **self-terminated** at 34 turns
  (33 tool calls + plain-text summary, vs the 36-turn hard budget), 23
  findings, 215k input tokens — 4.7× cheaper than #6. The summary itself
  shows the memory economy: "restart sweep matches the 18:02 baseline
  exactly" — wiki baselines verified instead of re-derived. Watch-list
  entries carry "Unfixed through 5th patrol" persistence tracking.

## Known limitations (it's a hacky PoC)

- Fixtures are static; a real cluster will exercise the loop differently.
- No per-scope cadence, no cost control beyond step budget, no notify-dedup
  beyond the anomaly log, wiki is a plain file (not MCP/vector memory).
- Investigation engine is intentionally dumb-pluggable: swap in HolmesGPT or an
  AgenticRun later rather than growing this (see `docs/prior-art.md`).
