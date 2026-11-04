"""Host-portable text I/O — contract tests for #77.

The patroller owns every byte of its text I/O: the wiki is UTF-8 without a BOM
with LF newlines, read back as UTF-8 explicitly, and the console is UTF-8/LF
regardless of the host locale. Development runs on Linux and Windows, so the
same content must produce the same bytes on both.

Contract: `.ai/spec/what/wiki-memory.md` rules 4c/4g,
`.ai/spec/what/reporting-notifications.md` rule 9b, ADR 0029.

The encoding/legacy tests drive the real functions in a child process under a
hostile ASCII locale with Python's UTF-8 mode off
(`support.hostile_locale_env`), so they fail on any host where the code inherits
the locale instead of pinning UTF-8. The five encoding/legacy tests fail now
(the write path uses `Path.write_text` with no encoding, the read paths use
locale decoding, `wiki_history` can yield `None`, and the console is not
reconfigured) and must pass after the #77 implementation. The other two tests
are harness guards that pass today: the dev-Python pin shipped with #77, and
`patrol_free_env` is the helper the stats tests use.
"""
import codecs
import os
import subprocess
import sys
from pathlib import Path

import pytest

from support import hostile_locale_env, patrol_free_env

import patrol


REPO = Path(__file__).resolve().parent.parent
PATROL = REPO / "patrol.py"

DOT = "\u00b7"      # U+00B7 middle dot — the metadata-line separator
ARROW = "\u2192"    # not encodable in cp1252 — the write path must not use it
YO = "\u0401"       # its UTF-8 bytes contain 0x81, undefined in cp1252


def wiki_text(patrol_count=3):
    """A skeleton-valid wiki with non-ASCII prose (rules 10-12a)."""
    return "\n".join([
        "---",
        "wiki_version: 1",
        "cluster_id: hostport",
        f"patrol_count: {patrol_count}",
        'last_patrol: "2026-11-04T18:44:00+0000"',
        "---",
        "# Cluster Wiki",
        "",
        "## Topology",
        f"- 3 masters {ARROW} 2 workers ({YO} observed, 2026-11-04, high)",
        "",
        "## Baselines",
        "_(none)_",
        "",
        "## Watch-list",
        (f"- id: W-1 {DOT} status: watching {DOT} first_seen: 2026-10-01"
         f" {DOT} last_verified: 2026-11-04 {DOT} source: kubectl get nodes"
         f" {DOT} confidence: high {DOT} recheck: get nodes"),
        f"  worker-1 NotReady {ARROW} hypothesis ({YO}).",
        "",
        "## Open questions",
        "- (none yet)",
        "",
        "## Anomaly log",
        "_(empty)_",
        "",
    ])


def _run(args, **kw):
    return subprocess.run(args, capture_output=True, timeout=240,
                          env=hostile_locale_env(), **kw)


def test_atomic_write_is_utf8_lf_without_bom(tmp_path):
    """Rule 4c: the wiki is written as deterministic bytes — UTF-8, no BOM,
    LF — on every host. Before #77 the temp file took the locale encoding
    (cp1252, CRLF on Windows), so the same wiki differed by host and the
    UTF-8 history reader (rule 27) could not decode it."""
    wiki = tmp_path / "wiki" / "cluster-wiki.md"
    wiki.parent.mkdir()
    content = wiki_text()
    driver = tmp_path / "driver.py"
    driver.write_text(
        "import sys\n"
        f"sys.path.insert(0, {str(REPO)!r})\n"
        "import patrol\n"
        f"patrol.write_wiki_atomic({str(wiki)!r}, {content!r})\n",
        encoding="utf-8", newline="\n")

    r = _run([sys.executable, str(driver)])
    assert r.returncode == 0, r.stderr.decode("utf-8", "replace")

    raw = wiki.read_bytes()
    assert raw == content.encode("utf-8"), "the wiki must be UTF-8, not locale-encoded"
    assert not raw.startswith(codecs.BOM_UTF8), "the wiki must carry no BOM"
    assert b"\r" not in raw, "the wiki must use LF, never CRLF"


def test_stats_mode_reads_utf8_and_prints_utf8_lf(tmp_path):
    """Rules 4g + 9b: `--wiki-stats` decodes the wiki as UTF-8 explicitly and
    prints UTF-8/LF stdout regardless of the host locale. Before #77 the read
    used the locale (mojibake/crash on a UTF-8 wiki) and the console inherited
    it (the ` · ` separator became a bare 0xb7 byte on Windows)."""
    wiki = tmp_path / "wiki" / "cluster-wiki.md"
    wiki.parent.mkdir()
    wiki.write_bytes(wiki_text().encode("utf-8"))

    r = _run([sys.executable, str(PATROL), "--wiki-stats", "--wiki", str(wiki)])
    assert r.returncode == 0, r.stderr.decode("utf-8", "replace")
    out = r.stdout.decode("utf-8")  # strict: the console contract is UTF-8
    assert "verification:" in out and "resolution:" in out
    assert "verification: 0/1" in out, \
        "the UTF-8 metadata separator must still parse the Watch-list entry"
    assert DOT in out, "the metadata separator must survive as UTF-8"
    assert b"\r\n" not in r.stdout, "console output must use LF, never CRLF"


def test_stats_mode_tolerates_a_legacy_locale_encoded_wiki(tmp_path):
    """Rule 4g: a pre-#77 wiki (cp1252 bytes, including bytes cp1252 itself
    leaves undefined) is decoded leniently, not fatally — an upgrade must not
    turn every patrol into a crash loop."""
    wiki = tmp_path / "wiki" / "cluster-wiki.md"
    wiki.parent.mkdir()
    legacy = wiki_text().replace(ARROW, "-").replace(YO, "o").encode("cp1252")
    legacy = legacy.replace(b"- 3 masters", b"- 3 masters \x81", 1)
    wiki.write_bytes(legacy)

    r = _run([sys.executable, str(PATROL), "--wiki-stats", "--wiki", str(wiki)])
    assert r.returncode == 0, r.stderr.decode("utf-8", "replace")
    out = r.stdout.decode("utf-8")
    assert "cluster hostport" in out
    # rule 4g: the cp1252 ` · ` separators became U+FFFD, so the
    # Watch-list no longer parses (runnable memory, not accepted memory).
    assert "verification: n/a (empty watch-list)" in out


def test_dev_python_is_pinned_for_a_bare_uv_run():
    """#77 acceptance: a bare `uv run --group dev pytest` must not select a
    Python that cannot build the dependency set (3.15 has no `zstandard`
    wheel). The pin shipped with #77 (so this guard passes today); it is the
    regression guard for the acceptance criterion."""
    pin = (REPO / ".python-version").read_text(encoding="utf-8").strip()
    assert pin.startswith("3.12"), (
        f".python-version must pin 3.12 (found {pin!r}) — without it `uv` picks "
        "the newest interpreter and the dependency resolve fails")


def test_stats_subprocess_env_carries_no_patrol_variables(monkeypatch):
    """Rule 23 (#77): the stats-mode subprocess still sees no `PATROL_*`
    variables — the offline guarantee is not weakened by inheriting the host
    environment (which Windows needs for `SystemRoot`)."""
    monkeypatch.setenv("PATROL_API_KEY", "must-not-leak")
    monkeypatch.setenv("PATROL_BASE_URL", "http://must-not-leak")
    env = patrol_free_env(EXTRA="kept")
    assert not any(k.startswith("PATROL_") for k in env)
    assert env["EXTRA"] == "kept"
    assert env.get("PATH") == os.environ.get("PATH")
    if os.name == "nt":
        assert env.get("SystemRoot") or env.get("SYSTEMROOT"), \
            "Windows needs SystemRoot for the child"


def test_console_stderr_is_utf8_lf_under_hostile_locale(tmp_path):
    """Rule 9b: stderr is pinned to UTF-8/LF too. An invalid wiki makes the
    stats mode warn on stderr with the wiki path, so a non-ASCII path proves
    the stream's encoding (the stdout test above proves the other stream)."""
    wiki = tmp_path / "Ё" / "cluster-wiki.md"
    wiki.parent.mkdir()
    wiki.write_text("# not a skeleton wiki\n\n## Topology\n- t\n",
                    encoding="utf-8", newline="\n")

    r = _run([sys.executable, str(PATROL), "--wiki-stats", "--wiki", str(wiki)])
    assert r.returncode == 0, r.stderr.decode("utf-8", "replace")
    err = r.stderr.decode("utf-8")   # strict: the console contract is UTF-8
    assert "Ё" in err
    assert b"\r\n" not in r.stderr, "console output must use LF, never CRLF"


@pytest.mark.filterwarnings("error::pytest.PytestUnhandledThreadExceptionWarning")
def test_wiki_history_degrades_on_undecodable_blobs(tmp_path):
    """Rule 27 (#77): a blob that is not valid UTF-8 yields the empty list —
    never a partial window and never a `None` entry. A dev-local pre-#77 repo
    may hold cp1252 blobs; the production archive is UTF-8."""
    wiki = tmp_path / "wiki" / "cluster-wiki.md"
    wiki.parent.mkdir()
    assert patrol.ensure_wiki_repo(wiki.parent)
    wiki.write_bytes(wiki_text().replace(ARROW, "-").replace(YO, "o")
                     .encode("cp1252"))   # cp1252 ` · ` = 0xB7
    assert patrol.commit_wiki(wiki, [], "test") is True
    wiki.write_bytes(wiki_text(patrol_count=4).encode("utf-8"))
    assert patrol.commit_wiki(wiki, [], "test") is True

    assert patrol.wiki_history(wiki, 5) == []
