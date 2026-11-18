"""Remote cluster target — contract tests for #122.

Contract: `.ai/spec/what/kubectl-guard.md` rules 12a-12e (ADR 0041).

They fail before implementation (missing symbols) and must pass after.

Run: uv run --group dev pytest tests/ -q
"""
import os
import stat
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

import patrol
from support import fake_cli, patrol_free_env


REPO = Path(__file__).resolve().parent.parent
PATROL = REPO / "patrol.py"


# ------------------------------------------------------------------ config matrix

def test_config_matrix_accepts_the_ambient_target():
    assert patrol.api_config_errors("", "") == []


@pytest.mark.parametrize("server,token,needle", [
    ("https://api.example:6443", "", "PATROL_API_TOKEN"),
    ("", "token", "PATROL_API_SERVER"),
])
def test_config_matrix_requires_the_pair(server, token, needle):
    errors = patrol.api_config_errors(server, token)
    assert errors, "a partial target is a config error"
    assert any(needle in e for e in errors), errors


def test_config_matrix_rejects_a_non_url_server():
    for value in ("not a url", "https://", "api.example:6443"):
        errors = patrol.api_config_errors(value, "token")
        assert any("PATROL_API_SERVER" in e for e in errors), (value, errors)


# ------------------------------------------------------------------ kubeconfig

def test_build_kubeconfig_shape():
    data = yaml.safe_load(
        patrol.build_kubeconfig("https://api.example:6443", "secret-token"))
    assert data["apiVersion"] == "v1"
    assert data["kind"] == "Config"
    assert len(data["clusters"]) == 1
    assert len(data["users"]) == 1
    assert len(data["contexts"]) == 1
    assert data["clusters"][0]["cluster"]["server"] == "https://api.example:6443"
    assert data["users"][0]["user"]["token"] == "secret-token"
    context = data["contexts"][0]["context"]
    assert context["cluster"] == data["clusters"][0]["name"]
    assert context["user"] == data["users"][0]["name"]
    assert data["current-context"] == data["contexts"][0]["name"]


def test_write_kubeconfig_is_private_and_removable():
    path = patrol.write_kubeconfig("https://api.example:6443", "secret-token")
    try:
        assert path.is_file()
        assert "secret-token" in path.read_text(encoding="utf-8")
        if os.name != "nt":
            assert stat.S_IMODE(path.stat().st_mode) == 0o600
    finally:
        patrol.remove_kubeconfig(path)
    assert not path.exists()


def test_configure_target_returns_none_for_the_ambient_case():
    assert patrol.configure_kubectl_target("", "") is None


def test_configure_target_writes_a_private_kubeconfig():
    path = patrol.configure_kubectl_target(
        "https://api.example:6443", "secret-token")
    try:
        assert path.is_file()
        assert "secret-token" in path.read_text(encoding="utf-8")
    finally:
        patrol.remove_kubeconfig(path)


# ------------------------------------------------------------------ kubectl wiring

def test_kubectl_readonly_uses_the_private_kubeconfig(tmp_path):
    out = tmp_path / "capture.txt"
    cli = fake_cli(tmp_path, f'''
import os, pathlib, sys
kube = pathlib.Path(os.environ["KUBECONFIG"])
pathlib.Path({str(out)!r}).write_text(
    "ARGS=" + repr(sys.argv[1:]) + "\n" + kube.read_text(encoding="utf-8"),
    encoding="utf-8", newline="\n")
print("ok")
''')
    kube = patrol.write_kubeconfig("https://api.example:6443", "secret-token")
    try:
        result = patrol.kubectl_readonly("get pods", kubectl=cli, dry=False,
                                         verbose=False, kubeconfig=str(kube))
    finally:
        patrol.remove_kubeconfig(kube)
    assert "ok" in result
    captured = out.read_text(encoding="utf-8")
    args, kube_text = captured.split("\n", 1)
    assert "secret-token" not in args
    assert "secret-token" in kube_text
    assert "https://api.example:6443" in kube_text


def test_make_kubectl_tool_passes_the_kubeconfig(tmp_path):
    out = tmp_path / "capture.txt"
    cli = fake_cli(tmp_path, f'''
import os, pathlib, sys
pathlib.Path({str(out)!r}).write_text(
    "ARGS=" + repr(sys.argv[1:]) + "\n" + os.environ["KUBECONFIG"],
    encoding="utf-8", newline="\n")
print("ok")
''')
    kube = patrol.write_kubeconfig("https://api.example:6443", "secret-token")
    try:
        tool = patrol.make_kubectl_tool(kubectl=cli, dry=False, verbose=False,
                                        kubeconfig=str(kube))
        assert "ok" in tool.invoke("get pods")
    finally:
        patrol.remove_kubeconfig(kube)
    captured = out.read_text(encoding="utf-8")
    assert "secret-token" not in captured.splitlines()[0]
    assert captured.splitlines()[1] == str(kube)


@pytest.mark.skipif(os.name == "nt",
                    reason="the guard runs [cli] without a shell, so a .cmd launcher does not execute on Windows; the in-process test covers Windows")
def test_subprocess_uses_the_configured_target_before_the_model(tmp_path):
    out = tmp_path / "capture.txt"
    cli = fake_cli(tmp_path, f'''
import os, pathlib, sys
kube = pathlib.Path(os.environ["KUBECONFIG"])
pathlib.Path({str(out)!r}).write_text(
    "ARGS=" + repr(sys.argv[1:]) + "\n" + kube.read_text(encoding="utf-8")
    + "\nPATH=" + str(kube),
    encoding="utf-8", newline="\n")
print("[]")
''')
    wiki = tmp_path / "wiki" / "cluster-wiki.md"
    env = patrol_free_env(
        PATROL_API_KEY="test-key",
        PATROL_BASE_URL="http://127.0.0.1:1/v1",
        PATROL_MODEL="fake-model",
        PATROL_API_SERVER="https://203.0.113.10:6443",
        PATROL_API_TOKEN="secret-token",
        KUBECTL=cli)
    r = subprocess.run(
        [sys.executable, str(PATROL), "--once", "--max-steps", "1",
         "--wiki", str(wiki)],
        capture_output=True, text=True, timeout=60, env=env)
    assert out.exists(), r.stderr[-1000:]
    assert "secret-token" not in (r.stdout + r.stderr)
    captured = out.read_text(encoding="utf-8")
    args, rest = captured.split("\n", 1)
    kube_text, kube_path = rest.rsplit("\nPATH=", 1)
    assert "secret-token" not in args
    assert "203.0.113.10" in kube_text
    assert "secret-token" in kube_text
    assert not Path(kube_path).exists(), "the process removes the kubeconfig at exit"


# ------------------------------------------------------------------ startup

def test_main_passes_the_target_to_the_patrol(tmp_path, monkeypatch):
    calls = []
    kube = tmp_path / "kubeconfig"
    kube.write_text("apiVersion: v1\n", encoding="utf-8", newline="\n")
    monkeypatch.setattr(patrol, "API_KEY", "key")
    monkeypatch.setattr(patrol, "BASE_URL", "http://127.0.0.1:1/v1")
    monkeypatch.setattr(patrol, "MODEL", "model")
    monkeypatch.setattr(patrol, "API_SERVER", "https://api.example:6443")
    monkeypatch.setattr(patrol, "API_TOKEN", "secret-token")
    monkeypatch.setattr(
        patrol, "configure_kubectl_target",
        lambda server, token: calls.append(("target", server, token)) or kube)
    monkeypatch.setattr(patrol, "resolve_kubectl", lambda: "oc")
    monkeypatch.setattr(patrol, "ensure_wiki_repo", lambda *a: True)

    async def fake_run(**kwargs):
        calls.append(("patrol", kwargs))

    monkeypatch.setattr(patrol, "run_patrol", fake_run)
    monkeypatch.setattr(sys, "argv", [
        str(PATROL), "--once", "--dry-cluster",
        "--wiki", str(tmp_path / "wiki" / "cluster-wiki.md")])
    patrol.main()
    assert calls[0] == ("target", "https://api.example:6443", "secret-token")
    assert calls[1][0] == "patrol"
    assert calls[1][1]["kubeconfig"] == str(kube)


def test_server_without_token_fails_startup_before_the_wiki(tmp_path):
    wiki = tmp_path / "wiki" / "cluster-wiki.md"
    r = subprocess.run(
        [sys.executable, str(PATROL), "--once", "--dry-cluster",
         "--wiki", str(wiki)],
        capture_output=True, text=True, timeout=45,
        env=patrol_free_env(PATROL_API_KEY="test-key",
                            PATROL_BASE_URL="http://127.0.0.1:1/v1",
                            PATROL_MODEL="fake-model",
                            PATROL_API_SERVER="https://api.example:6443",
                            KUBECTL="oc"))
    assert r.returncode != 0
    assert "PATROL_API_TOKEN" in r.stderr, r.stderr[-500:]
    assert "https://api.example:6443" not in r.stderr, "the server value stays out of logs"
    assert not wiki.parent.exists(), "the config error runs before any wiki touch"
