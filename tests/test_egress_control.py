"""Egress control — contract tests for #34.

Contract: `.ai/spec/what/egress-control.md` (rules 1-10) plus the guard's
endpoint-override refusal (`.ai/spec/what/kubectl-guard.md` rule 6c), ADR 0030.

The surfaces under test:

    egress_destinations(base_url, notify_urls, wiki_remote) -> (hosts, notes)
        # rules 4-4e: the only definition of the destination set (pure)
    resolve_hosts(hosts) -> list[str]
        # rule 5: the one impure step; ValueError on an unresolvable host
    render_egress_policy(addresses, *, api_server=None) -> str
        # rules 6-6a: the default-deny NetworkPolicy text (pure)
    EGRESS_POLICY_NAME / EGRESS_DNS_NAMESPACES
        # rule 6: the hard-coded names
    patrol.py --egress-policy
        # rules 7-7e: the offline renderer (stdout = YAML, stderr = diagnostics)
    manifests/egress-networkpolicy.yaml.example + .gitignore + README
        # rules 8-8c: the committed template, the gitignored artifact, the
        # procedure, and the regeneration discipline
    guard_refusal / kubectl_readonly on --server/-s
        # rule 3 + kubectl-guard.md rule 6c

They fail before implementation (AttributeError on the missing symbols,
AssertionError on the missing surfaces) and must pass after. No test needs
outbound network access: resolution is exercised against address literals and
`localhost`, which every host resolves locally (the #77 host-portability rule).

Run: uv run --group dev pytest tests/ -q
"""
import ipaddress
import re
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

import patrol
from support import fake_cli, patrol_free_env


REPO = Path(__file__).resolve().parent.parent
PATROL = REPO / "patrol.py"
MANIFESTS = REPO / "manifests"
TEMPLATE = MANIFESTS / "egress-networkpolicy.yaml.example"
SOURCE = PATROL.read_text(encoding="utf-8")

EGRESS_ENV_KEYS = ("PATROL_BASE_URL", "PATROL_NOTIFY_URLS", "PATROL_WIKI_REMOTE")

# Rule 6c's exact refusal message (kubectl-guard.md).
SERVER_REFUSAL = (
    "REFUSED: endpoint-override flags are off-limits — kubectl must use the "
    "ambient in-cluster API endpoint, never a caller-supplied one.")

# RFC 5737 documentation ranges — the template's placeholder addresses.
DOC_NETS = [ipaddress.ip_network(n) for n in
            ("192.0.2.0/24", "198.51.100.0/24", "203.0.113.0/24")]


# ------------------------------------------------------------------ helpers

def guard(command, *, kubectl="oc", dry=True, verbose=False):
    """One guarded call in dry mode unless stated otherwise."""
    return patrol.kubectl_readonly(command, kubectl=kubectl, dry=dry,
                                  verbose=verbose)


def egress_env(**extra):
    """The real environment minus every `PATROL_*` key and the ambient
    in-pod `KUBERNETES_SERVICE_*` keys, plus `extra` — the ambient values
    are dropped BEFORE `extra` lands, so a test can opt into rule 7c's
    in-pod leg explicitly."""
    env = patrol_free_env()
    env.pop("KUBERNETES_SERVICE_HOST", None)
    env.pop("KUBERNETES_SERVICE_PORT", None)
    env.update(extra)
    return env


def run_egress(*extra, env=None, cwd=None):
    """Run the renderer exactly as a human would — as a subprocess. Output is
    decoded as UTF-8, never the host locale (#77 rule 9b)."""
    r = subprocess.run(
        [sys.executable, str(PATROL), "--egress-policy", *extra],
        capture_output=True, timeout=240, cwd=cwd,
        env=egress_env() if env is None else env)
    return subprocess.CompletedProcess(r.args, r.returncode,
                                       r.stdout.decode("utf-8"),
                                       r.stderr.decode("utf-8", "replace"))


def cidrs(policy):
    """Every ipBlock cidr in the rendered policy."""
    out = []
    for rule in policy["spec"].get("egress", []):
        for peer in rule.get("to", []):
            block = peer.get("ipBlock")
            if block:
                out.append(block["cidr"])
    return out


def namespace_peers(policy):
    """The namespace names selected by namespaceSelector peers."""
    out = []
    for rule in policy["spec"].get("egress", []):
        for peer in rule.get("to", []):
            sel = peer.get("namespaceSelector")
            if sel:
                out.append(sel["matchLabels"]["kubernetes.io/metadata.name"])
    return out


# ------------------------------------------- derivation: the model endpoint (4a)

def test_destinations_model_endpoint_host():
    """Rule 4a: the model endpoint contributes its host — no port, no path."""
    hosts, notes = patrol.egress_destinations(
        "https://api.example.com/v1", [], "")
    assert hosts == ["api.example.com"], hosts
    assert notes == [], notes


def test_destinations_hostless_base_url_notes():
    """Rule 4a: a value with no host contributes nothing and one note naming
    `PATROL_BASE_URL` (the CLI refuses to render without it, rule 7a)."""
    for value in ("", "api.example.com/v1"):
        hosts, notes = patrol.egress_destinations(value, [], "")
        assert hosts == [], f"{value!r} must derive no host"
        assert len(notes) == 1, notes
        assert notes[0].startswith("egress: "), notes[0]
        assert "PATROL_BASE_URL" in notes[0], notes[0]


# ------------------------------------------- derivation: apprise sinks (4b)

@pytest.mark.parametrize("url,expected", [
    ("json://api.example.com/patrol", "api.example.com"),
    ("https://ntfy.sh/patrol-topic", "ntfy.sh"),
    ("json://192.0.2.10:8080/patrol", "192.0.2.10"),
    ("json://localhost/patrol", "localhost"),
    ("matrix://bot:secret@matrix.example.com/room", "matrix.example.com"),
])
def test_destinations_apprise_host_forms(url, expected):
    """Rule 4b: every apprise URL whose host position is host-like (IP
    literal, dotted name, or a non-numeric single label) contributes it."""
    hosts, notes = patrol.egress_destinations("https://api.example.com/v1",
                                              [url], "")
    assert hosts == sorted({"api.example.com", expected}), hosts
    assert notes == [], notes


@pytest.mark.parametrize("url,scheme", [
    ("discord://123456789012345678/abcdef", "discord"),
    ("json://987654321/patrol", "json"),
])
def test_destinations_token_in_netloc_notes(url, scheme):
    """Rule 4b + Constraints: a token-in-netloc webhook URL derives no host —
    the driver resolves the service's own endpoint — so the renderer names it
    on stderr instead of guessing (an all-digits single label is not a host)."""
    hosts, notes = patrol.egress_destinations("https://api.example.com/v1",
                                              [url], "")
    assert hosts == ["api.example.com"], hosts
    assert len(notes) == 1, notes
    assert notes[0].startswith("egress: "), notes[0]
    assert "PATROL_NOTIFY_URLS" in notes[0], notes[0]
    assert scheme in notes[0], notes[0]


# ------------------------------------------- derivation: the wiki remote (4c)

@pytest.mark.parametrize("remote,expected", [
    ("git@github.com:you/cluster-wiki.git", "github.com"),
    ("github.com:you/cluster-wiki.git", "github.com"),
    ("localhost:path/to/wiki.git", "localhost"),
    ("ssh://git@git.example.com:2222/you/cluster-wiki.git", "git.example.com"),
    ("git+ssh://git@git.example.com/you/wiki.git", "git.example.com"),
    ("https://github.com/you/cluster-wiki.git", "github.com"),
    ("git://git.example.com/you/cluster-wiki.git", "git.example.com"),
])
def test_destinations_wiki_remote_forms(remote, expected):
    """Rule 4c: the scp-like form (`[user@]host:path`) and the `scheme://`
    forms both contribute their host — the SSH deploy-key wiring (#39) uses the
    scp-like form, so it cannot be the only supported shape."""
    hosts, notes = patrol.egress_destinations("https://api.example.com/v1",
                                              [], remote)
    assert hosts == sorted({"api.example.com", expected}), hosts
    assert notes == [], notes


# ------------------------------------------- derivation: the set itself (4-4e)

def test_destinations_unset_optionals_are_silent():
    """Rule 4b/4c: unset optional keys contribute nothing *and* no note — a
    patrol with no sinks and no remote is a normal configuration."""
    hosts, notes = patrol.egress_destinations("https://api.example.com/v1",
                                              [], "")
    assert hosts == ["api.example.com"], hosts
    assert notes == [], notes


def test_destinations_sorted_deduped_lowercased():
    """Rule 4e: fixed order, no duplicates, lowercase — a re-render is
    diffable."""
    hosts, _ = patrol.egress_destinations(
        "https://API.Example.com/v1",
        ["json://api.example.com/x", "https://Ntfy.SH/topic"],
        "git@api.example.com:you/wiki.git",
    )
    assert hosts == ["api.example.com", "ntfy.sh"], hosts


def test_destinations_is_pure(monkeypatch):
    """Rule 4: no I/O and **no DNS** in the derivation — resolution is the
    separate step of rule 5, which is what keeps the suite network-free."""
    def explode(*_a, **_kw):
        raise AssertionError("egress_destinations must not resolve anything")

    monkeypatch.setattr("socket.getaddrinfo", explode)
    monkeypatch.setattr("socket.gethostbyname", explode)
    hosts, _ = patrol.egress_destinations(
        "https://api.example.com/v1", ["json://api.example.com/x"], "")
    assert hosts == ["api.example.com"], hosts


# ------------------------------------------------------------- resolution (5)

def test_resolve_hosts_literals_pass_through():
    """Rule 5: an address literal needs no lookup and survives unchanged."""
    assert patrol.resolve_hosts(["127.0.0.1", "::1"]) == ["127.0.0.1", "::1"]


def test_resolve_hosts_dedups_and_sorts():
    """Rule 5: addresses are deduplicated and sorted as text."""
    assert patrol.resolve_hosts(["::1", "127.0.0.1", "127.0.0.1"]) == \
        ["127.0.0.1", "::1"]


def test_resolve_hosts_localhost_without_network():
    """Rule 5: a hostname resolves through the platform resolver — exercised
    against `localhost`, which needs no network on any host."""
    addresses = patrol.resolve_hosts(["localhost"])
    assert addresses, "localhost must resolve"
    parsed = [ipaddress.ip_address(a) for a in addresses]
    assert any(addr.is_loopback for addr in parsed), addresses
    assert addresses == sorted(set(addresses)), addresses


def test_resolve_hosts_unresolvable_raises_named_error():
    """Rule 5: an unresolvable host is an error naming the host — never a
    partial set (an allowlist missing a destination breaks patrols silently).
    `"not a host"` cannot be a DNS name, so this raises without any lookup."""
    with pytest.raises(ValueError) as excinfo:
        patrol.resolve_hosts(["not a host"])
    assert "not a host" in str(excinfo.value), str(excinfo.value)


# -------------------------------------------------------------- rendering (6)

def render(addresses=("203.0.113.10", "192.0.2.7"), **kw):
    policy = yaml.safe_load(patrol.render_egress_policy(list(addresses), **kw))
    assert policy is not None, "the renderer must emit parseable YAML"
    return policy


def test_render_egress_policy_shape():
    """Rules 6 + 2: a namespace-scoped, pod-selected, egress-only default-deny
    policy whose only writable destinations are the given addresses, the
    kube-apiserver, and cluster DNS."""
    policy = render(api_server="172.30.0.1")
    assert policy["apiVersion"] == "networking.k8s.io/v1"
    assert policy["kind"] == "NetworkPolicy"
    assert policy["metadata"]["name"] == patrol.EGRESS_POLICY_NAME
    assert policy["metadata"]["name"] == "ols-patroller-egress"
    assert policy["metadata"]["namespace"] == "ols-patroller"
    spec = policy["spec"]
    assert spec["podSelector"] == {"matchLabels": {"app": "ols-patroller"}}, (
        "the policy must select the deployment's pod label "
        "(in-cluster-deployment.md rule 13)"
    )
    assert spec["policyTypes"] == ["Egress"], spec["policyTypes"]
    assert "ingress" not in spec, (
        "the policy is egress-only — ingress is untouched (rule 6)"
    )
    assert set(cidrs(policy)) == {
        "203.0.113.10/32", "192.0.2.7/32", "172.30.0.1/32",
    }, cidrs(policy)


def test_render_dns_rule_allows_cluster_dns_only():
    """Rule 6: cluster DNS is allowed through a namespace selector on port 53
    (UDP + TCP) for both the OpenShift and the upstream namespace — no other
    port, no IP guessing."""
    policy = render()
    assert tuple(patrol.EGRESS_DNS_NAMESPACES) == ("openshift-dns", "kube-system")
    assert namespace_peers(policy) == ["openshift-dns", "kube-system"], (
        "the peers follow EGRESS_DNS_NAMESPACES order (rule 6): "
        f"{namespace_peers(policy)}"
    )
    dns_rules = [r for r in policy["spec"]["egress"]
                 if any("namespaceSelector" in p for p in r.get("to", []))]
    assert len(dns_rules) == 1, dns_rules
    ports = {(p["protocol"], p["port"]) for p in dns_rules[0]["ports"]}
    assert ports == {("UDP", 53), ("TCP", 53)}, ports


def test_render_destination_peers_carry_no_ports():
    """Rule 6 + Constraints: destination and apiserver peers are
    destination-keyed — no `ports` (apprise's per-scheme transport ports are
    not in the URL, so a port restriction would silently break sinks)."""
    policy = render(api_server="172.30.0.1")
    for rule in policy["spec"]["egress"]:
        if any("ipBlock" in p for p in rule.get("to", [])):
            assert "ports" not in rule, rule


def test_render_ipv6_cidr():
    """Rule 6: an IPv6 address renders as a /128."""
    policy = render(addresses=("::1",))
    assert "::1/128" in cidrs(policy), cidrs(policy)


def test_render_without_api_server_omits_the_peer():
    """Rule 6a/7c: `api_server=None` renders the policy without that peer —
    never an invented address — and the policy is still default-deny."""
    policy = render(api_server=None)
    assert set(cidrs(policy)) == {"203.0.113.10/32", "192.0.2.7/32"}, cidrs(policy)
    assert policy["spec"]["policyTypes"] == ["Egress"]
    assert set(namespace_peers(policy)) == {"openshift-dns", "kube-system"}


def test_render_empty_addresses_emits_no_destination_rule():
    """Rule 6: no addresses, no destination rule — an empty peer list is
    ambiguous, and the renderer never emits an implicitly permissive entry."""
    policy = render(addresses=(), api_server=None)
    assert cidrs(policy) == [], cidrs(policy)
    assert set(namespace_peers(policy)) == {"openshift-dns", "kube-system"}, (
        "the DNS allowance is unconditional"
    )


def test_render_is_byte_stable():
    """Rule 6: sorted addresses inside the rule, LF endings, one trailing
    newline — the same configuration renders identical bytes, so an operator
    can diff a re-render."""
    a = patrol.render_egress_policy(["203.0.113.10", "192.0.2.7"],
                                    api_server="172.30.0.1")
    b = patrol.render_egress_policy(["192.0.2.7", "203.0.113.10"],
                                    api_server="172.30.0.1")
    assert a == b
    assert a.endswith("\n") and not a.endswith("\n\n"), repr(a[-4:])
    assert "\r" not in a, "the artifact is LF-only"


# ------------------------------------------------------- the renderer (rules 7)

def test_cli_renders_the_policy():
    """Rules 7–7b: `--egress-policy` prints the policy for the process's own
    configuration; stdout is parseable YAML and nothing else."""
    env = egress_env(PATROL_BASE_URL="http://localhost:8080/v1",
                     PATROL_NOTIFY_URLS="json://127.0.0.1/patrol")
    r = run_egress(env=env)
    assert r.returncode == 0, r.stderr[-600:]
    policy = yaml.safe_load(r.stdout)
    assert policy["kind"] == "NetworkPolicy"
    assert policy["metadata"]["name"] == "ols-patroller-egress"
    blocks = cidrs(policy)
    assert any(c.endswith("/32") or c.endswith("/128") for c in blocks), blocks
    assert "127.0.0.1/32" in blocks, (
        f"the literal sink address must be in the allowlist, got {blocks}"
    )


def test_cli_requires_base_url():
    """Rules 7a/7d: `PATROL_BASE_URL` is the renderer's one required input, and
    a value no host can be derived from counts as missing (rule 4a) — each run
    exits non-zero, names it, and prints no policy."""
    for value in (None, "", "api.example.com/v1"):
        env = egress_env() if value is None else egress_env(PATROL_BASE_URL=value)
        r = run_egress(env=env)
        assert r.returncode != 0, f"{value!r} must be refused"
        assert r.stdout.strip() == "", "no policy may be printed on this failure"
        assert "PATROL_BASE_URL" in r.stderr, r.stderr[-400:]


def test_cli_unresolvable_host_fails_clean():
    """Rules 5 + 7d: an unresolvable destination is a named failure with empty
    stdout — never a partial allowlist."""
    r = run_egress(env=egress_env(PATROL_BASE_URL="http://bad host/v1"))
    assert r.returncode != 0
    assert r.stdout.strip() == "", r.stdout[:400]
    assert "bad host" in r.stderr, r.stderr[-400:]


def test_cli_names_the_missing_apiserver_peer():
    """Rule 7c: off-cluster (`KUBERNETES_SERVICE_HOST` unset) the policy has no
    apiserver peer and stderr names the omission and the stanza to add."""
    env = egress_env(PATROL_BASE_URL="http://127.0.0.1:8080/v1")
    r = run_egress(env=env)
    assert r.returncode == 0, r.stderr[-600:]
    assert "KUBERNETES_SERVICE_HOST" in r.stderr, r.stderr[-600:]
    assert "ipBlock" in r.stderr, r.stderr[-600:]
    assert "192.0.2.99/32" not in cidrs(yaml.safe_load(r.stdout)), (
        "no apiserver peer may be invented when its address is unknown"
    )


def test_cli_renders_the_apiserver_peer_in_pod():
    """Rule 7c: in-pod, the apiserver address comes from the environment and
    lands in the policy."""
    env = egress_env(PATROL_BASE_URL="http://127.0.0.1:8080/v1",
                     KUBERNETES_SERVICE_HOST="192.0.2.99")
    r = run_egress(env=env)
    assert r.returncode == 0, r.stderr[-600:]
    assert "192.0.2.99/32" in cidrs(yaml.safe_load(r.stdout)), r.stdout[:600]
    assert "KUBERNETES_SERVICE_HOST" not in r.stderr, (
        "no omission note when the apiserver address is known"
    )


def test_cli_ignores_the_model_keys():
    """Rule 7a: the renderer reads the egress keys only — it must run with no
    `PATROL_API_KEY`/`PATROL_MODEL` (the patrol itself would refuse)."""
    env = egress_env(PATROL_BASE_URL="http://127.0.0.1:8080/v1")
    assert "PATROL_API_KEY" not in env and "PATROL_MODEL" not in env
    r = run_egress(env=env)
    assert r.returncode == 0, r.stderr[-600:]


def test_cli_writes_nothing(tmp_path):
    """Rule 7: an offline mode — it initializes no wiki repo and creates no
    directory (the `--wiki-stats` slot, wiki-memory.md rule 23)."""
    wiki = tmp_path / "wiki" / "cluster-wiki.md"
    r = run_egress("--wiki", str(wiki),
                   env=egress_env(PATROL_BASE_URL="http://127.0.0.1:8080/v1"))
    assert r.returncode == 0, r.stderr[-600:]
    assert not wiki.parent.exists(), (
        "the renderer must not initialize the wiki repo or create directories"
    )


@pytest.mark.parametrize("conflict", ["--loop", "--wiki-stats"])
def test_cli_mode_conflicts(conflict):
    """Rule 7e: `--egress-policy` cannot be combined with the other modes —
    an argparse usage error naming both flags."""
    r = run_egress(conflict,
                   env=egress_env(PATROL_BASE_URL="http://127.0.0.1:8080/v1"))
    assert r.returncode == 2, (r.returncode, r.stderr[-400:])
    assert "cannot be combined" in r.stderr, r.stderr[-400:]
    assert "--egress-policy" in r.stderr and conflict in r.stderr, r.stderr[-400:]


# ------------------------------------------------- the artifact & procedure (8)

def test_committed_template_shape():
    """Rule 8: the committed template is a complete policy of rule 6's shape
    with documentation placeholder addresses, carrying the procedure."""
    assert TEMPLATE.exists(), (
        "manifests/egress-networkpolicy.yaml.example must be committed as the "
        "policy template (egress-control.md rule 8, #34)"
    )
    raw = TEMPLATE.read_text(encoding="utf-8")
    policy = yaml.safe_load(raw)
    assert policy["apiVersion"] == "networking.k8s.io/v1"
    assert policy["kind"] == "NetworkPolicy"
    assert policy["metadata"]["name"] == "ols-patroller-egress"
    assert policy["metadata"]["namespace"] == "ols-patroller"
    spec = policy["spec"]
    assert spec["podSelector"] == {"matchLabels": {"app": "ols-patroller"}}
    assert spec["policyTypes"] == ["Egress"]
    assert set(namespace_peers(policy)) == {"openshift-dns", "kube-system"}
    blocks = [ipaddress.ip_network(c) for c in cidrs(policy)]
    assert blocks, "the template must show the ipBlock peers"
    for net in blocks:
        assert any(net.subnet_of(doc) for doc in DOC_NETS), (
            f"{net} is not an RFC 5737 documentation address — the template "
            f"carries placeholders, never a real endpoint (rule 8)"
        )
    for needle in ("--egress-policy", "oc apply -f", *EGRESS_ENV_KEYS):
        assert needle in raw, (
            f"the template's comments must document {needle!r} (rule 8)"
        )
    assert re.search("regenerat", raw, re.I), (
        "the template's comments must document the regeneration rule "
        "(rule 8c)"
    )


def test_operator_copy_is_gitignored():
    """Rule 8: the operator's rendered artifact is gitignored and untracked,
    like the other opt-in wiring copies."""
    gitignore = (REPO / ".gitignore").read_text(encoding="utf-8")
    assert re.search(r"^manifests/egress-networkpolicy\.yaml\s*$", gitignore, re.M), (
        ".gitignore must ignore manifests/egress-networkpolicy.yaml "
        "(egress-control.md rule 8, #34)"
    )
    tracked = subprocess.run(
        ["git", "ls-files", "manifests/"], cwd=REPO,
        capture_output=True, text=True, check=True,
    ).stdout.split()
    assert "manifests/egress-networkpolicy.yaml" not in tracked


def test_base_apply_does_not_ship_the_policy():
    """Rule 8a: the allowlist is opt-in — the committed kustomization still
    lists exactly the six resources and names neither the policy nor its
    artifact."""
    k = yaml.safe_load((MANIFESTS / "kustomization.yaml").read_text(encoding="utf-8"))
    assert set(k["resources"]) == {
        "namespace.yaml", "serviceaccount.yaml", "clusterrolebinding.yaml",
        "pvc.yaml", "secret.yaml", "deployment.yaml",
    }, k["resources"]
    raw = (MANIFESTS / "kustomization.yaml").read_text(encoding="utf-8")
    assert "egress" not in raw.lower(), (
        "the committed kustomization must not reference the egress policy "
        "(rule 8a) — it is applied out-of-band"
    )


def test_readme_documents_the_egress_procedure():
    """Rules 8b/8c: the README carries the procedure end-to-end and
    self-sufficiently — render, apply, regenerate after any egress-config
    change, the apprise-scheme limitation, and how to prove it bites."""
    readme = (REPO / "README.md").read_text(encoding="utf-8")
    m = re.search(r"^### Restrict egress \(optional\)\s*$\n(.*?)(?=^#{2,3} |\Z)",
                  readme, re.M | re.S)
    assert m, (
        "README.md must carry a '### Restrict egress (optional)' subsection "
        "(egress-control.md rule 8b, #34)"
    )
    section = m.group(1)
    for needle in ("--egress-policy", "oc apply -f", *EGRESS_ENV_KEYS):
        assert needle in section, (
            f"the README egress procedure must mention {needle!r} (rule 8b)"
        )
    assert re.search("regenerat", section, re.I), (
        "the README procedure must state the regeneration rule (rule 8b/8c)"
    )
    assert "apprise" in section.lower(), (
        "the README procedure must state the apprise-scheme limitation "
        "(rule 8b/4b)"
    )
    assert "bites" in section, (
        "the README procedure must document how to prove the allowlist "
        "bites (rule 8b/10)"
    )
    assert ".ai/spec" not in section, (
        "the README procedure must be self-sufficient (rule 8b, the rule-16d "
        "standard)"
    )


def test_process_never_reads_the_artifact():
    """Rule 9a: the artifact is operator surface — `patrol.py` never reads the
    rendered file (it renders text; it does not consult a policy on disk)."""
    assert "egress-networkpolicy" not in SOURCE, (
        "patrol.py must not reference the generated policy artifact "
        "(egress-control.md rule 9a) — enabling the allowlist is operator action"
    )


# ------------------------------------- in-agent egress closure (rule 3, guard 6c)

SERVER_COMMANDS = [
    "get pods --server=https://evil.example",
    "get pods --server https://evil.example",
    "get nodes --server",
    "get pods --SERVER=https://evil.example",
    "get pods -s=https://evil.example",
    "get pods -s https://evil.example",
    "get pods -shttps://evil.example",
    "get pods -As https://evil.example",
    "logs -s https://evil.example pod/demo-app-abc",
    "logs -fs https://evil.example pod/demo-app-abc",
    "logs -fAs https://evil.example pod/demo-app-abc",
    "logs -fs=https://evil.example pod/demo-app-abc",
    # #83: `p` is boolean `--previous` on `logs`, so the walk runs past it and
    # the `s` after it is still the endpoint shorthand
    "logs -ps https://evil.example pod/demo-app-abc",
    "logs -fps https://evil.example pod/demo-app-abc",
    "logs -pfs=https://evil.example pod/demo-app-abc",
    "logs -pshttps://evil.example pod/demo-app-abc",
]


@pytest.mark.parametrize("command", SERVER_COMMANDS)
def test_endpoint_override_refused(command):
    """Guard rule 6c (#34): a caller-supplied API endpoint is an outbound
    destination the egress allowlist does not cover and the model must never
    choose — every spelling is refused with the exact message."""
    out = guard(command)
    assert out.startswith("REFUSED:"), f"{command!r} must be refused, got {out!r}"
    assert out == SERVER_REFUSAL, out


@pytest.mark.parametrize("command", [
    "get pods",
    "get pods -A",
    "describe node worker-1",
    "logs -f deploy/ols-patroller",
    "get pods -n ols-patroller -o wide",
    "get pods -lapp=frontend",
    "get pods -o jsonpath={.items[*].metadata.name}",
    "get pods -o custom-columns=NAME:.metadata.name",
    "get pods --server-print=false",
    "get pods --sort-by=.metadata.name",
])
def test_plain_reads_still_allowed(command):
    """Guard rule 6c scope: the refusal is the endpoint flag, not the word
    `server` — `--server-print` (a printing hint) and ordinary reads stay
    accepted, and the rule-6b shorthand behavior is unchanged."""
    out = guard(command)
    assert not out.startswith("REFUSED:"), (
        f"{command!r} is a legal read, got {out!r}"
    )


def test_endpoint_override_preempts_execution(tmp_path):
    """Guard rule 6c: the refusal fires before any subprocess runs."""
    cli = fake_cli(tmp_path, "print('SHOULD-NOT-RUN')")
    out = guard("get pods --server=https://evil.example", kubectl=cli, dry=False)
    assert out == SERVER_REFUSAL
    assert "SHOULD-NOT-RUN" not in out


def test_endpoint_override_preempts_dry_fixtures(monkeypatch):
    """Guard rule 6c + rule 13: in dry mode the refusal fires before fixture
    lookup."""
    monkeypatch.setattr(patrol, "FIXTURES", [("server", "SHOULD-NOT-SERVE")])
    out = guard("get pods --server=https://evil.example")
    assert out == SERVER_REFUSAL
    assert "SHOULD-NOT-SERVE" not in out


def test_endpoint_override_check_runs_after_the_verb_whitelist():
    """Guard rule 6c ordering: a mutation verb keeps its verb refusal."""
    out = guard("apply --server=https://evil.example -f /tmp/x.yaml")
    assert out.startswith("REFUSED:")
    assert "not a read-only verb" in out
