"""Wiki git persistence — contract tests for #3 (git-backed wiki).

These tests pin the public surface of patrol.py's planned wiki-git region:

    ensure_wiki_repo(wiki_dir) -> bool          # rule 4a/4b: init at startup
    write_wiki_atomic(wiki_path, content)       # rule 4c: temp file + rename
    commit_wiki(wiki_path, findings, cluster_id) -> bool   # rule 4d
    push_wiki(wiki_dir, remote) -> bool         # rule 4e: bounded best-effort push
    PUSH_ATTEMPTS / PUSH_TIMEOUT / PUSH_BACKOFF # rule 4e constants

They fail before implementation (AttributeError on the missing symbols) and must
pass after. Contract: .ai/spec/what/wiki-memory.md rules 4a-4e [PLANNED: #3].

Scope note: these tests pin the persistence layer itself. The wiring into
`main()`/`run_patrol` (repo ensured at startup before the first patrol; write →
commit → push order in the patrol tail; push only when `PATROL_WIKI_REMOTE` is
set) is contract-only here — it is verified end-to-end by the fake-model e2e
planned in #4.

Run: uv run --group dev pytest tests/ -q
"""
import os
import subprocess
from pathlib import Path

import pytest

import patrol


def git(*args, cwd):
    """Run git in cwd, raising on failure — tests assert on real git state."""
    return subprocess.run(["git", *args], cwd=cwd, capture_output=True,
                          text=True, check=True)


# ------------------------------------------------------------------ ensure_wiki_repo

def test_ensure_wiki_repo_inits_when_absent(tmp_path):
    wiki_dir = tmp_path / "wiki"
    wiki_dir.mkdir()
    assert patrol.ensure_wiki_repo(wiki_dir) is True
    assert (wiki_dir / ".git").is_dir()
    top = git("rev-parse", "--show-toplevel", cwd=wiki_dir).stdout.strip()
    assert Path(top) == wiki_dir
    # default branch is main (rule 4a)
    ref = git("symbolic-ref", "HEAD", cwd=wiki_dir).stdout.strip()
    assert ref == "refs/heads/main"


def test_ensure_wiki_repo_creates_missing_directory(tmp_path):
    wiki_dir = tmp_path / "wiki"  # does not exist yet
    assert patrol.ensure_wiki_repo(wiki_dir) is True
    assert (wiki_dir / ".git").is_dir()


def test_ensure_wiki_repo_idempotent(tmp_path):
    assert patrol.ensure_wiki_repo(tmp_path) is True
    assert patrol.ensure_wiki_repo(tmp_path) is True  # already a repo: no error
    assert git("rev-parse", "--is-inside-work-tree",
               cwd=tmp_path).stdout.strip() == "true"


def test_ensure_wiki_repo_without_git_binary(tmp_path, monkeypatch):
    """Rule 4b: no git on PATH -> file-only mode, never raises."""
    def no_git(*args, **kwargs):
        raise FileNotFoundError("git not found")
    monkeypatch.setattr(patrol.subprocess, "run", no_git)
    assert patrol.ensure_wiki_repo(tmp_path) is False


# ------------------------------------------------------------------ write_wiki_atomic

def test_write_wiki_atomic_replaces_and_cleans_up(tmp_path):
    wiki = tmp_path / "cluster-wiki.md"
    patrol.write_wiki_atomic(wiki, "old content")
    patrol.write_wiki_atomic(wiki, "new content")
    assert wiki.read_text() == "new content"
    # no temporary files left behind in the wiki directory
    assert sorted(p.name for p in tmp_path.iterdir()) == ["cluster-wiki.md"]


def test_write_wiki_atomic_failure_leaves_old_intact(tmp_path, monkeypatch):
    """Rule 4c: a crash before the rename must not truncate the old wiki."""
    wiki = tmp_path / "cluster-wiki.md"
    wiki.write_text("old content")

    def boom(*args, **kwargs):
        raise RuntimeError("simulated crash before rename")

    monkeypatch.setattr(os, "replace", boom)
    monkeypatch.setattr(os, "rename", boom)
    with pytest.raises(RuntimeError):
        patrol.write_wiki_atomic(wiki, "new content")
    assert wiki.read_text() == "old content"
    # the temporary file is cleaned up on failure
    assert sorted(p.name for p in tmp_path.iterdir()) == ["cluster-wiki.md"]


# ------------------------------------------------------------------ commit_wiki

def test_commit_wiki_message_and_identity(tmp_path):
    patrol.ensure_wiki_repo(tmp_path)
    wiki = tmp_path / "cluster-wiki.md"
    patrol.write_wiki_atomic(wiki, "# wiki v1\n")
    findings = [{"severity": "info"}, {"severity": "warn"}, {"severity": "crit"}]
    assert patrol.commit_wiki(wiki, findings, "test-cluster") is True
    out = git("log", "-1", "--format=%an|%ae|%cn|%ce|%s", cwd=tmp_path).stdout.strip()
    # identity is per-command config (rule 4d); Y counts warn AND crit
    assert out == ("ols-patroller|patrol@test-cluster|"
                   "ols-patroller|patrol@test-cluster|patrol 1: 3 findings, 2 warn")


def test_commit_wiki_number_increments(tmp_path):
    patrol.ensure_wiki_repo(tmp_path)
    wiki = tmp_path / "cluster-wiki.md"
    patrol.write_wiki_atomic(wiki, "# v1\n")
    patrol.commit_wiki(wiki, [], "c")
    patrol.write_wiki_atomic(wiki, "# v2\n")
    patrol.commit_wiki(wiki, [{"severity": "warn"}], "c")
    assert git("rev-list", "--count", "HEAD", cwd=tmp_path).stdout.strip() == "2"
    assert git("log", "-1", "--format=%s", cwd=tmp_path).stdout.strip() == \
        "patrol 2: 1 findings, 1 warn"


def test_commit_wiki_no_change_no_commit(tmp_path):
    patrol.ensure_wiki_repo(tmp_path)
    wiki = tmp_path / "cluster-wiki.md"
    patrol.write_wiki_atomic(wiki, "# same\n")
    assert patrol.commit_wiki(wiki, [], "c") is True
    patrol.write_wiki_atomic(wiki, "# same\n")  # byte-identical rewrite
    assert patrol.commit_wiki(wiki, [], "c") is False
    assert git("rev-list", "--count", "HEAD", cwd=tmp_path).stdout.strip() == "1"


def test_commit_wiki_stages_only_the_wiki_file(tmp_path):
    """Rule 4d: exactly the wiki file is staged — a tracked, modified stray file
    must NOT ride along (this is what rules out a literal `git commit -am`)."""
    patrol.ensure_wiki_repo(tmp_path)
    stray = tmp_path / "stray.txt"
    stray.write_text("baseline\n")
    git("add", "stray.txt", cwd=tmp_path)
    # baseline commit including stray.txt, so it is tracked before commit_wiki runs
    git("-c", "user.name=t", "-c", "user.email=t@t",
        "commit", "-m", "setup", cwd=tmp_path)
    stray.write_text("modified after baseline\n")  # tracked AND modified
    wiki = tmp_path / "cluster-wiki.md"
    patrol.write_wiki_atomic(wiki, "# wiki\n")
    assert patrol.commit_wiki(wiki, [], "c") is True
    # the wiki commit itself touches only the wiki file
    assert git("show", "--name-only", "--format=", "HEAD",
               cwd=tmp_path).stdout.split() == ["cluster-wiki.md"]
    assert git("ls-files", cwd=tmp_path).stdout.split() == \
        ["cluster-wiki.md", "stray.txt"]


def test_commit_wiki_without_repo_returns_false(tmp_path):
    """Rule 4b degradation: no repo (ensure never ran) -> no commit, no crash."""
    wiki = tmp_path / "cluster-wiki.md"
    patrol.write_wiki_atomic(wiki, "# wiki\n")
    assert patrol.commit_wiki(wiki, [], "c") is False
    assert not (tmp_path / ".git").exists()  # commit_wiki never inits


def test_commit_wiki_independent_of_home_and_user_config(tmp_path, monkeypatch):
    """Rule 4d: git invocations work with no readable HOME / user git config."""
    monkeypatch.setenv("HOME", str(tmp_path / "no-such-home"))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "no-such-xdg"))
    patrol.ensure_wiki_repo(tmp_path)
    wiki = tmp_path / "cluster-wiki.md"
    patrol.write_wiki_atomic(wiki, "# wiki\n")
    assert patrol.commit_wiki(wiki, [{"severity": "warn"}], "c") is True


# ------------------------------------------------------------------ push_wiki

def test_push_wiki_success(tmp_path):
    wiki_dir = tmp_path / "wiki"
    remote = tmp_path / "remote.git"
    patrol.ensure_wiki_repo(wiki_dir)
    git("init", "--bare", "-b", "main", str(remote))
    wiki = wiki_dir / "cluster-wiki.md"
    patrol.write_wiki_atomic(wiki, "# wiki\n")
    patrol.commit_wiki(wiki, [], "c")
    assert patrol.push_wiki(wiki_dir, str(remote)) is True
    local_sha = git("rev-parse", "HEAD", cwd=wiki_dir).stdout.strip()
    remote_sha = git("rev-parse", "--verify", "refs/heads/main",
                     cwd=remote).stdout.strip()
    assert remote_sha == local_sha


def test_push_wiki_retries_are_bounded(tmp_path, monkeypatch):
    """Rule 4e: a dead remote yields exactly PUSH_ATTEMPTS git push invocations."""
    push_calls = []
    real_run = subprocess.run

    def counting_run(cmd, *args, **kwargs):
        if cmd and cmd[0] == "git" and "push" in cmd:
            push_calls.append(list(cmd))
        return real_run(cmd, *args, **kwargs)

    monkeypatch.setattr(patrol.subprocess, "run", counting_run)
    monkeypatch.setattr(patrol, "PUSH_BACKOFF", 0)  # no sleeping in tests

    wiki_dir = tmp_path / "wiki"
    patrol.ensure_wiki_repo(wiki_dir)
    wiki = wiki_dir / "cluster-wiki.md"
    patrol.write_wiki_atomic(wiki, "# wiki\n")
    patrol.commit_wiki(wiki, [], "c")
    dead_remote = str(tmp_path / "no-such-remote.git")
    assert patrol.push_wiki(wiki_dir, dead_remote) is False
    assert len(push_calls) == patrol.PUSH_ATTEMPTS


def test_push_constants_are_bounded():
    """Rule 4e: the retry bounds are the specced module constants."""
    assert patrol.PUSH_ATTEMPTS == 3
    assert patrol.PUSH_TIMEOUT == 30
    assert patrol.PUSH_BACKOFF == 5
