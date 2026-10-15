"""kubectl guard unit tests — the refusal & execution contract (#4).

Direct calls to `kubectl_readonly` — no engine, no model. Dry-cluster mode
answers allowed commands from fixtures; real-mode execution semantics (missing
binary, timeout, stderr, empty output) are exercised with a throwaway CLI
script. The `--raw` refusal is specced for #11 and carried here as a strict
expected-failure tripwire: it must keep failing until #11's implementation
lands, and start failing loudly (XPASS) if the refusal appears without the
spec flip.

Contract: .ai/spec/what/kubectl-guard.md rules 1-13 (safety-model.md rule 6a).

Run: uv run --group dev pytest tests/ -q
"""
import stat
from types import SimpleNamespace

import pytest

import patrol


def guard(command, *, kubectl="oc", dry=True, verbose=False):
    """One guarded call in dry mode unless stated otherwise."""
    return patrol.kubectl_readonly(command, kubectl=kubectl, dry=dry, verbose=verbose)


# ------------------------------------------------------------ verb whitelist (rules 2-3)

MUTATION_VERBS = ["apply", "delete", "edit", "patch", "replace", "create",
                  "scale", "rollout", "drain", "cordon", "uncordon", "taint",
                  "label", "annotate", "set", "attach", "proxy", "port-forward"]


@pytest.mark.parametrize("verb", MUTATION_VERBS)
def test_mutation_verbs_refused(verb):
    out = guard(f"{verb} pod demo-6b5c-xyz -n demo-app")
    assert out.startswith("REFUSED:"), f"{verb!r} must be refused, got {out!r}"
    assert "not a read-only verb" in out
    # the refusal lists the allowed verbs so the model can recover
    for allowed in ("get", "describe", "logs", "top"):
        assert allowed in out


def test_non_whitelisted_verb_refused():
    assert guard("adm policy who-can delete pods").startswith("REFUSED:")


def test_every_whitelisted_verb_passes_the_guard():
    verbs = ["get pods -A", "describe node worker-1", "logs pod/demo-6b5c-xyz",
             "top nodes", "version", "api-resources", "api-versions",
             "explain pod", "status", "cluster-info", "whoami"]
    for command in verbs:
        out = guard(command)
        assert not out.startswith("REFUSED:"), (
            f"{command!r} is a whitelisted read-only command, got {out!r}")


# ------------------------------------------------------- secret-value protection (rules 5-6)

@pytest.mark.parametrize("command", [
    "get secret demo-secret -o yaml",
    "get secret demo-secret -o json",
    "get secret demo-secret -o jsonpath={.data.token}",
    "get secret demo-secret -o go-template={{.data}}",
    "get SECRET demo-secret -o yaml",          # case-insensitive
    "get secrets -n demo-app -o yaml",
])
def test_secret_value_reads_refused(command):
    out = guard(command)
    assert out.startswith("REFUSED:"), f"{command!r} must be refused, got {out!r}"
    assert "Secret values" in out


@pytest.mark.parametrize("command", [
    "get secrets",                # names/metadata only
    "get secret demo-secret",     # no output format -> no values
    "describe secret demo-secret",
])
def test_secret_names_and_metadata_allowed(command):
    out = guard(command)
    assert not out.startswith("REFUSED:"), (
        f"{command!r} reads names/metadata only, got {out!r}")


def test_overbroad_secret_match_refuses_jsonpath_on_non_secret():
    """Rule 6: the substring match is deliberately over-broad — jsonpath
    extraction of a secret-ish field from a non-Secret resource is refused."""
    out = guard("get deployment demo -o "
                "jsonpath={.spec.template.spec.volumes[0].secret.secretName}")
    assert out.startswith("REFUSED:")
    assert "Secret values" in out


# ------------------------------------------------------------- parse failures (rule 4)

@pytest.mark.parametrize("command", ["get 'pod", 'get "pod', "  "])
def test_unparseable_command_refused(command):
    assert guard(command).startswith("REFUSED:")


def test_empty_command_refused():
    assert guard("").startswith("REFUSED:")


# --------------------------------------------------------- execution semantics (rules 7-11)

def test_missing_binary_refused_with_hint():
    """Rule 9: a missing CLI is a REFUSED with a --dry-cluster hint, not a crash."""
    out = guard("get nodes", kubectl="/no/such/cli-on-this-path", dry=False)
    assert out.startswith("REFUSED:")
    assert "not found on PATH" in out
    assert "--dry-cluster" in out


def _fake_cli(tmp_path, body):
    """A throwaway executable standing in for oc/kubectl in real mode."""
    script = tmp_path / "fake-cli"
    script.write_text(f"#!/bin/sh\n{body}\n")
    script.chmod(script.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    return str(script)


def test_timeout_returns_error_not_exception(tmp_path, monkeypatch):
    """Rule 8: a timed-out command returns an ERROR message."""
    cli = _fake_cli(tmp_path, "sleep 30")
    monkeypatch.setattr(patrol, "TOOL_TIMEOUT", 1)
    out = guard("get nodes", kubectl=cli, dry=False)
    assert out.startswith("ERROR:")
    assert "timed out" in out
    assert "1s" in out


def test_stderr_appended_under_header(tmp_path):
    """Rule 10: stderr lands in the output under a [stderr] header."""
    cli = _fake_cli(tmp_path, "echo out-line; echo err-line >&2")
    out = guard("get nodes", kubectl=cli, dry=False)
    assert "out-line" in out
    assert "[stderr]" in out
    assert "err-line" in out


def test_empty_output_marker(tmp_path):
    """Rule 10: empty output becomes (no output)."""
    cli = _fake_cli(tmp_path, "true")
    out = guard("get nodes", kubectl=cli, dry=False)
    assert out == "(no output)"


def test_output_cap_truncation_marker(tmp_path):
    """Rule 11: real-path output fed back to the model is capped at OUT_CAP
    with a truncation marker naming the true length."""
    long_text = "x" * (patrol.OUT_CAP + 5000)
    cli = _fake_cli(tmp_path, f"printf '%s\\n' '{long_text}'")
    out = guard("get nodes", kubectl=cli, dry=False)
    assert len(out) < patrol.OUT_CAP + 100
    assert f"[truncated, {len(long_text) + 1} chars total]" in out


def test_dry_mode_fixture_cap_is_silent(monkeypatch):
    """Rule 13: dry-mode fixture text is capped at OUT_CAP (no marker — the
    fixture table is demo data, not cluster output)."""
    monkeypatch.setattr(patrol, "FIXTURES", [("get nodes", "y" * (patrol.OUT_CAP + 100))])
    out = guard("get nodes")
    assert len(out) == patrol.OUT_CAP
    assert "truncated" not in out


# ------------------------------------------------------------- CLI resolution (rule 12)

def test_cli_resolution_env_override(monkeypatch):
    monkeypatch.setattr(patrol, "KUBECTL", "/opt/special/oc")
    assert patrol.resolve_kubectl() == "/opt/special/oc"


def test_cli_resolution_fallback_chain(monkeypatch):
    def fake_run(cmd, **kwargs):
        assert cmd[:2] == ["bash", "-lc"]
        probe = cmd[2]
        available = {"oc": oc_ok, "kubectl": kubectl_ok}[probe.split()[-1]]
        return SimpleNamespace(returncode=0 if available else 1)

    for oc_ok, kubectl_ok, expected in [(True, True, "oc"),
                                        (False, True, "kubectl"),
                                        (False, False, "oc")]:
        monkeypatch.setattr(patrol, "KUBECTL", "")
        monkeypatch.setattr(patrol.subprocess, "run", fake_run)
        assert patrol.resolve_kubectl() == expected, (
            f"oc={oc_ok} kubectl={kubectl_ok} should resolve {expected!r}")


# ------------------------------------------------------------- dry-cluster (rule 13)

def test_dry_mode_fixture_substitution_first_match():
    out = guard("version")
    assert "Client Version" in out and "Server Version" in out


def test_dry_mode_no_fixture_message_lists_available():
    out = guard("get deployments -A")
    assert "no fixture" in out
    assert "get nodes" in out          # available fixtures are listed
    assert "do not retry" in out or "not retry" in out


# ------------------------------------------------------------- planned: #11 (--raw)

@pytest.mark.xfail(strict=True, reason="--raw refusal is specced for #11, not yet implemented")
def test_raw_refusal_planned_11():
    """[PLANNED: #11] --raw proxy/stats endpoints must be refused. Strict xfail:
    fails today (the guard does not refuse --raw yet); once #11 lands, the
    xfail marker is removed by #11's spec PR — an XPASS here means the refusal
    appeared without its spec cycle."""
    out = guard("get --raw /apis/metrics.k8s.io/v1beta1")
    assert out.startswith("REFUSED:"), (
        "--raw must be refused once #11 lands; until then this test is an "
        "expected failure"
    )
