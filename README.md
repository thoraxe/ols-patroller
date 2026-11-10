# Patroller — a resident SRE agent for Kubernetes and OpenShift

A **proactive, non-deterministic SRE agent** ("resident SRE"): on a schedule, an LLM
agentically explores a cluster **read-only**, validates reality against a persistent
**wiki** (its memory), updates/refines the wiki, and notifies a human (console /
apprise sinks) about anything it thinks is wrong.

The patroller is **platform-agnostic**: any Kubernetes or OpenShift cluster, any
OpenAI-compatible model endpoint, no vendor or Lightspeed code imported. It began as
an OpenShift Lightspeed research spike ([OLS-4439](https://redhat.atlassian.net/browse/OLS-4439));
OLS/Lightspeed is now an optional integration, not a dependency. The repository,
package, image, and manifest names keep the historical `ols-patroller` name until the
rename lands.

| Concern | Choice |
|---|---|
| Cluster | any Kubernetes/OpenShift, via `oc`/`kubectl`; RBAC is the enforcement boundary |
| Model | any OpenAI-compatible endpoint (`PATROL_BASE_URL` / `PATROL_MODEL`) |
| Engine | [`deepagents`](https://github.com/langchain-ai/deepagents) |
| Memory | a git-backed markdown wiki on a PVC (or any directory) |
| Notifications | console + [apprise](https://github.com/caronc/apprise) sinks |
| OLS/Lightspeed | optional: in-flight `AgenticRun` dedup today; governed remediation later (#12) |

Prior-art landscape and positioning: see [`docs/prior-art.md`](docs/prior-art.md).
Plans are tracked in [GitHub issues and milestones](../../milestones), not in a
document — v0.1 *in-cluster*, v0.2 *compounding wiki*, v0.3 *operational
hardening*, v0.3.1 *reporting reliability*, v0.3.2 *notification cadence*, v0.3.3 *reporting-bound patch*, v0.3.4 *truncation-aware empty-wiki reason*,
v0.4.0 *fleet memory & wiki usability*, plus a `future` milestone for unscheduled
direction and research.

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

1. Reads in-flight agentic work if the cluster exposes it (OLS `AgenticRuns`
   today; best-effort dedup context — a cluster without the CRD simply reports
   nothing).
2. Loads the wiki (`wiki/cluster-wiki.md`, seeded on first run).
3. Agentic loop: model explores via a single **`kubectl_readonly`** tool (it decides
   what to look at; ~5–15 steps typical).
4. The agent ends on its own with a plain-text summary (auto tool choice — no
   forced tool calls); a tool-free **shape pass** (`with_structured_output`) then
   converts the conversation into the typed report: `findings`,
   `notifications`, `updated_wiki`.
5. Writes the wiki back atomically and commits it to the wiki repo (one
   wiki-changing patrol, one commit; optional `PATROL_WIKI_REMOTE` replication);
   prints/sends notifications.

## Architecture

One patrol is a straight pipeline; the process is a timer around it (`--once` for
a single run):

```text
 timer / --once
       │
       ▼
 exploration ── deepagents agent, auto tool choice
       │         one tool: kubectl_readonly (guard-enforced)
       │         budgets: turn cap + input-token soft-stop
       ▼
 shape pass ── tool-free structured output → PatrolReport
       │         {findings, notifications, updated_wiki}
       │         bounds: 300 s/turn vs 900 s shape; 16k vs 32k tokens
       │         one retry; a gateway failure re-compacts and asks for brevity
       ▼
 wiki gate ── validate the skeleton → one repair pass → else keep the old wiki
       │
       ▼
 wiki tail ── expire stale entries → compact the anomaly log → stamp counters
       │         atomic UTF-8/LF write → git commit → optional push
       ▼
 delivery ── state-change filter → periodic digest → console + apprise sinks
```

- **Process:** `patrol.py` is the whole implementation (single-file by design).
  `main()` owns the event loop, the wiki repo, and the timer; the agent never
  touches disk or the shell.
- **Model seam:** any OpenAI-compatible endpoint. Exploration and the reporting
  pass are two `ChatOpenAI` instances from the same `make_model` seam, so each
  call shape carries its own request timeout and completion bound
  (ADRs 0018/0021/0031).
- **Failure behavior:** one retry per reporting call; a rejected wiki gets one
  repair pass; a patrol that cannot produce a report still advances the wiki
  clock with a minimal write and sends one FAILURE notification (rule 33) —
  memory never freezes silently.
- **Storage and packaging:** the wiki is a git repo on the `/wiki` PVC (one
  commit per accepted write; optional remote push). The image is built in CI
  from the pinned `Containerfile` + `requirements.txt`; `manifests/` deploys it
  with kustomize.

Deeper maps: [`ARCHITECTURE.md`](ARCHITECTURE.md) and the specs under
[`.ai/spec/`](.ai/spec/README.md).

## Engine

The agent loop is [langchain-ai/deepagents](https://github.com/langchain-ai/deepagents)
(`create_deep_agent`) — the same upstream library used by other agentic sandboxes
(including the OLS agentic sandbox), depended on **directly** (no vendor or
platform code imported, so the patroller stays standalone/open-source):

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
  pattern used by other agentic sandboxes.
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

## Security model

Read-only access is enforced in four independent layers; the tool guard is
defense-in-depth, not the boundary itself.

| Layer | Control | Enforced by |
|---|---|---|
| **1. Tool guard** | `kubectl_readonly` only: verb allowlist, no Secret access at all, no local-file/credential flags, no endpoint overrides, no `--raw` | unit tests |
| **2. Engine surface** | exactly `[read_file, kubectl_readonly]` — no shell, no filesystem writes, no subagents | fake-model e2e |
| **3. RBAC** | pod ServiceAccount bound to the built-in `cluster-reader` role (no Secret access, read verbs only) | cluster-side |
| **4. Network egress** | optional default-deny `NetworkPolicy`: cluster DNS + kube-apiserver + the model/sink/wiki-remote hosts | operator-applied (opt-in) |

**Tool guard (layer 1).** The only cluster tool whitelist-checks the first verb
(`get/describe/logs/top/version/api-resources/api-versions/explain/status/
cluster-info/whoami`) and runs via a `subprocess` list (no shell). It refuses all
Secret access (any command containing `secret` — names and metadata included, not
just `-o yaml/json/jsonpath` values), local-file/credential flags
(`-f`/`--filename`, `--kubeconfig`/token/cert, `*-file` output formats),
endpoint-override flags (`--server`/`-s` in every spelling — kubectl must use the
ambient in-cluster API endpoint), and `--raw` proxy/stats paths
(multi-hundred-KB dumps that would truncate). The patrol **never mutates the
cluster**; remediation is out of scope for the patroller itself (the governed
handoff is #12).

**Container and supply chain.** Non-root and random-UID compatible (`USER 1001:0`),
admitted under the restricted SCC: `runAsNonRoot`, `seccompProfile:
RuntimeDefault`, `allowPrivilegeEscalation: false`, all capabilities dropped.
Python dependencies install at build time from the exactly pinned
`requirements.txt` — nothing resolves or installs at start — on a pinned
UBI9-minimal base, with the `oc`/`kubectl` client fetched at build time from a
version-pinned mirror and SHA-256 verified before extraction. CI builds the
image; the manifests pin the current release tag with `imagePullPolicy: Always`
(a mutable tag must re-pull), testing the latest merge is a local `:main`
override (see [Test the latest merge](#test-the-latest-merge)), and releases are
registry-side retags of the same sha image (ADR 0010).

**Credentials and data egress.** All secret material arrives through the
`ols-patroller` Secret (`envFrom`); nothing sensitive is literal in a committed
manifest, and `secret.yaml` is gitignored. The optional wiki deploy key is its
own Secret, mounted read-only. **What leaves the cluster:** cluster observations
→ the configured model endpoint; findings/notifications → the configured apprise
sinks; the wiki → the optional git remote; plus the kube-apiserver and cluster
DNS. Nothing else is contacted. The optional egress allowlist (layer 4) turns
that list into a default-deny policy — render it with `--egress-policy` (see
[Restrict egress](#restrict-egress-optional)).

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

The committed kustomization pins the **current `vX.Y.Z` release tag**, not the
rolling `:main` tag: a plain `oc apply -k manifests/` deploys a known release,
not the latest merge. Testing unreleased work — or pulling from another
registry — is a local override that never changes the committed default (see
[Test the latest merge](#test-the-latest-merge)).

Check it worked:

```bash
oc auth can-i get pods -A --as=system:serviceaccount:ols-patroller:ols-patroller   # yes
oc -n ols-patroller logs deploy/ols-patroller                                      # patrol output
oc -n ols-patroller exec deploy/ols-patroller -- git -C /wiki log --oneline       # wiki commits
```

### Test the latest merge

The committed default is a release tag, so testing the latest merge is an
explicit local override — the same applies to pulling from another registry.
Pick one form:

```bash
# 1. Uncommitted working-tree edit of the images entry, then apply:
#      images:
#        - name: ols-patroller
#          newName: ghcr.io/thoraxe/ols-patroller
#          newTag: main
oc apply -k manifests/

# 2. Override the running Deployment directly (rolling latest):
oc -n ols-patroller set image deploy/ols-patroller ols-patroller=ghcr.io/thoraxe/ols-patroller:main

# 3. Pin one specific merge by its immutable sha-<SHA> tag:
oc -n ols-patroller set image deploy/ols-patroller ols-patroller=ghcr.io/thoraxe/ols-patroller:sha-<SHA>
```

A kustomize overlay works too. For another registry, use the same forms with
the other registry's `newName`/image ref. Overrides are local: never commit
them — the committed `newTag` stays a `vX.Y.Z` release tag, and changing the
release default is a release-pin commit (see [Releasing](#releasing)). The
pull policy is unchanged: a mutable `:main` override follows
`imagePullPolicy: Always`, while an immutable `sha-<SHA>`/digest pin may drop
it.

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

### Restrict egress (optional)

The patrol pod makes outbound connections to only four kinds of destination:
the model endpoint (`PATROL_BASE_URL`), the apprise notification sinks
(`PATROL_NOTIFY_URLS`), the optional wiki git remote (`PATROL_WIKI_REMOTE`), and
the cluster's kube-apiserver (with cluster DNS resolving the first three). You
can turn that into a default-deny network allowlist with a rendered
egress `NetworkPolicy` — opt-in, never part of the committed base.

The committed [`manifests/egress-networkpolicy.yaml.example`](manifests/egress-networkpolicy.yaml.example)
is a template; the operator's working copy
`manifests/egress-networkpolicy.yaml` is gitignored and applied out-of-band.

1. Render the policy from the live configuration. In the pod, where
   `KUBERNETES_SERVICE_HOST` carries the apiserver address:

   ```bash
   oc -n ols-patroller exec deploy/ols-patroller -- \
     python3.12 patrol.py --egress-policy > manifests/egress-networkpolicy.yaml
   ```

   On a workstation the same command needs `PATROL_BASE_URL` set (plus any
   optional `PATROL_NOTIFY_URLS`/`PATROL_WIKI_REMOTE` actually used); it
   renders without the kube-apiserver peer (stderr names the omission) and you
   add that `ipBlock` stanza by hand. You can also copy the template and edit
   its documentation placeholder addresses.

2. Apply it:

   ```bash
   oc apply -f manifests/egress-networkpolicy.yaml
   ```

3. Prevent drift: re-render and re-apply after **any** change to
   `PATROL_BASE_URL`, `PATROL_NOTIFY_URLS`, or `PATROL_WIKI_REMOTE`, and after a
   provider rotates its addresses (regeneration, not hand maintenance). The
   symptom of a stale snapshot is model-call or delivery failures in the pod
   logs — never silent loss; re-run step 1, diff, and re-apply.

Limitation: an apprise sink whose URL carries no derivable host — the
`discord://<id>/<token>` token-in-netloc webhook schemes, or a
`mailto://user@domain` sink whose SMTP host is not the URL domain — sits
outside the allowlist. The renderer names such values on stderr, and their
delivery fails once the policy is applied. Choose a host-form sink URL, add the
peer by hand knowing a re-render drops it, or leave the allowlist off.

To prove the allowlist bites, apply it and point `PATROL_NOTIFY_URLS` at a sink
whose host is not in the policy: the console `🔔 NOTIFY:` line still prints,
that sink's delivery fails with a stderr warning, and the patrol still exits
zero. Restore the Secret afterwards. Enabling the allowlist can only break
traffic, never heal it — that is the point of default-deny.

## Releasing

The committed manifest default is the current release tag, and the tagged tree
is the deployable artifact — so a release is pinned **before** it is tagged.

1. **Pin the manifest default.** Open a PR that sets the incoming release tag
   and the static suite's expected tag together:

   ```yaml
   images:
     - name: ols-patroller
       newName: ghcr.io/thoraxe/ols-patroller
       newTag: vX.Y.Z
   ```

   Merge it to `main`.
2. **Wait for the main-push build.** The merge publishes
   `ghcr.io/thoraxe/ols-patroller:sha-<SHA>` for the pin commit. Confirm that
   build succeeded — the retag copies exactly that image.
3. **Publish the GitHub release** whose tag points at the pin commit:

   ```bash
   gh release create vX.Y.Z --target <pin-commit> --title vX.Y.Z --notes "..."
   ```

   The `release: published` job then retags the image `vX.Y.Z` from its
   `sha-<SHA>` image — a registry-side copy, never a rebuild.
4. **Apply.** Once the retag has completed, `oc apply -k manifests/` on a
   cluster picks up the release tag (the pod template changes, so the rollout
   happens on its own).

**Never move** a published release tag — no retargeting, no force-update. A
wrong pin is fixed forward by the next release, and a release that predates
this rule gets a follow-up pin commit on `main`, never a moved tag. `image.yml`
only builds and retags; it never writes `manifests/`.

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

- Full graph runs: tool calls dispatched, guard refusals (mutation verb, Secret
  access), `execute`/`task` rejected as invalid tools (surface is exactly
  `[read_file, kubectl_readonly]`), natural termination and budget-cut paths
  both flow through the same shape pass (`PatrolReport` parsed), wiki written,
  notifications emitted.

Early real-cluster runs (2026-10-13, the original demo cluster / OCP 4.21,
EnMaaS GLM 5.3; that cluster happened to be loaded with the OLS/Lightspeed
stack, so the findings below are described generically):

- Patrol #4 (fresh wiki): 53 tool calls, 16 findings, 0 guard refusals;
  independently rediscovered the 19-day `wrker` nodeSelector typo plus new
  catches (DiskPressure↔workload-restart correlation, all-ephemeral platform
  state on unused storage, a personal registry source, 4 unapplied errata). Budget
  exhausted at 15 turns → salvage pass forced the final report (508k in /
  25k out tokens).
- Patrol #5 (memory loop): 45 tool calls, 17 findings; watch-list carried
  forward with per-item added/updated/unchanged tracking — "still unfixed at
  2nd patrol", DiskPressure downgraded to FYI after ~4.6h non-recurrence,
  emptyDir risk marked "materialized once, non-fatally" (rhokp Solr rebuild).
  One guard refusal: jsonpath extracting a credential field from a platform CR
  (strict by design; the model recovered via a plain `get -o yaml`).
- Patrol #6 (30-turn budget): 36 turns / 63 tool calls, ~1.02M input tokens —
  still ran to the budget under the then-forced tool choice; this motivated
  the shape-pass switch.
- Patrol #7 (shape pass, auto tool choice): **self-terminated** at 34 turns
  (33 tool calls + plain-text summary, vs the 36-turn hard budget), 23
  findings, 215k input tokens — 4.7× cheaper than #6. The summary itself
  shows the memory economy: "restart sweep matches the 18:02 baseline
  exactly" — wiki baselines verified instead of re-derived. Watch-list
  entries carry "Unfixed through 5th patrol" persistence tracking.

## Known limitations (pre-1.0)

- Pre-1.0 (single-file `patrol.py` heritage); fixtures are static and a real
  cluster exercises the loop differently.
- Single cluster, single wiki per process: fleet/multi-cluster layout is
  planned (#89 → #13).
- No per-scope cadence; the step and token budgets are the only cost controls.
- The wiki is a plain git-backed markdown file by design — not MCP or vector
  memory (see #16 for the trigger that would revisit it).
- Remediation is not implemented: the patrol is read-only, and the governed
  handoff (#12) is future work.
- The `ols-patroller` name (repo, package, image, manifests) is historical and
  will be untied from OLS over the coming releases.
- The investigation engine is intentionally swappable: HolmesGPT or an
  AgenticRun RCA phase can slot in later rather than growing this (see
  `docs/prior-art.md`).
