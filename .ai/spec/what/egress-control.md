# Egress Control (data-exfiltration prevention)

The rudimentary network-containment layer: the deployed patroller's outbound
traffic is **default-deny**, with the allowlist *derived* from the same
configuration the process uses to make those connections. Hand-written
NetworkPolicy egress lists are not enough on their own — the external providers
the patrol must reach are exactly the destinations whose addresses are not
stably knowable, and a hand-kept CIDR list is a second source of truth that
drifts from the Secret (issue #34; ADR
`decisions/0030-derived-network-egress-allowlist.md`). This file is the
contract for the derivation, the rendered artifact, the operator procedure, and
the one in-agent egress channel the guard closes.

## Behavioral Rules

### The egress surface

1. The patroller's outbound destinations are exactly four: the model endpoint
   (`PATROL_BASE_URL`), the apprise notification sinks (`PATROL_NOTIFY_URLS`),
   the optional wiki git remote (`PATROL_WIKI_REMOTE`), and the cluster's
   kube-apiserver (every `kubectl_readonly` read), with cluster DNS resolving
   the first three. The model's tool surface is exactly
   `[read_file, kubectl_readonly]` (`safety-model.md` rule 3), so no tool call
   can open a destination of its own: what leaves the process leaves through
   this set, or through a destination the process's *configuration* names.
   [PLANNED: #34]
2. **Default deny.** When the egress allowlist is applied, the patrol pod may
   reach only the cluster DNS service, the kube-apiserver, and the derived
   destination addresses (rules 4–5); every other destination is dropped by the
   cluster's network implementation, not by the process. The policy is
   namespace-scoped and pod-selected on the deployment's label, so no other
   workload's traffic is affected. [PLANNED: #34]
3. The in-agent endpoint-override channel is closed in the guard:
   `kubectl --server`/`-s` re-points the CLI at a caller-supplied API endpoint,
   so it is refused (`kubectl-guard.md` rule 6c). [PLANNED: #34]

### Derivation (one source of truth)

4. `egress_destinations(base_url, notify_urls, wiki_remote) -> (hosts, notes)`
   is the **only** definition of the destination set: pure, deterministic, no
   I/O, no DNS, no filesystem. `hosts` is the sorted, case-insensitively
   deduplicated list of lowercased hostnames/address literals the configuration
   names; `notes` is the list of single-line diagnostics the CLI prints to
   stderr (rule 7b). A destination set maintained by hand next to this
   function is a contract violation. [PLANNED: #34]
   4a. **The model endpoint** contributes its `urlsplit` hostname (no port, no
       path, no credentials). An empty or host-less value contributes no host
       and one note naming `PATROL_BASE_URL`; for this key that note is followed
       by the CLI's fatal refusal (rules 7a and 7d), so a rendered policy never
       lacks the model-endpoint peer.
   4b. **Each apprise sink URL** (the `parse_notify_urls` list,
       `reporting-notifications.md` rule 10a) contributes its `urlsplit`
       hostname when that hostname is **host-like**: an IP literal, a name
       containing a dot, or a single label that is not all digits. An apprise
       URL whose host position is not host-like — the token-in-netloc webhook
       schemes, e.g. `discord://<numeric id>/<token>` — contributes nothing and
       one note naming `PATROL_NOTIFY_URLS` and the URL's scheme: such a sink
       sits outside the allowlist (Constraints). An unset `PATROL_NOTIFY_URLS`
       contributes nothing and produces no note.
   4c. **The wiki remote** contributes its `urlsplit` hostname for
       `scheme://…` forms (`ssh://`, `git+ssh://`, `git://`, `https://`,
       `http://`); the scp-like form with no scheme (`[user@]host:path`,
       e.g. `git@github.com:you/cluster-wiki.git`) contributes the text between
       the optional `user@` and the first `:`. An unset `PATROL_WIKI_REMOTE`
       contributes nothing and produces no note.
   4d. Every note is one line beginning `egress: ` and naming the
       configuration key it came from; a note never aborts the render (a
       legitimate sink outside the allowlist is the operator's call, rules
       4b/Constraints).
   4e. The order is fixed: `hosts` sorted ascending, duplicates removed — the
       same configuration triple always yields the same list, so a re-render
       is diffable.

### Resolution

5. `resolve_hosts(hosts) -> list[str]` resolves each host to its addresses
   (IPv4 and IPv6), deduplicated and sorted as text. An address literal (per
   `ipaddress.ip_address`) passes through unchanged. A host that yields no
   address, or whose lookup raises, is an error: `ValueError` naming the host —
   never a partial result, because an allowlist silently missing a destination
   is the silent patrol break this control exists to avoid. [PLANNED: #34]

### Rendering

6. `render_egress_policy(addresses, *, api_server=None) -> str` is pure and
   returns the policy text: exactly one trailing newline, no blank line, never
   `\r`, an LF-terminated `networking.k8s.io/v1`
   `NetworkPolicy` named `ols-patroller-egress` in namespace `ols-patroller`,
   parsing with `yaml.safe_load` into this shape and nothing more: [PLANNED: #34]
   - `spec.podSelector.matchLabels` = `{app: ols-patroller}` — the deployment's
     label (`in-cluster-deployment.md` rule 13);
   - `spec.policyTypes` = `[Egress]` — ingress is untouched, and there is no
     `ingress` key;
   - one DNS egress rule: peers selecting each namespace in
     `EGRESS_DNS_NAMESPACES` (an ordered tuple: `openshift-dns`, then
     `kube-system`) by the
     `kubernetes.io/metadata.name` label, restricted to ports 53/UDP and
     53/TCP;
   - when `api_server` is given, one egress rule with a single `ipBlock` peer
     for it;
   - one egress rule whose peers are one `ipBlock` per entry of `addresses`;
     when `addresses` is empty there is **no** destination rule (an empty peer
     list is ambiguous, never implicitly permissive);
   - a peer's `cidr` is `<address>/32` for IPv4 and `<address>/128` for IPv6;
   - destination and apiserver peers carry **no** `ports` (the allowlist is
     destination-keyed, Constraints), and the address order inside a rule is
     the sorted order of rule 5, so the rendered text is byte-stable.
6a. `api_server=None` renders the policy without the apiserver rule (rule 7c).
    The renderer never invents a destination: no configured value, no peer.

### The renderer command

7. `patrol.py --egress-policy` prints the policy for the process's own
   configuration to stdout and exits 0: derive (rule 4) → resolve (rule 5) →
   render (rule 6). It is an **offline** mode in the `--wiki-stats` slot
   (`wiki-memory.md` rule 23) — it branches right after argparse, before the
   model-key env check, before CLI resolution, and before wiki-repo init; it
   makes no model call, touches no cluster, and writes nothing. [PLANNED: #34]
   7a. Its inputs are the egress keys only: `PATROL_BASE_URL` is **required** —
       unset, empty, or a value no host can be derived from (rule 4a) exits
       non-zero with an error naming it (the model endpoint is the largest
       payload channel; an allowlist without it is a broken policy) — while
       `PATROL_NOTIFY_URLS` and `PATROL_WIKI_REMOTE` are optional. It never
       reads `PATROL_API_KEY` or `PATROL_MODEL`.
   7b. **stdout is exactly the policy YAML**, so the artifact is a redirect
       (`uv run patrol.py --egress-policy > manifests/egress-networkpolicy.yaml`).
       Every diagnostic goes to stderr: the rule-4d notes, rule 7c's omission
       line (only when `KUBERNETES_SERVICE_HOST` is unset), and any resolution
       failure — nothing else is printed.
   7c. The kube-apiserver peer comes from the in-pod env
       `KUBERNETES_SERVICE_HOST`, resolved per rule 5: the mode is documented to
       run in the pod (`oc -n ols-patroller exec deploy/ols-patroller --
       python3.12 patrol.py --egress-policy`) where that variable exists. When
       it is unset (a workstation run) the policy renders without that peer,
       one stderr line names the omission by naming `KUBERNETES_SERVICE_HOST`
       and the `ipBlock` stanza to add by hand, and the exit code is still 0 —
       the apiserver's address is not in the patrol's own configuration, so it
       cannot be derived, and a hand-added peer is the documented fallback.
   7d. A missing, empty, or host-less `PATROL_BASE_URL` (rules 7a, 4a) or an
       unresolvable host (rule 5) exits non-zero with an error on stderr and
       **empty stdout** — never a partial policy.
   7e. Combining `--egress-policy` with `--loop` or with `--wiki-stats` is an
       argparse usage error (exit 2) whose message contains `cannot be
       combined` and names both flags — the `--wiki-stats`/`--loop`
       convention (`wiki-memory.md` rule 23); combining it with `--once`,
       `--dry-cluster`, or `--wiki` is accepted and inert.

### The artifact and the procedure

8. `manifests/egress-networkpolicy.yaml.example` is a committed **template**
   (the `secret.yaml.example`/`deploy-key-patch.yaml.example`/
   `pvc-patch.yaml.example` pattern): a complete policy of rule 6's shape with
   RFC 5737 documentation addresses as placeholders, carrying the procedure in
   comments — render (rule 7), apply, regenerate after any egress-config
   change — and naming the three egress keys the procedure reads
   (`PATROL_BASE_URL`, `PATROL_NOTIFY_URLS`, `PATROL_WIKI_REMOTE`). The
   operator's working copy `manifests/egress-networkpolicy.yaml`
   is **gitignored** and applied out-of-band: `oc apply -f
   manifests/egress-networkpolicy.yaml`. [PLANNED: #34]
   8a. The allowlist is opt-in: the base apply stays self-contained. The
       committed `kustomization.yaml` still lists exactly six resources
       (`in-cluster-deployment.md` rule 1) and names neither the policy nor its
       artifact; no committed manifest references the policy, and `patrol.py`
       never reads the artifact (rule 9a).
   8b. `README.md`'s in-cluster deployment section carries a
       `### Restrict egress (optional)` subsection documenting the procedure
       end-to-end and self-sufficiently (the rule-16d standard): render in the
       pod (or on a workstation plus the hand-added apiserver peer), apply, the
       regeneration rule (rule 8c), the apprise-scheme limitation (rule 4b),
       and how to prove the allowlist bites (rule 10). No reference to
       `.ai/spec/`.
   8c. **The staleness answer.** The derived address set is a snapshot of
       resolved addresses, so the contract is regeneration, not hand
       maintenance: after **any** change to `PATROL_BASE_URL`,
       `PATROL_NOTIFY_URLS`, or `PATROL_WIKI_REMOTE`, re-run `--egress-policy`
       and re-apply; after a provider rotates its addresses (the symptom is
       model-call or delivery failures in pod logs, never silent loss), do the
       same. The README subsection states both, naming the three keys
       (rule 8b).

### What the process does not do

9. The patroller holds no egress-controlling behavior: it never applies,
   patches, queries, or reads the generated policy — the operator owns it, like
   every other in-cluster object (`constraints.md` rule 2, read-only forever).
   [PLANNED: #34]
   9a. The artifact path is operator surface only: the string
       `egress-networkpolicy` never appears in `patrol.py`.
   9b. The process never routes around the allowlist: a blocked destination
       surfaces through the existing failure paths — a model-call error becomes
       the classified attempt-failure line
       (`reporting-notifications.md` rule 7f), a sink failure the rule-10c
       stderr line, a push failure `wiki-memory.md` rule 4e's bounded
       give-up — so the patrol degrades exactly as specified elsewhere.

### Acceptance (done-when)

10. Operator acceptance, performed on a dev cluster with `oc` (documented here,
    not in the pytest suite — the `in-cluster-deployment.md` rule 17 pattern):
    [PLANNED: #34]
    1. render and capture —
       `oc -n ols-patroller exec deploy/ols-patroller -- python3.12 patrol.py
       --egress-policy > /tmp/egress.yaml` (or render on a workstation with the
       same values and add the apiserver peer by hand, rule 7c);
    2. apply — copy to `manifests/egress-networkpolicy.yaml`,
       `oc apply -f manifests/egress-networkpolicy.yaml`;
    3. the patrol still works — `in-cluster-deployment.md` rule 17 step 4's
       check (findings and notifications in the pod logs, the wiki commit
       advances);
    4. the allowlist bites — point `PATROL_NOTIFY_URLS` at a sink whose host is
       not in the policy: the console `🔔 NOTIFY:` line still prints, that
       sink's delivery fails with the rule-10c stderr line, and the patrol
       still exits zero. Restore the Secret;
    5. regeneration — change `PATROL_BASE_URL`, re-run step 1, diff the
       artifact, re-apply, and confirm the patrol works again.

## Configuration Surface

- `--egress-policy` — the offline renderer: policy YAML on stdout, diagnostics
  on stderr, exit 0 on success and non-zero per rule 7d (mode contract:
  rules 7–7e)
- `PATROL_BASE_URL`, `PATROL_NOTIFY_URLS`, `PATROL_WIKI_REMOTE` — reused as the
  allowlist's input (rule 4); **no new environment variable exists**
- `KUBERNETES_SERVICE_HOST` — read by the renderer for the apiserver peer
  (rule 7c); not new configuration
- `manifests/egress-networkpolicy.yaml.example` — committed template
  (rule 8); `manifests/egress-networkpolicy.yaml` — gitignored operator
  artifact, applied with `oc apply -f` (rule 8a)
- hard-coded constants (not env-configurable, the ADR 0015 convention):
  `EGRESS_POLICY_NAME` (`ols-patroller-egress`) and `EGRESS_DNS_NAMESPACES`
  (a tuple, in order: `openshift-dns`, `kube-system`) — rule 6

## Constraints

- **The allowlist is a snapshot** (rule 8c): resolved addresses do not survive a
  provider rotating them, and every egress-config change needs a re-render and
  re-apply. Accepted as the rudimentary first step (ADR 0030).
  OpenShift's `EgressFirewall` with `dnsName` peers solves rotation natively but
  is OpenShift-only, namespace-wide, and unservable on a vanilla-Kubernetes
  kustomize apply — a documented **non-normative** alternative, deliberately
  not implemented (ADR 0030).
- **Destinations, not payloads.** The allowlist constrains where traffic may go;
  what may travel is the guard's and the Secret ban's business
  (`kubectl-guard.md` rules 5–6, ADR 0028). Content/DLP scanning, egress
  proxies, and admission-webhook enforcement are out of scope (ADR 0030).
- **Destination-keyed, not port-keyed** (rule 6): no `ports` on the destination
  peers. Rudimentary by intent, and it keeps the derivation independent of
  apprise's per-scheme transport defaults (IMAP 993, SMTP 587, …), which are
  not in the URL.
- **apprise schemes the derivation cannot cover** (rule 4b): token-in-netloc
  webhook URLs contribute no host, and a `mailto://user@domain` sink's derived
  host is the address domain, not the SMTP host the driver actually connects to.
  Such sinks sit outside the allowlist and fail delivery once the policy is
  applied; the operator chooses a host-form sink URL, adds the peer by hand
  knowing a re-render drops it, or leaves the allowlist off. The renderer names
  every such value on stderr (rule 4d) rather than letting it fail silently.
- **Enabling the allowlist can only break traffic, never heal it** — that is the
  point of default-deny. The failure is visible in pod logs (rule 9b), and the
  policy never ships enabled in a committed manifest (rule 8a).
- The policy object is namespace-scoped and pod-selected (rule 6): it never
  constrains the observed cluster's other workloads, and the patroller's
  read-only posture is untouched (rule 9).

## Planned Changes

| Ticket | Summary |
|---|---|
| #34 | This file's contract: the four-channel egress surface (rule 1), the default-deny policy (rule 2), the pure derivation `egress_destinations` (rules 4–4e) and resolution `resolve_hosts` (rule 5), the pure `render_egress_policy` shape (rules 6–6a), the offline `--egress-policy` mode (rules 7–7e), the committed template + gitignored artifact + README procedure + regeneration discipline (rules 8–8c), the process-has-no-egress-behavior pins (rules 9–9b), and the operator acceptance procedure (rule 10). The in-agent endpoint-override refusal lands in `kubectl-guard.md` rule 6c; `in-cluster-deployment.md`'s "no NetworkPolicy" constraint is superseded; ADR 0030. Impl commit also syncs `AGENTS.md`'s test list, `README.md`'s guard bullets, and `how/project-structure.md`'s symbol map out of its `[PLANNED: #34]` markers |
