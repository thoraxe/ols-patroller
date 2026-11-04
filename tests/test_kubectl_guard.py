"""kubectl guard unit tests — the refusal & execution contract (#4).

Direct calls to `kubectl_readonly` — no engine, no model. Dry-cluster mode
answers allowed commands from fixtures; real-mode execution semantics (missing
binary, timeout, stderr, empty output) are exercised with a throwaway CLI
script. The raw-path (`--raw`) refusal is specced for #11 (rule 6a): its tests
land with #11's spec PR and fail until #11's implementation makes them pass.

Contract: .ai/spec/what/kubectl-guard.md rules 1-13 (safety-model.md rule 6a).
The truncation-marker recovery hint is specced for #20 (rule 11): its tests
land with #20's spec PR and fail until #20's implementation makes them pass.

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


# ------------------------------------------------------------- secret access ban (rules 5-6)

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
    assert "Secret access is off-limits" in out


@pytest.mark.parametrize("command", [
    "get secrets",                # names/metadata
    "get secret demo-secret",     # no output format
    "get secret demo-secret -o name",
    "describe secret demo-secret",
])
def test_secret_names_and_metadata_refused(command):
    """Rule 5 (#33 flip): names/metadata are refused too — Secret access is
    banned in full, not only value reads (was allowed before #33)."""
    out = guard(command)
    assert out.startswith("REFUSED:"), (
        f"{command!r} accesses a Secret and must be refused, got {out!r}")
    assert "Secret access is off-limits" in out


def test_overbroad_secret_match_refuses_jsonpath_on_non_secret():
    """Rule 6: the substring match is deliberately over-broad — jsonpath
    extraction of a secret-ish field from a non-Secret resource is refused."""
    out = guard("get deployment demo -o "
                "jsonpath={.spec.template.spec.volumes[0].secret.secretName}")
    assert out.startswith("REFUSED:")
    assert "Secret access is off-limits" in out


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
    """Rule 11 (#10 flip): real-path output fed back to the model is capped
    head+tail with an elision marker naming the true total and the elided
    middle — was head-only with `[truncated, N chars total]` before #10."""
    long_text = "x" * (patrol.OUT_CAP + 5000)
    cli = _fake_cli(tmp_path, f"printf '%s\\n' '{long_text}'")
    out = guard("get nodes", kubectl=cli, dry=False)
    n = len(long_text) + 1  # printf adds the newline
    m = n - patrol.OUT_HEAD - patrol.OUT_TAIL
    assert f"[truncated, {n} chars total: middle {m} chars elided, head+tail kept]" in out


def test_output_cap_truncation_hint(tmp_path):
    """Rule 11 (#20, #10 flip): truncated output carries a recovery hint
    naming the narrowing patterns (--field-selector, -o jsonpath) after the
    kept tail — the model recovers systematically instead of retrying the
    same dump."""
    long_text = "x" * (patrol.OUT_CAP + 5000)
    cli = _fake_cli(tmp_path, f"printf '%s\\n' '{long_text}'")
    out = guard("get nodes", kubectl=cli, dry=False)
    # the hint follows the kept tail (asserted on presence first so a missing
    # hint fails as a missing-behavior assertion, not a lookup error)
    assert "field-selector" in out
    assert out.index("[truncated,") < out.index("field-selector")
    assert "jsonpath" in out
    assert "custom-columns" in out
    assert "single-object" in out


def test_no_hint_when_output_fits(tmp_path):
    """Rule 11 (#20): the hint rides only truncated output — output that fits
    carries neither marker nor hint."""
    cli = _fake_cli(tmp_path, "echo all good")
    out = guard("get nodes", kubectl=cli, dry=False)
    assert "truncated" not in out
    assert "field-selector" not in out and "jsonpath" not in out


def test_dry_mode_cap_stays_hint_free(monkeypatch):
    """Rule 11 (#20) with rule 13: dry-mode fixture capping is silent — no
    marker, no hint (fixture text is demo data, not cluster output)."""
    monkeypatch.setattr(patrol, "FIXTURES", [("get nodes", "y" * (patrol.OUT_CAP + 100))])
    out = guard("get nodes")
    assert len(out) == patrol.OUT_CAP
    assert "truncated" not in out
    assert "field-selector" not in out and "jsonpath" not in out


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
    out = guard("get pvcs")
    assert "no fixture" in out
    assert "get nodes" in out          # available fixtures are listed
    assert "do not retry" in out or "not retry" in out


# ------------------------------------------------------- raw-path refusal (rule 6a)

RAW_COMMANDS = [
    "get --raw /api/v1/nodes/master-0/proxy/stats/summary",  # the patrol-6 burner
    "get --raw /apis/metrics.k8s.io/v1beta1",
    "get --raw=/api/v1/nodes/master-0/proxy/stats/summary",  # = form
    "describe --raw /api/v1/nodes/master-0/proxy/stats/summary",
    "top --raw /api/v1/nodes",                               # any whitelisted verb
    "get --RAW /api/v1/nodes/master-0/proxy/stats/summary",  # case-insensitive
]


@pytest.mark.parametrize("command", RAW_COMMANDS)
def test_raw_refused_across_flag_forms(command):
    """Rule 6a: any command containing --raw is refused, whatever the flag
    form, verb, or case. These run in dry mode (the guard default), pinning the
    refusal there; test_raw_refusal_preempts_execution pins real mode."""
    out = guard(command)
    assert out.startswith("REFUSED:"), f"{command!r} must be refused, got {out!r}"
    assert "--raw" in out.lower()
    assert "truncat" in out.lower()  # says raw dumps truncate at the cap
    assert "typed" in out.lower()  # steers recovery to typed read commands


def test_raw_check_runs_after_the_verb_whitelist():
    """Rule 6a: the raw check sits after the verb whitelist — a mutation verb
    with --raw keeps its verb refusal (the allowed-verb list is the recovery
    info the model needs there)."""
    out = guard("apply --raw /api/v1/nodes/master-0/proxy/stats/summary")
    assert out.startswith("REFUSED:")
    assert "not a read-only verb" in out


def test_secret_and_raw_double_match_still_refused():
    """Rule 6a: a command matching both the Secret-access ban and --raw is
    refused — either refusal message is conformant."""
    out = guard("get secret demo-secret --raw -o yaml")
    assert out.startswith("REFUSED:")


@pytest.mark.parametrize("command", [
    "get pods -A",
    "describe node raw-worker-1",  # bare 'raw' without the flag
    "get events -A",
])
def test_bare_raw_still_allowed(command):
    """Rule 6a: the match is on '--raw', not the bare word 'raw' — resource
    names and ordinary commands stay allowed."""
    out = guard(command)
    assert not out.startswith("REFUSED:"), (
        f"{command!r} contains no --raw flag, got {out!r}")


def test_raw_refusal_preempts_execution(tmp_path):
    """Rule 6a: the refusal fires before any subprocess runs — the CLI is
    never invoked for a --raw command."""
    cli = _fake_cli(tmp_path, "echo SHOULD-NOT-RUN")
    out = guard("get --raw /api/v1/nodes/master-0/proxy/stats/summary",
                kubectl=cli, dry=False)
    assert out.startswith("REFUSED:")
    assert "SHOULD-NOT-RUN" not in out


def test_raw_refusal_preempts_dry_fixtures(monkeypatch):
    """Rule 6a: in dry mode the refusal fires before fixture lookup — even a
    fixture that would match the command never answers it."""
    monkeypatch.setattr(patrol, "FIXTURES", [("--raw", "SHOULD-NOT-SERVE")])
    out = guard("get --raw /api/v1/nodes/master-0/proxy/stats/summary")
    assert out.startswith("REFUSED:")
    assert "SHOULD-NOT-SERVE" not in out
