"""Wiki layout — one remote repo, one branch per cluster. Contract tests for #89.

These tests pin the fleet ref layout:

    WIKI_BRANCH_PREFIX                           # "cluster/"
    wiki_branch(cluster_id) -> str               # slug validation, or ValueError
    ensure_wiki_repo(wiki_dir, branch) -> bool   # init on `branch`, adopt as-is
    push_wiki(wiki_dir, remote) -> bool          # same-named branch, never force

They fail before implementation (missing symbol, or the old one-argument
`ensure_wiki_repo`) and must pass after.

Contract: .ai/spec/what/wiki-memory.md rules 4a, 4a1, 4e, 4f1, 4f2
(ADR .ai/spec/decisions/0039-one-repo-one-branch-per-cluster.md).

Run: uv run --group dev pytest tests/ -q
"""
import subprocess
import sys
from pathlib import Path

import pytest

import patrol
from support import patrol_free_env


REPO = Path(__file__).resolve().parent.parent
PATROL = REPO / "patrol.py"


def git(*args, cwd):
    """Run git in cwd, raising on failure — tests assert on real git state."""
    return subprocess.run(["git", *args], cwd=cwd, capture_output=True,
                          text=True, check=True)


def heads(cwd) -> list[str]:
    """Branch refs in the repo, sorted."""
    out = git("for-each-ref", "--format=%(refname)", "refs/heads", cwd=cwd)
    return sorted(p for p in out.stdout.split() if p)


# ------------------------------------------------------------------ branch name (rule 4a1)

def test_branch_prefix_constant():
    """Rule 4a1: the branch layout is one prefix constant, not scattered text."""
    assert patrol.WIKI_BRANCH_PREFIX == "cluster/"


@pytest.mark.parametrize("cluster_id", [
    "local",
    "ols-demo-2",
    "a",
    "a1",
    "1",
    "a_b",
    "a.b",
    "1.2.3",
    "a--b",
    "a" * 63,
])
def test_wiki_branch_accepts_valid_slugs(cluster_id):
    """Rule 4a1: a valid slug maps to exactly one cluster branch."""
    assert patrol.wiki_branch(cluster_id) == "cluster/" + cluster_id


@pytest.mark.parametrize("cluster_id", [
    "",
    " ",
    "abc ",
    " abc",
    "Local",          # uppercase is not a slug
    "ABC",
    "-abc",
    "abc-",
    ".abc",
    "abc.",
    "a..b",           # git forbids '..' in a ref
    "a b",
    "a/b",            # one ref component only
    "a:b",
    "a~b",
    "a^b",
    "a?b",
    "a*b",
    "a[b",
    "a\\b",
    "a@{b",
    "a.lock",         # git forbids a '.lock' suffix
    "a\nb",
    "a\tb",
    "a" * 64,         # too long
    "@",
])
def test_wiki_branch_rejects_invalid_slugs(cluster_id):
    """Rule 4a1: a ref-special value is never silently mapped into a branch."""
    with pytest.raises(ValueError):
        patrol.wiki_branch(cluster_id)


def test_wiki_branch_error_names_the_value():
    """Rule 4a1: the operator sees the offending value, not a bare ref error."""
    with pytest.raises(ValueError) as excinfo:
        patrol.wiki_branch("bad id")
    assert "bad id" in str(excinfo.value)


# ------------------------------------------------------------------ startup config error (rule 4a1)

def test_invalid_cluster_id_fails_startup_before_touching_the_wiki(tmp_path):
    """Rule 4a1: a bad PATROL_CLUSTER_ID is a config error, not a patrol."""
    wiki = tmp_path / "wiki" / "cluster-wiki.md"
    r = subprocess.run(
        [sys.executable, str(PATROL), "--once", "--dry-cluster",
         "--wiki", str(wiki)],
        capture_output=True, text=True, timeout=120,
        env=patrol_free_env(PATROL_API_KEY="test-key",
                            PATROL_BASE_URL="http://fake.test/v1",
                            PATROL_MODEL="fake-model",
                            PATROL_CLUSTER_ID="bad id",
                            KUBECTL="oc"),
    )
    assert r.returncode != 0, "an invalid CLUSTER_ID must fail startup"
    assert "PATROL_CLUSTER_ID" in r.stderr, r.stderr[-400:]
    assert "bad id" in r.stderr, r.stderr[-400:]
    assert not wiki.parent.exists(), \
        "the config error must run before any wiki touch"
    # the offline modes branch before the slug check (rule 4a1)
    wiki.parent.mkdir(parents=True)
    wiki.write_text("# wiki\n", encoding="utf-8", newline="\n")
    r = subprocess.run(
        [sys.executable, str(PATROL), "--wiki-stats", "--wiki", str(wiki)],
        capture_output=True, text=True, timeout=120,
        env=patrol_free_env(PATROL_CLUSTER_ID="bad id"))
    assert r.returncode == 0, r.stderr[-400:]
    egress_env = patrol_free_env(PATROL_CLUSTER_ID="bad id",
                                 PATROL_BASE_URL="http://127.0.0.1:8080/v1",
                                 KUBECTL="oc")
    egress_env.pop("KUBERNETES_SERVICE_HOST", None)
    egress_env.pop("KUBERNETES_SERVICE_PORT", None)
    r = subprocess.run(
        [sys.executable, str(PATROL), "--egress-policy"],
        capture_output=True, text=True, timeout=120, env=egress_env)
    assert r.returncode == 0, r.stderr[-400:]


# ------------------------------------------------------------------ init branch (rule 4a)

def test_ensure_wiki_repo_inits_on_the_given_branch(tmp_path):
    """Rule 4a: a fresh wiki repo is born on the cluster branch, not main."""
    wiki_dir = tmp_path / "wiki"
    assert patrol.ensure_wiki_repo(wiki_dir, "cluster/alpha") is True
    assert git("symbolic-ref", "HEAD", cwd=wiki_dir).stdout.strip() == \
        "refs/heads/cluster/alpha"


def test_adopted_repo_keeps_its_branch(tmp_path):
    """Rule 4a: an adopted repo is never renamed, switched, or re-branched."""
    wiki_dir = tmp_path / "wiki"
    wiki_dir.mkdir()
    git("init", "-b", "main", cwd=wiki_dir)
    (wiki_dir / "cluster-wiki.md").write_text("# legacy\n", encoding="utf-8",
                                              newline="\n")
    git("add", "cluster-wiki.md", cwd=wiki_dir)
    git("-c", "user.name=t", "-c", "user.email=t@t",
        "commit", "-m", "legacy", cwd=wiki_dir)
    assert patrol.ensure_wiki_repo(wiki_dir, "cluster/alpha") is True
    assert git("symbolic-ref", "HEAD", cwd=wiki_dir).stdout.strip() == \
        "refs/heads/main"
    assert heads(wiki_dir) == ["refs/heads/main"], \
        "the patroller must not create the cluster branch on an adopted repo"


# ------------------------------------------------------------------ same-named push (rule 4e)

def _pushed_spoke(tmp_path, cluster_id: str, body: str, remote: Path):
    """A spoke repo on its cluster branch, one commit, pushed to `remote`."""
    wiki_dir = tmp_path / f"wiki-{cluster_id}"
    assert patrol.ensure_wiki_repo(wiki_dir, patrol.wiki_branch(cluster_id))
    wiki = wiki_dir / "cluster-wiki.md"
    patrol.write_wiki_atomic(wiki, body)
    assert patrol.commit_wiki(wiki, [], cluster_id) is True
    assert patrol.push_wiki(wiki_dir, str(remote)) is True
    sha = git("rev-parse", "HEAD", cwd=wiki_dir).stdout.strip()
    return wiki_dir, sha


def test_push_lands_the_current_branch_on_the_same_named_branch(tmp_path):
    """Rule 4e: the refspec keeps the branch name — no fixed destination."""
    remote = tmp_path / "remote.git"
    git("init", "--bare", "-b", "main", str(remote), cwd=tmp_path)
    wiki_dir = tmp_path / "wiki"
    patrol.ensure_wiki_repo(wiki_dir, "cluster/alpha")
    wiki = wiki_dir / "cluster-wiki.md"
    patrol.write_wiki_atomic(wiki, "# alpha wiki\n")
    assert patrol.commit_wiki(wiki, [], "alpha") is True
    assert patrol.push_wiki(wiki_dir, str(remote)) is True
    sha = git("rev-parse", "HEAD", cwd=wiki_dir).stdout.strip()
    assert git("rev-parse", "--verify", "refs/heads/cluster/alpha",
               cwd=remote).stdout.strip() == sha
    assert heads(remote) == ["refs/heads/cluster/alpha"]


def test_push_never_forces(tmp_path, monkeypatch):
    """Rule 4e: a successful push carries no force flag and no '+' refspec."""
    push_calls = []
    real_run = subprocess.run

    def recording_run(cmd, *args, **kwargs):
        if cmd and cmd[0] == "git" and "push" in cmd:
            push_calls.append(list(cmd))
        return real_run(cmd, *args, **kwargs)

    monkeypatch.setattr(patrol.subprocess, "run", recording_run)
    remote = tmp_path / "remote.git"
    git("init", "--bare", "-b", "main", str(remote), cwd=tmp_path)
    _pushed_spoke(tmp_path, "alpha", "# alpha wiki\n", remote)

    assert push_calls, "the push must have run"
    for cmd in push_calls:
        assert "-f" not in cmd, cmd
        assert not any(str(a).startswith("--force") for a in cmd), cmd
        assert not any(str(a).startswith("+") for a in cmd), cmd


def test_adopted_repo_pushes_its_own_branch(tmp_path):
    """Rule 4e: an adopted repo pushes its own branch (for example `main`),
    never a hard-coded cluster branch."""
    remote = tmp_path / "remote.git"
    git("init", "--bare", "-b", "main", str(remote), cwd=tmp_path)
    wiki_dir = tmp_path / "wiki"
    wiki_dir.mkdir()
    git("init", "-b", "main", cwd=wiki_dir)
    assert patrol.ensure_wiki_repo(wiki_dir, "cluster/alpha") is True
    wiki = wiki_dir / "cluster-wiki.md"
    patrol.write_wiki_atomic(wiki, "# legacy wiki\n")
    assert patrol.commit_wiki(wiki, [], "legacy") is True
    assert patrol.push_wiki(wiki_dir, str(remote)) is True
    sha = git("rev-parse", "HEAD", cwd=wiki_dir).stdout.strip()
    assert git("rev-parse", "--verify", "refs/heads/main",
               cwd=remote).stdout.strip() == sha
    assert heads(remote) == ["refs/heads/main"]


def test_diverged_remote_branch_rejects_the_push_without_force(tmp_path,
                                                               monkeypatch):
    """Rule 4e: a non-fast-forward push fails, and the remote keeps its commit."""
    monkeypatch.setattr(patrol, "PUSH_BACKOFF", 0)
    monkeypatch.setattr(patrol, "PUSH_ATTEMPTS", 1)
    remote = tmp_path / "remote.git"
    git("init", "--bare", "-b", "main", str(remote), cwd=tmp_path)
    wiki_dir, sha1 = _pushed_spoke(tmp_path, "alpha", "# v1\n", remote)

    # someone else moves the remote branch to an unrelated commit
    tree = git("rev-parse", "HEAD^{tree}", cwd=wiki_dir).stdout.strip()
    divergent = git("-c", "user.name=t", "-c", "user.email=t@t",
                    "commit-tree", tree, "-m", "divergent",
                    cwd=wiki_dir).stdout.strip()
    git("update-ref", "refs/heads/cluster/alpha", divergent, cwd=remote)

    # the spoke moves ahead on its own history and pushes again
    wiki = wiki_dir / "cluster-wiki.md"
    patrol.write_wiki_atomic(wiki, "# v2\n")
    assert patrol.commit_wiki(wiki, [], "alpha") is True
    assert patrol.push_wiki(wiki_dir, str(remote)) is False
    assert git("rev-parse", "--verify", "refs/heads/cluster/alpha",
               cwd=remote).stdout.strip() == divergent, \
        "the push must never overwrite a diverged remote branch"
    assert git("rev-parse", "HEAD", cwd=wiki_dir).stdout.strip() != sha1, \
        "the local commit stands (local-first)"


# ------------------------------------------------------------------ fleet layout (rules 4f1/4f2)

def test_two_spokes_one_remote_land_on_separate_branches(tmp_path):
    """Rules 4f1/4f2: each spoke owns its branch; the hub reads each tip."""
    remote = tmp_path / "fleet.git"
    git("init", "--bare", "-b", "main", str(remote), cwd=tmp_path)
    _alpha, alpha_sha = _pushed_spoke(tmp_path, "alpha", "# ALPHA wiki\n", remote)
    _beta, beta_sha = _pushed_spoke(tmp_path, "beta", "# BETA wiki\n", remote)

    assert heads(remote) == ["refs/heads/cluster/alpha",
                             "refs/heads/cluster/beta"]
    assert git("rev-parse", "--verify", "refs/heads/cluster/alpha",
               cwd=remote).stdout.strip() == alpha_sha
    assert git("rev-parse", "--verify", "refs/heads/cluster/beta",
               cwd=remote).stdout.strip() == beta_sha
    # the hub read procedure: one file at each branch tip, same path
    assert git("show", "cluster/alpha:cluster-wiki.md",
               cwd=remote).stdout == "# ALPHA wiki\n"
    assert git("show", "cluster/beta:cluster-wiki.md",
               cwd=remote).stdout == "# BETA wiki\n"


def test_a_spoke_never_reads_another_cluster_branch(tmp_path, monkeypatch):
    """Rule 4f2: a spoke stays on its own branch and never fetches."""
    git_calls = []
    real_run = subprocess.run

    def recording_run(cmd, *args, **kwargs):
        if cmd and cmd[0] == "git":
            git_calls.append(list(cmd))
        return real_run(cmd, *args, **kwargs)

    monkeypatch.setattr(patrol.subprocess, "run", recording_run)
    remote = tmp_path / "fleet.git"
    git("init", "--bare", "-b", "main", str(remote), cwd=tmp_path)
    alpha_dir, _ = _pushed_spoke(tmp_path, "alpha", "# ALPHA wiki\n", remote)
    _pushed_spoke(tmp_path, "beta", "# BETA wiki\n", remote)

    assert heads(alpha_dir) == ["refs/heads/cluster/alpha"], \
        "the alpha spoke must not gain beta's branch"
    assert "BETA" not in (alpha_dir / "cluster-wiki.md").read_text(
        encoding="utf-8")
    # and a later alpha patrol still pushes its own branch, unaffected
    wiki = alpha_dir / "cluster-wiki.md"
    patrol.write_wiki_atomic(wiki, "# ALPHA v2\n")
    assert patrol.commit_wiki(wiki, [], "alpha") is True
    assert patrol.push_wiki(alpha_dir, str(remote)) is True
    assert git("show", "cluster/beta:cluster-wiki.md",
               cwd=remote).stdout == "# BETA wiki\n"
    forbidden = ("fetch", "pull", "checkout", "merge", "rebase")

    def git_subcommand(cmd: list[str]) -> str:
        i = 1
        while i < len(cmd):
            if cmd[i] in ("-C", "-c"):
                i += 2
                continue
            return cmd[i]
        return ""

    for cmd in git_calls:
        assert git_subcommand(cmd) not in forbidden, \
            f"a spoke must never fetch, checkout, merge, or rebase: {cmd}"
