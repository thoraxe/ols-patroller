"""Container image — contract tests for #1 (ubi9-minimal in-cluster image).

These tests pin the image contract STATICALLY: they parse the repo-root
`Containerfile` and `requirements.txt` and assert the build recipe's rules —
the Containerfile is the artifact under contract, so its content rules are the
behavior under test. No container runtime is needed; they fail on a missing or
non-conforming file.

Runtime acceptance (image builds; oc/kubectl/git/python imports work inside the
container; missing-env fast-fail) is a documented procedure performed with a
local container runtime — .ai/spec/what/container-image.md rule 12 — not part of
this suite. The full in-cluster patrol is #2's done-when.

Contract: .ai/spec/what/container-image.md rules 1-11 [PLANNED: #1].

Run: uv run --group dev pytest tests/ -q
"""
import re
from pathlib import Path


REPO = Path(__file__).resolve().parent.parent
CONTAINERFILE = REPO / "Containerfile"
REQUIREMENTS = REPO / "requirements.txt"
PYPROJECT = REPO / "pyproject.toml"
PATROL = REPO / "patrol.py"


# ------------------------------------------------------------------ helpers

def _norm(name: str) -> str:
    """Normalize a package name for set comparison (PEP 503-lite)."""
    return name.lower().replace("_", "-")


def _cf() -> list[str]:
    """Logical instruction lines of the Containerfile.

    Line continuations are joined; blank lines and full-line comments dropped,
    so assertions see whole instructions. Called inside each test so a missing
    file fails that test directly.
    """
    assert CONTAINERFILE.exists(), (
        "Containerfile must exist at the repo root "
        "(.ai/spec/what/container-image.md rule 1, #1)"
    )
    joined = re.sub(r"\\\s*\n", " ", CONTAINERFILE.read_text())
    lines = [ln.strip() for ln in joined.splitlines()]
    return [ln for ln in lines if ln and not ln.startswith("#")]


def _req() -> list[str]:
    assert REQUIREMENTS.exists(), (
        "requirements.txt must exist at the repo root "
        "(.ai/spec/what/container-image.md rule 6, #1)"
    )
    raw = REQUIREMENTS.read_text().splitlines()
    return [ln.strip() for ln in raw if ln.strip() and not ln.strip().startswith("#")]


def _pinned_names(lines: list[str]) -> set[str]:
    """Parse requirement names, asserting every line is an exact pin (rule 6)."""
    names = set()
    for ln in lines:
        m = re.match(r"^([A-Za-z0-9][A-Za-z0-9._\-]*)==(\S+)$", ln)
        assert m, (
            f"every requirement must be an exact pin 'name==version' "
            f"(rule 6), got: {ln!r}"
        )
        names.add(_norm(m.group(1)))
    return names


def _pep723_names() -> set[str]:
    """Top-level dependency names from patrol.py's PEP 723 uv header (rule 7)."""
    text = PATROL.read_text()
    m = re.search(r"# /// script\n(.*?)# ///", text, re.S)
    assert m, "patrol.py must keep its PEP 723 uv header (rule 7)"
    return {_norm(n) for n in
            re.findall(r'"([A-Za-z0-9._\-]+)\s*[><=~^!]', m.group(1))}


def _pyproject_project_names() -> set[str]:
    """Dependency names from pyproject.toml's [project] section only (rule 7)."""
    text = PYPROJECT.read_text()
    m = re.search(r"^\[project\]\n(.*?)(?=^\[|\Z)", text, re.S | re.M)
    assert m, "pyproject.toml must keep its [project] dependencies (rule 7)"
    return {_norm(n) for n in
            re.findall(r'"([A-Za-z0-9._\-]+)\s*[><=~^!]', m.group(1))}


# ------------------------------------------------------------------ rules 1-4: base & OS

def test_containerfile_exists():
    assert CONTAINERFILE.exists(), (
        "Containerfile must exist at the repo root "
        "(.ai/spec/what/container-image.md rule 1, #1)"
    )


def test_base_image_is_ubi9_minimal():
    cf = _cf()
    froms = [ln for ln in cf if ln.startswith("FROM")]
    assert froms, "Containerfile must have a FROM instruction (rule 1)"
    assert any(
        re.match(r"FROM\s+registry\.access\.redhat\.com/ubi9/ubi-minimal(?=[\s:@]|$)", ln)
        for ln in froms
    ), "base image must be registry.access.redhat.com/ubi9/ubi-minimal (rule 1)"


def test_os_packages_python_and_git():
    cf = _cf()
    installs = [ln for ln in cf if "microdnf" in ln and "install" in ln]
    assert installs, "OS packages must be installed via microdnf (rule 1)"
    text = " ".join(installs)
    assert re.search(r"python3\.12(?![\w.\-])", text), (
        "microdnf must install python3.12 (rule 1)"
    )
    assert re.search(r"python3\.12-pip(?![\w.\-])", text), (
        "microdnf must install python3.12-pip (rule 1)"
    )
    assert re.search(r"\bgit\b", text), "microdnf must install git (rule 1)"


def test_oc_client_fetched_pinned():
    text = "\n".join(_cf())
    urls = re.findall(r"mirror\.openshift\.com/\S*openshift-client\S*\.tar\.gz", text)
    assert urls, (
        "the build must fetch an openshift-client tarball from mirror.openshift.com "
        "(rule 2)"
    )
    for url in urls:
        assert not re.search(r"stable|latest", url, re.I), (
            f"client URL must be version-pinned, no stable/latest floating tags "
            f"(rule 3), got: {url}"
        )
        assert re.search(r"/[0-9][0-9A-Za-z.\-]*/openshift-client", url), (
            f"client URL must name an exact release — digit-leading path segment "
            f"before the archive name (rule 3), got: {url}"
        )


def test_oc_tarball_checksum_verified():
    cf = _cf()
    digest_lines = [ln for ln in cf if re.search(r"\b[0-9a-f]{64}\b", ln)]
    assert digest_lines, (
        "a SHA-256 digest for the client tarball must be pinned in the "
        "Containerfile (rule 4)"
    )
    assert any("openshift-client" in ln for ln in digest_lines), (
        "the pinned SHA-256 digest must live in the instruction that handles the "
        "client tarball (rule 4)"
    )
    assert any(re.search(r"sha256sum\s+(-c|--check)\b", ln) for ln in cf), (
        "the build must verify the client tarball with 'sha256sum -c' before "
        "extraction (rule 4)"
    )
    extract = re.compile(r"\btar\s+-?\S*x")  # tar -xzf / tar xzf; not '.tar.gz'
    for ln in cf:
        m = extract.search(ln)
        if ("openshift-client" in ln and m
                and re.search(r"sha256sum\s+(-c|--check)\b", ln)):
            assert ln.find("sha256sum") < m.start(), (
                f"checksum verification must precede extraction in the client "
                "fetch instruction (rule 4), got: {ln!r}"
            )


# ------------------------------------------------------------------ rules 5-7: dependencies

def test_dependencies_installed_at_build_from_requirements():
    runs = "\n".join(ln for ln in _cf() if ln.startswith("RUN"))
    assert re.search(r"\bpip[0-9.]*\s+install\b|-m\s+pip\s+install\b", runs), (
        "Python dependencies must be pip-installed at build time (rule 5)"
    )
    assert "requirements.txt" in runs, (
        "pip install must consume the repo-root requirements.txt (rule 5)"
    )


def test_no_runtime_package_installation():
    runtime = "\n".join(ln for ln in _cf() if ln.startswith(("ENTRYPOINT", "CMD")))
    assert runtime, "Containerfile must define ENTRYPOINT/CMD (rule 9)"
    assert "pip" not in runtime and not re.search(r"\buv\b", runtime), (
        "ENTRYPOINT/CMD must not install or resolve packages at container start "
        "(rule 5)"
    )


def test_requirements_exact_pins():
    req_lines = _req()
    assert req_lines, (
        "requirements.txt must pin the runtime dependencies, it is empty (rule 6)"
    )
    _pinned_names(req_lines)  # asserts every line is 'name==version'


def test_requirements_mirror_pep723_and_pyproject():
    req = _pinned_names(_req())
    pep = _pep723_names()
    py = _pyproject_project_names()
    assert req == pep, (
        f"requirements.txt must pin exactly the PEP 723 top-level set (rule 7); "
        f"requirements={sorted(req)} pep723={sorted(pep)}"
    )
    assert py <= req, (
        f"requirements.txt must cover pyproject.toml [project] dependencies "
        f"(rule 7); missing={sorted(py - req)}"
    )


def test_apprise_replaces_requests_top_level():
    """#9: apprise joins the pinned runtime set and requests drops from it
    (container-image.md rule 6 — requests' only direct use was the Slack
    webhook post; apprise brings it transitively)."""
    pep = _pep723_names()
    req = _pinned_names(_req())
    py = _pyproject_project_names()
    for names, surface in ((pep, "the PEP 723 header"),
                           (req, "requirements.txt"),
                           (py, "pyproject.toml [project] dependencies")):
        assert "apprise" in names, (
            f"apprise must be a top-level runtime dependency in {surface} "
            f"(rule 6, #9)"
        )
        assert "requests" not in names, (
            f"requests must drop from the top-level set in {surface} "
            f"(rule 6, #9 — transitive via apprise)"
        )


# ------------------------------------------------------------------ rules 8-10: user & layout

def test_runtime_user_non_root():
    users = [ln for ln in _cf() if ln.startswith("USER")]
    assert users, "Containerfile must set a runtime USER (rule 8)"
    m = re.match(r"USER\s+(\S+)", users[-1])
    uid_gid = m.group(1)
    uid, _, gid = uid_gid.partition(":")
    assert uid.lower() not in ("root", "0"), (
        f"runtime USER must be non-root (rule 8), got: {uid_gid!r}"
    )
    if gid:
        assert gid == "0", (
            f"runtime USER group must be GID 0 for random-UID compatibility "
            f"(rule 8), got: {uid_gid!r}"
        )


def test_wiki_mount_point_random_uid_writable():
    wiki = [ln for ln in _cf() if "/wiki" in ln]
    assert wiki, (
        "the /wiki mount point must be prepared in the image (rule 8)"
    )
    assert any(re.search(r"\b(?:mkdir|install)\b", ln) for ln in wiki), (
        "/wiki must be created — mkdir or install -d (rule 8)"
    )
    assert any(
        re.search(r"\b(?:chgrp|chown|install)\b", ln)
        and re.search(r"(?:^|\s|:)0(?:\s|$|:)", ln)
        for ln in wiki
    ), "/wiki must be group-owned by GID 0 — chgrp/chown ... 0 (rule 8)"
    assert any(
        re.search(r"\b(?:chmod|install)\b", ln)
        and re.search(r"g=u|g\+w|g\+rw|g\+rwX|\b77[57]\b|\b2775\b", ln)
        for ln in wiki
    ), "/wiki must be group-writable — chmod g=u or equivalent (rule 8)"


def test_copies_patrol_and_requirements():
    copies = "\n".join(ln for ln in _cf() if ln.startswith(("COPY", "ADD")))
    assert "patrol.py" in copies, "the image must COPY patrol.py in (rule 9)"
    assert "requirements.txt" in copies, (
        "the image must COPY requirements.txt in (rule 9)"
    )


def test_default_command_loop_wiki():
    cf = _cf()
    ep = [ln for ln in cf if ln.startswith("ENTRYPOINT")]
    cmd = [ln for ln in cf if ln.startswith("CMD")]
    assert ep, "ENTRYPOINT must invoke patrol.py via python3.12 (rule 9)"
    assert cmd, "CMD must carry the default patrol args (rule 9)"
    eptext = " ".join(ep)
    assert "python3.12" in eptext and "patrol.py" in eptext, (
        f"ENTRYPOINT must run patrol.py with the image's python3.12 (rule 9), "
        f"got: {eptext!r}"
    )
    cmdtext = " ".join(cmd)
    for token in ("--loop", "--interval", "21600", "--wiki", "/wiki/cluster-wiki.md"):
        assert token in cmdtext, (
            f"CMD default args must include {token!r} — 6h cadence, identical "
            f"to the Deployment args (rule 9, #25), got: {cmdtext!r}"
        )


def test_no_secrets_or_patrol_env_baked():
    for ln in (ln for ln in _cf() if ln.startswith("ENV")):
        assert "PATROL_" not in ln and "SLACK_" not in ln, (
            f"the image must not bake config or secrets via ENV (rule 10), "
            f"got: {ln!r}"
        )
