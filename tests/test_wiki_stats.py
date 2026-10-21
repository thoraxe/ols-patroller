"""Wiki health stats — contract tests for #8.

These tests pin the public surface of patrol.py's planned stats mode:

    WIKI_CHURN_WINDOW / WIKI_STATS_LOPSIDED_MIN / WIKI_STATS_LOPSIDED_SHARE
                                                        # rules 25/27 constants
    wiki_stats(content) -> dict                         # rule 25 (3 document stats)
    wiki_churn(revisions) -> list[str] | None           # rule 26 (churn parser)
    wiki_history(wiki_path, window) -> list[str]        # rule 27 (revision series)
    --wiki-stats                                         # rules 23-24 (the CLI mode)

They fail before implementation (AttributeError on the missing symbols,
AssertionError on the missing CLI surface) and must pass after.
Contract: .ai/spec/what/wiki-memory.md rules 23-27 [PLANNED: #8].

Scope note: the stats mode has no model in it, so the fake-model e2e is not
extended — the subprocess tests below ARE the wiring tests (the mode is run
exactly as a human runs it, with no PATROL_* env).

Run: uv run --group dev pytest tests/ -q
"""
import os
import subprocess
import sys
from pathlib import Path

import patrol


REPO = Path(__file__).resolve().parent.parent
PATROL = REPO / "patrol.py"
SOURCE = PATROL.read_text()

SECTIONS = ["Topology", "Baselines", "Watch-list", "Open questions", "Anomaly log"]

DOT = "·"  # U+00B7 middle dot — the metadata-line separator (rule 12)


def meta_line(id="W-1", status="watching", first_seen="2026-10-01",
              last_verified="2026-10-10", source="kubectl get nodes",
              confidence="high", recheck="get nodes", verified_patrol=None):
    """A well-formed entry metadata line (rules 12 + 12a; the verified_patrol
    pair rides the additional-pairs slot)."""
    line = (f"- id: {id} · status: {status} · first_seen: {first_seen}"
            f" · last_verified: {last_verified} · source: {source}"
            f" · confidence: {confidence}")
    if recheck is not None:
        line += f" · recheck: {recheck}"
    if verified_patrol is not None:
        line += f" · verified_patrol: {verified_patrol}"
    return line


def entry(id="W-1", prose="  worker-1 NotReady for 40d — free prose hypothesis.",
          **kw):
    """A full entry block: metadata line + prose continuation lines."""
    return meta_line(id=id, **kw) + "\n" + prose


def build_wiki(watch=(), anomaly=(), patrol_count=3, cluster_id="test-cluster"):
    """Assemble a skeleton-valid wiki (rules 10-12a) from entry blocks."""
    bodies = {
        "Topology": "- 3 masters, 2 workers (kubectl get nodes, 2026-10-14, high)",
        "Baselines": "_(none)_",
        "Watch-list": "\n".join(watch) if watch else "_(empty)_",
        "Open questions": "- (none yet)",
        "Anomaly log": "\n".join(anomaly) if anomaly else "_(empty)_",
    }
    parts = ["---", "wiki_version: 1", f"cluster_id: {cluster_id}",
             f"patrol_count: {patrol_count}",
             'last_patrol: "2026-10-14T18:44:00+0000"', "---",
             "# Cluster Wiki", ""]
    for name in SECTIONS:
        parts += [f"## {name}", bodies[name], ""]
    wiki = "\n".join(parts)
    assert patrol.validate_wiki(wiki) == [], "test fixture must be skeleton-valid"
    return wiki


def commit_rev(wiki_path, content):
    """One accepted write + one commit (rules 4c/4d): the churn history unit."""
    patrol.write_wiki_atomic(wiki_path, content)
    assert patrol.commit_wiki(wiki_path, [], "test") is True


def run_stats(wiki, *extra):
    """Run the stats mode exactly as a human would — as a subprocess with a
    minimal env carrying NO PATROL_* variables (rule 23: the mode is fully
    offline; PATH for git, PYTHONIOENCODING so the middle dot prints the
    same everywhere)."""
    env = {"PATH": os.environ.get("PATH", ""), "PYTHONIOENCODING": "utf-8"}
    return subprocess.run(
        [sys.executable, str(PATROL), "--wiki-stats", "--wiki", str(wiki), *extra],
        capture_output=True, text=True, timeout=240, env=env)


# ---------------------------------------------------------- config surface (rules 25/27)

def test_stats_constants_surface():
    """Rules 25/27: the lopsided thresholds and the churn window are pinned
    constants (not env-configurable)."""
    assert patrol.WIKI_CHURN_WINDOW == 5
    assert patrol.WIKI_STATS_LOPSIDED_MIN == 5
    assert patrol.WIKI_STATS_LOPSIDED_SHARE == 2 / 3


def test_help_shows_wiki_stats_flag():
    """Rule 23: the flag exists and is documented."""
    r = subprocess.run([sys.executable, str(PATROL), "--help"],
                       capture_output=True, text=True, timeout=240)
    assert r.returncode == 0, r.stderr[-400:]
    assert "--wiki-stats" in r.stdout, (
        "--help must document --wiki-stats (wiki-memory.md rule 23, #8)")


# ---------------------------------------------------------- wiki_stats: verification rate (rule 25)

def test_verification_rate_counts_clock_equal_to_patrol_count():
    """Rule 25: verified this patrol == verified_patrol equal to the frontmatter
    patrol_count — over Watch-list entries only (an Anomaly-log entry is never
    in the denominator, and a clock ahead of P is not verified)."""
    wiki = build_wiki(watch=[entry(id="W-1", verified_patrol=7),
                             entry(id="W-2", verified_patrol=8),
                             entry(id="W-3", verified_patrol=6)],
                      anomaly=[entry(id="A-1", status="expired",
                                     verified_patrol=7)],
                      patrol_count=7)
    s = patrol.wiki_stats(wiki)
    assert set(s) == {"patrol_count", "cluster_id", "watch_total",
                      "watch_verified", "verification_rate", "status_counts",
                      "confidence_counts", "lopsided", "char_count",
                      "over_budget"}  # exactly ten keys (#40 added the size pair)
    assert s["patrol_count"] == 7
    assert s["cluster_id"] == "test-cluster"
    assert s["watch_total"] == 3
    assert s["watch_verified"] == 1
    assert s["verification_rate"] == 1 / 3


def test_verification_rate_missing_or_garbled_clock_is_not_verified():
    """Rule 25: a missing or garbled verified_patrol pair (a pre-#7 wiki, a
    hand-edit) counts as not verified — the parser is defensive."""
    wiki = build_wiki(watch=[entry(id="W-1", verified_patrol=None),
                             entry(id="W-2", verified_patrol="soon")],
                      patrol_count=4)
    s = patrol.wiki_stats(wiki)
    assert s["watch_total"] == 2
    assert s["watch_verified"] == 0
    assert s["verification_rate"] == 0.0


def test_verification_rate_empty_watch_list_is_none():
    """Rule 25: no Watch-list entries -> verification_rate is None (printed
    n/a), never a divide-by-zero."""
    wiki = build_wiki(anomaly=[entry(id="A-1", status="expired")],
                      patrol_count=9)
    s = patrol.wiki_stats(wiki)
    assert s["watch_total"] == 0
    assert s["watch_verified"] == 0
    assert s["verification_rate"] is None


# ---------------------------------------------------------- wiki_stats: resolution ratio (rule 25)

def test_resolution_ratio_counts_statuses_across_both_sections():
    """Rule 25: watching/resolved/expired counts over BOTH metadata sections,
    bucketed by status value regardless of section (expired entries are read
    straight from the Anomaly log)."""
    wiki = build_wiki(watch=[entry(id="W-1", status="watching"),
                             entry(id="W-2", status="resolved"),
                             entry(id="W-3", status="expired")],
                      anomaly=[entry(id="A-1", status="expired", recheck=None),
                               entry(id="A-2", status="watching", recheck=None)])
    s = patrol.wiki_stats(wiki)
    assert s["status_counts"] == {"watching": 2, "resolved": 1, "expired": 2}


# ---------------------------------------------------------- wiki_stats: confidence spread (rule 25)

def test_confidence_spread_counts_both_sections():
    """Rule 25: high/medium/low counts over both metadata sections."""
    wiki = build_wiki(watch=[entry(id="W-1", confidence="high"),
                             entry(id="W-2", confidence="medium")],
                      anomaly=[entry(id="A-1", status="expired",
                                     confidence="low", recheck=None),
                               entry(id="A-2", status="resolved",
                                     confidence="low", recheck=None)])
    s = patrol.wiki_stats(wiki)
    assert s["confidence_counts"] == {"high": 1, "medium": 1, "low": 2}
    assert s["lopsided"] is None  # 2/4 = 50% (and n < 5 besides)


def test_lopsided_fires_at_two_thirds_with_min_entries():
    """Rule 25: 4 high of 6 graded (4/6 >= 2/3, n >= 5) -> the warning fires
    and names the dominant level."""
    wiki = build_wiki(watch=[entry(id="W-1", confidence="high"),
                             entry(id="W-2", confidence="high"),
                             entry(id="W-3", confidence="high")],
                      anomaly=[entry(id="A-1", status="expired",
                                     confidence="high", recheck=None),
                               entry(id="A-2", status="resolved",
                                     confidence="medium", recheck=None),
                               entry(id="A-3", status="expired",
                                     confidence="low", recheck=None)])
    s = patrol.wiki_stats(wiki)
    assert s["confidence_counts"] == {"high": 4, "medium": 1, "low": 1}
    assert s["lopsided"] == "high"


def test_lopsided_silent_below_min_entries():
    """Rule 25: 100% high but only 4 graded entries — below the minimum, the
    spread is reported without judgment."""
    wiki = build_wiki(watch=[entry(id="W-1", confidence="high"),
                             entry(id="W-2", confidence="high"),
                             entry(id="W-3", confidence="high")],
                      anomaly=[entry(id="A-1", status="expired",
                                     confidence="high", recheck=None)])
    assert patrol.wiki_stats(wiki)["lopsided"] is None


def test_lopsided_silent_below_two_thirds():
    """Rule 25: 3 high of 6 (50%) is not lopsided."""
    wiki = build_wiki(watch=[entry(id="W-1", confidence="high"),
                             entry(id="W-2", confidence="high"),
                             entry(id="W-3", confidence="high"),
                             entry(id="W-4", confidence="medium")],
                      anomaly=[entry(id="A-1", status="expired",
                                     confidence="medium", recheck=None),
                               entry(id="A-2", status="resolved",
                                     confidence="low", recheck=None)])
    assert patrol.wiki_stats(wiki)["lopsided"] is None


def test_lopsided_names_any_dominant_level():
    """Rule 25: 'a grade everyone gets is not a grade' holds for ANY level —
    4 low of 5 warns as 'low', not only a high monopoly."""
    wiki = build_wiki(watch=[entry(id="W-1", confidence="low"),
                             entry(id="W-2", confidence="low"),
                             entry(id="W-3", confidence="low")],
                      anomaly=[entry(id="A-1", status="expired",
                                     confidence="low", recheck=None),
                               entry(id="A-2", status="resolved",
                                     confidence="high", recheck=None)])
    assert patrol.wiki_stats(wiki)["lopsided"] == "low"


# ---------------------------------------------------------- wiki_stats: defensive parsing (rule 25)

def test_stats_parse_defensively():
    """Rule 25: a hand-edited wiki with no frontmatter still parses —
    patrol_count 0, cluster_id ''; a prose-only list item is invisible; a
    garbage status value skips that stat only; the entry still counts where
    its fields parse."""
    raw = (
        "## Topology\n- t\n\n## Baselines\n_(none)_\n\n## Watch-list\n"
        + entry(id="W-1", verified_patrol=3, recheck=None) + "\n"
        + "- just a prose note, not an entry\n"
        + entry(id="W-2", status="bogus", recheck=None) + "\n"
        + "\n## Open questions\n- q\n\n## Anomaly log\n_(empty)_\n"
    )
    s = patrol.wiki_stats(raw)
    assert s["patrol_count"] == 0        # no frontmatter: P counts 0
    assert s["cluster_id"] == ""
    assert s["watch_total"] == 2         # both id-carrying entries
    assert s["watch_verified"] == 0      # vp 3 != 0
    assert s["verification_rate"] == 0.0
    assert s["status_counts"] == {"watching": 1, "resolved": 0, "expired": 0}
    assert s["confidence_counts"] == {"high": 2, "medium": 0, "low": 0}
    assert s["lopsided"] is None         # 2 graded < 5


# ---------------------------------------------------------- wiki_churn (rule 26)

def test_churn_flags_entry_reworded_every_patrol():
    """Rule 26: an entry present throughout whose prose changed in every
    adjacent revision-pair, with status unchanged, is flagged — even as its
    clock and frontmatter advance (the stamps are not prose)."""
    revs = [build_wiki(watch=[entry(id="W-1", prose=f"  hypothesis v{i}",
                                    verified_patrol=i)],
                       patrol_count=i)
            for i in (1, 2, 3)]
    assert patrol.wiki_churn(revs) == ["W-1"]


def test_churn_immune_to_stamps_and_date_advances():
    """Rule 26: identical prose with advancing verified_patrol, last_verified,
    and frontmatter counters is the healthy case — metadata-line and
    frontmatter changes never count as rewording."""
    prose = "  stable hypothesis"
    r1 = build_wiki(watch=[entry(id="W-1", prose=prose,
                                 last_verified="2026-10-01", verified_patrol=1)],
                    patrol_count=1)
    r2 = build_wiki(watch=[entry(id="W-1", prose=prose,
                                 last_verified="2026-10-08", verified_patrol=2)],
                    patrol_count=2)
    assert patrol.wiki_churn([r1, r2]) == []


def test_churn_requires_status_stability():
    """Rule 26: a status change in any pair breaks the flag — resolution and
    expiry are learning events, never drift."""
    r1 = build_wiki(watch=[entry(id="W-1", status="watching", prose="  v1")],
                    patrol_count=1)
    r2 = build_wiki(watch=[entry(id="W-1", status="resolved", prose="  v2")],
                    patrol_count=2)
    r3 = build_wiki(watch=[entry(id="W-1", status="resolved", prose="  v3")],
                    patrol_count=3)
    assert patrol.wiki_churn([r1, r2, r3]) == []


def test_churn_stable_prose_breaks_the_streak():
    """Rule 26: the flag needs a churn pair in EVERY pair — one stable
    revision pair breaks it."""
    revs = [build_wiki(watch=[entry(id="W-1", prose=p, verified_patrol=i)],
                       patrol_count=i)
            for i, p in ((1, "  v1"), (2, "  v1"), (3, "  v2"))]
    assert patrol.wiki_churn(revs) == []


def test_churn_requires_full_window_presence():
    """Rule 26: an id must be present in every revision of the series — born
    mid-window (churns since birth) and vanished mid-window (churned then
    left) are both unflagged."""
    born = [build_wiki(watch=[entry(id="W-2", prose="  stable")], patrol_count=1),
            build_wiki(watch=[entry(id="W-1", prose="  v1"),
                              entry(id="W-2", prose="  stable")], patrol_count=2),
            build_wiki(watch=[entry(id="W-1", prose="  v2"),
                              entry(id="W-2", prose="  stable")], patrol_count=3)]
    assert patrol.wiki_churn(born) == []

    vanished = [build_wiki(watch=[entry(id="W-1", prose="  v1")], patrol_count=1),
                build_wiki(watch=[entry(id="W-1", prose="  v2")], patrol_count=2),
                build_wiki(watch=[entry(id="W-2", prose="  stable")], patrol_count=3)]
    assert patrol.wiki_churn(vanished) == []


def test_churn_needs_two_revisions():
    """Rule 26: fewer than two revisions is nothing measurable — None, not a
    clean list (the printer says n/a)."""
    r1 = build_wiki(watch=[entry(id="W-1", prose="  v1")], patrol_count=1)
    assert patrol.wiki_churn([]) is None
    assert patrol.wiki_churn([r1]) is None


def test_churn_section_move_is_not_churn():
    """Rule 26: a section move (Watch-list -> Anomaly log, a deliberate model
    placement) with stable prose and status is not churn — location is not
    prose; but the id is keyed ACROSS sections, so a move combined with a
    reword and unchanged status still churns (cross-section tracking)."""
    r1 = build_wiki(watch=[entry(id="W-1", prose="  same prose")], patrol_count=1)
    r2 = build_wiki(anomaly=[entry(id="W-1", prose="  same prose")],
                    patrol_count=2)
    assert patrol.wiki_churn([r1, r2]) == []

    moved_and_reworded = [
        build_wiki(watch=[entry(id="W-1", prose="  v1")], patrol_count=1),
        build_wiki(anomaly=[entry(id="W-1", prose="  v2")], patrol_count=2),
    ]
    assert patrol.wiki_churn(moved_and_reworded) == ["W-1"]


def test_churn_flags_in_newest_revision_order():
    """Rule 26: flagged ids report in the newest revision's order — Watch-list
    document order first, then the Anomaly log (the live loop first)."""
    def rev(i, w2, w1, a9):
        return build_wiki(watch=[entry(id="W-2", prose=w2),
                                 entry(id="W-1", prose=w1)],
                          anomaly=[entry(id="A-9", status="expired",
                                         prose=a9, recheck=None)],
                          patrol_count=i)
    revs = [rev(1, "  a1", "  b1", "  c1"),
            rev(2, "  a2", "  b2", "  c2"),
            rev(3, "  a3", "  b3", "  c3")]
    assert patrol.wiki_churn(revs) == ["W-2", "W-1", "A-9"]


def test_churn_ignores_fenced_pseudo_entries():
    """Rule 26: fence-aware scanning — a list-looking line AND its indented
    continuation inside a fenced code block are prose, never an entry: a
    fenced pseudo-entry whose metadata parses and whose 'prose' changes is
    invisible to churn."""
    def fenced(prose):
        return ("```\n- id: FENCED · status: watching · first_seen: 2026-10-01"
                " · last_verified: 2026-10-10 · source: kubectl get nodes"
                f" · confidence: high\n  {prose}\n```")
    r1 = build_wiki(watch=[entry(id="W-1", prose="  stable")
                           + "\n\n" + fenced("fenced prose one")],
                    patrol_count=1)
    r2 = build_wiki(watch=[entry(id="W-1", prose="  stable")
                           + "\n\n" + fenced("fenced prose two")],
                    patrol_count=2)
    assert patrol.wiki_churn([r1, r2]) == []


# ---------------------------------------------------------- wiki_history (rule 27)

def test_wiki_history_returns_last_window_oldest_first(tmp_path):
    """Rule 27: the wiki file's blobs at the last `window` commits that
    changed it, oldest -> newest."""
    wiki_path = tmp_path / "wiki" / "cluster-wiki.md"
    patrol.ensure_wiki_repo(wiki_path.parent)
    revisions = {i: build_wiki(watch=[entry(id="W-1", prose=f"  v{i}",
                                            verified_patrol=i)],
                               patrol_count=i)
                 for i in (1, 2, 3)}
    for i in (1, 2, 3):
        commit_rev(wiki_path, revisions[i])
    assert patrol.wiki_history(wiki_path, window=2) == [revisions[2], revisions[3]]


def test_wiki_history_short_history_returns_all(tmp_path):
    """Rule 27: a history shorter than the window yields every revision it
    has — the window takes what exists."""
    wiki_path = tmp_path / "wiki" / "cluster-wiki.md"
    patrol.ensure_wiki_repo(wiki_path.parent)
    v1 = build_wiki(watch=[entry(id="W-1", prose="  v1")], patrol_count=1)
    commit_rev(wiki_path, v1)
    assert patrol.wiki_history(wiki_path, window=5) == [v1]


def test_wiki_history_empty_without_repo_or_commits(tmp_path):
    """Rule 27: no repo (file-only mode) and no commits both degrade to an
    empty series — churn n/a, never an error."""
    wiki_path = tmp_path / "wiki" / "cluster-wiki.md"
    wiki_path.parent.mkdir(parents=True)
    wiki_path.write_text(build_wiki())
    assert patrol.wiki_history(wiki_path, window=5) == []
    patrol.ensure_wiki_repo(wiki_path.parent)  # repo present, zero commits
    assert patrol.wiki_history(wiki_path, window=5) == []


# ---------------------------------------------------------- the stats mode (rules 23-24)

def test_stats_mode_prints_all_four_numbers(tmp_path):
    """Rules 23-24, the issue's done-when: one command prints all four —
    offline (no PATROL_* env), reading the document (three numbers) and the
    git history (churn: W-3 reworded every patrol, W-1/W-2/A-1/A-2 stable)."""
    wiki_path = tmp_path / "wiki" / "cluster-wiki.md"
    patrol.ensure_wiki_repo(wiki_path.parent)

    def rev(i, w3_prose):
        return build_wiki(
            watch=[entry(id="W-1", confidence="high", prose="  stable one",
                         verified_patrol=3),
                   entry(id="W-2", confidence="high", prose="  stable two",
                         verified_patrol=2),
                   entry(id="W-3", confidence="medium", prose=w3_prose,
                         verified_patrol=1)],
            anomaly=[entry(id="A-1", status="expired", confidence="low",
                           prose="  history one", recheck=None),
                     entry(id="A-2", status="resolved", confidence="high",
                           prose="  history two", recheck=None)],
            patrol_count=i, cluster_id="stats-cluster")

    for i, prose in ((1, "  hypothesis a"), (2, "  hypothesis b"),
                     (3, "  hypothesis c")):
        commit_rev(wiki_path, rev(i, prose))

    r = run_stats(wiki_path)
    assert r.returncode == 0, r.stderr[-400:]
    out = r.stdout
    # header (rule 24)
    assert "stats-cluster" in out and "patrol_count 3" in out
    # 1. verification rate: 1/3 verified by patrol 3 (33%)
    assert "verification:" in out and "1/3" in out
    assert "verified by patrol 3" in out and "33%" in out
    # 2. resolution ratio: 3 watching, 1 resolved, 1 expired, 5 tracked
    assert "resolution:" in out
    assert "watching 3" in out and "resolved 1" in out and "expired 1" in out
    assert "5 tracked" in out
    # 3. confidence spread: high 3, medium 1, low 1 — not lopsided (3/5)
    assert "confidence:" in out
    assert "high 3" in out and "medium 1" in out and "low 1" in out
    assert "WARNING" not in out
    # 4. churn: W-3 reworded in every patrol of the window
    assert "churn:" in out and "W-3" in out


def test_stats_mode_lopsided_warning_and_file_only_churn(tmp_path):
    """Rules 23-25: the lopsided warning prints (4/6 high = 66%); a file-only
    wiki (no repo) degrades churn to n/a; and reading stats never initializes
    a repo."""
    wiki_path = tmp_path / "wiki" / "cluster-wiki.md"
    wiki_path.parent.mkdir(parents=True)
    wiki_path.write_text(build_wiki(
        watch=[entry(id="W-1", confidence="high"),
               entry(id="W-2", confidence="high"),
               entry(id="W-3", confidence="high")],
        anomaly=[entry(id="A-1", status="expired", confidence="high",
                       recheck=None),
                 entry(id="A-2", status="resolved", confidence="medium",
                       recheck=None),
                 entry(id="A-3", status="expired", confidence="low",
                       recheck=None)],
        patrol_count=2))
    r = run_stats(wiki_path)
    assert r.returncode == 0, r.stderr[-400:]
    assert "lopsided" in r.stdout and "4/6" in r.stdout and "high" in r.stdout
    assert "66%" in r.stdout
    assert "churn:" in r.stdout and "n/a" in r.stdout
    assert not (wiki_path.parent / ".git").exists(), \
        "the stats mode must never create or initialize the wiki repo (rule 23)"


def test_stats_mode_missing_wiki_errors(tmp_path):
    """Rule 23: a missing wiki file is a config error — stderr names the
    path, exit 1."""
    missing = tmp_path / "nope" / "cluster-wiki.md"
    r = run_stats(missing)
    assert r.returncode == 1
    assert "cluster-wiki.md" in r.stderr


def test_stats_mode_rejects_loop(tmp_path):
    """Rule 23: --wiki-stats cannot be combined with --loop — a usage error
    (exit 2) whose message names both flags and states the conflict (the
    argparse usage line always names --loop; the message must too)."""
    wiki_path = tmp_path / "wiki" / "cluster-wiki.md"
    wiki_path.parent.mkdir(parents=True)
    wiki_path.write_text(build_wiki())
    r = run_stats(wiki_path, "--loop")
    assert r.returncode == 2
    assert "cannot be combined" in r.stderr
    assert "--wiki-stats" in r.stderr and "--loop" in r.stderr


def test_stats_mode_invalid_wiki_still_prints_with_warning(tmp_path):
    """Rule 23: a wiki that fails skeleton validation still gets its stats
    (defensively parsed) with one stderr warning naming the failure; exit 0."""
    wiki_path = tmp_path / "wiki" / "cluster-wiki.md"
    wiki_path.parent.mkdir(parents=True)
    wiki_path.write_text("# not a skeleton wiki\n\n## Topology\n- t\n")
    r = run_stats(wiki_path)
    assert r.returncode == 0, r.stderr[-400:]
    assert "verification:" in r.stdout   # stats still print
    assert "n/a" in r.stdout             # empty watch-list, no history
    assert "validation" in r.stderr.lower()


def test_stats_mode_churn_none_and_once_precedence(tmp_path):
    """Rules 23-24: measurable history with no churn prints `churn: none`
    (not n/a), and --wiki-stats takes precedence over --once (the default
    patrol mode) — the command prints stats and exits 0."""
    wiki_path = tmp_path / "wiki" / "cluster-wiki.md"
    patrol.ensure_wiki_repo(wiki_path.parent)
    for i in (1, 2):
        commit_rev(wiki_path, build_wiki(
            watch=[entry(id="W-1", prose="  stable prose", verified_patrol=i)],
            patrol_count=i))
    r = run_stats(wiki_path, "--once")
    assert r.returncode == 0, r.stderr[-400:]
    assert "churn:" in r.stdout and "none" in r.stdout
    assert "n/a" not in r.stdout


def test_stats_mode_precedes_env_check_and_kubectl_resolution():
    """Rule 23: the stats branch runs before the env check, kubectl
    resolution, and repo initialization — the mode works with no PATROL_*
    env and never touches the cluster CLI or the repo. A source-ordering
    pin on main()'s body (the test_cli_defaults.py pattern); the env-free
    subprocess runs above are the behavioral half."""
    main_src = SOURCE[SOURCE.index("def main()"):]
    assert "args.wiki_stats" in main_src, \
        "main() must branch on the --wiki-stats flag (rule 23)"
    branch_at = main_src.index("args.wiki_stats")
    assert branch_at < main_src.index("not API_KEY"), \
        "the stats branch must precede the startup env check (rule 23)"
    assert branch_at < main_src.index("resolve_kubectl()"), \
        "the stats branch must precede kubectl resolution (rule 23)"
    assert branch_at < main_src.index("ensure_wiki_repo"), \
        "the stats branch must precede wiki repo initialization (rule 23)"
