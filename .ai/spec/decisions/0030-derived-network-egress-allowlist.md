# 0030. Derived network egress allowlist (data-exfiltration prevention)

Date: 2026-11-04 · Status: proposed

## Context

The patrol reads a live cluster and sends what it read to an external model
endpoint; it also delivers notifications to apprise sinks and may push the wiki
to a git remote. Nothing in the process's tool surface can open a destination —
the model's tools are exactly `[read_file, kubectl_readonly]`, with no shell and
no network tool (`safety-model.md` rule 3) — so the outbound channels are the
four the *process* owns: the model endpoint, the notification sinks, the wiki
remote, and the kube-apiserver (with cluster DNS resolving the first three).
Exfiltration can ride that fixed set, or a destination the process's
configuration names after a tampered Secret or a compromised dependency.

Network-level controls alone are the deficient case #34 describes. A
hand-written `NetworkPolicy` egress list has to name IP addresses, and the
external providers are exactly the destinations whose addresses are *not*
stably knowable: CDN-fronted model endpoints and SaaS notification sinks rotate
them on the provider's schedule, undocumented. The operator's choices are then
a stale list that breaks the patrol silently at the next rotation, or
`0.0.0.0/0` on 443, which controls nothing. The list also lives away from the
Secret the patroller actually reads, so the two drift the moment either changes
— a policy that says one thing and a process that talks to another is worse
than no policy, because it reads as a control.

Design space considered: (a) a hand-maintained CIDR template; (b) an allowlist
*derived* from the process's own egress configuration by an offline renderer;
(c) OpenShift's `EgressFirewall` with `dnsName` peers; (d) an in-process
allowlist that refuses outbound URLs not in the derived set; (e) content/DLP
scanning of what is sent.

## Decision

Derive the allowlist from the same configuration the process uses (option b).
`patrol.py --egress-policy` — an offline mode in the `--wiki-stats` slot
(`wiki-memory.md` rule 23) — reads `PATROL_BASE_URL`, `PATROL_NOTIFY_URLS`, and
`PATROL_WIKI_REMOTE`, derives their hosts with a pure function, resolves them,
and prints a default-deny egress `NetworkPolicy` (DNS and kube-apiserver
allowances included) whose only writable destinations are those addresses. The
artifact is an opt-in operator file (`manifests/egress-networkpolicy.yaml`,
rendered or copied from the committed `.yaml.example` template) applied
out-of-band with `oc apply -f`; the committed base stays exactly six resources.

The contract is "one source of truth, regenerate after any config change", not
"keep a CIDR list in sync by hand": the derived set is a **snapshot** of
resolved addresses, so it must be re-rendered and re-applied after changing an
egress env key and after a provider address rotation (which surfaces as
model-call or delivery failures in pod logs, never as silent data loss).
The same issue closes the one in-agent egress channel: `kubectl --server`
overrides the API endpoint, so the guard refuses endpoint-override flags
(`kubectl-guard.md` rule 6c) — a strictness increase, no ADR of its own. The
in-process allowlist (option d) is deferred: every outbound URL is
config-derived by construction, so it adds no containment against the cases
that matter (compromised dependency, tampered Secret), which are exactly the
ones only cluster-enforced default-deny covers.

## Consequences

The allowlist is enforced by the CNI, not by the process: a compromised
dependency or a poisoned `PATROL_NOTIFY_URLS` reaches only destinations the
cluster already allowed. It costs an operator step at every egress-config
change, and it cannot survive provider rotation unattended — both accepted, and
stated in the contract (`what/egress-control.md` rules 8b–8c) and the README
procedure rather than hidden.

Two boundaries are deliberate and recorded there: the allowlist constrains
destinations, never payloads (payload control is the guard's Secret ban,
ADR 0028, not DLP), and apprise sinks whose URL carries no host component
(`discord://<id>/<token>` and similar token-in-netloc schemes) cannot be
derived — they fall outside the allowlist, named on stderr by the renderer,
and fail delivery once the policy is applied.
OpenShift's `EgressFirewall` with `dnsName` peers (option c) solves rotation
natively and stays a documented, **non-normative** alternative: it is
OpenShift-only, namespace-wide, and the committed root must also apply on
vanilla Kubernetes (`in-cluster-deployment.md` rule 1 is a plain `oc apply -k`
over standard `networking.k8s.io` objects), so it would carry a CRD the cluster
may not serve. Not implemented here.

Deferred: egress proxies, admission webhooks enforcing the policy, dependency
auditing, and content scanning — none is rudimentary, and each would need its
own decision record. The follow-ups live in #34's family, not in this contract.
