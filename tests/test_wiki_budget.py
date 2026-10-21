"""Wiki length budget — contract tests for #40.

These tests pin the public surface of patrol.py's planned working-set
budgets:

    WIKI_ANOMALY_KEEP / WIKI_CHAR_BUDGET             # rules 28-29 constants
    compact_anomaly_log(content, keep)                # rule 28 (compaction pass)
        -> (rewritten content, [pruned entry ids])
    wiki_budget_warnings(content) -> list[str]        # rule 29 (soft budget)
    wiki_stats: char_count / over_budget keys         # rule 25 amendment
    --wiki-stats: the size line + over-budget warning # rule 24 amendment
    REPORT_CONTRACT: working-set expectations         # reporting rule 5 amendment

They fail before implementation (AttributeError on the missing symbols,
AssertionError on the missing surfaces) and must pass after.
Contract: .ai/spec/what/wiki-memory.md rules 28-29 plus the rule 24/25
amendments [PLANNED: #40].

Scope note: like test_wiki_expiry.py, these tests pin the pure passes. The
wiring into the patrol tail (expire -> compact -> stamp, the prune console
lines, the stderr warning, the repair-pass inclusion) is verified
end-to-end by the fake-model e2e in test_patrol_deepagents.py.

Run: uv run --group dev pytest tests/ -q
"""
import os
import subprocess
import sys
from pathlib import Path

import patrol


REPO = Path(__file__).resolve().parent.parent
PATROL = REPO / "patrol.py"

SECTIONS = ["Topology", "Baselines", "Watch-list", "Open questions", "Anomaly log"]

DOT = "·"  # U+00B7 middle dot — the metadata-line separator (rule 12)


def meta_line(id="W-1", status="watching", first_seen="2026-10-01",
              last_verified="2026-10-10", source="kubectl get nodes",
              confidence="high", recheck="get nodes", verified_patrol=None):
    """A well-formed entry metadata line (rules 12 + 12a; the verified_patrol
    pair is optional — the model may carry, drop, or mangle it)."""
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


def closed(i):
    """A closed (resolved/expired) Anomaly-log entry with distinct prose —
    the history the keep budget governs (rule 28)."""
    status = "resolved" if i % 2 else "expired"
    return entry(id=f"A-{i}", status=status, recheck=None,
                 prose=f"  history prose {i}.\n  - sub-bullet {i}")


def build_wiki(watch=(), anomaly=(), patrol_count=3):
    """Assemble a skeleton-valid wiki (rules 10-12a) from entry blocks."""
    bodies = {
        "Topology": "- 3 masters, 2 workers (kubectl get nodes, 2026-10-14, high)",
        "Baselines": "_(none)_",
        "Watch-list": "\n".join(watch) if watch else "_(empty)_",
        "Open questions": "- (none yet)",
        "Anomaly log": "\n".join(anomaly) if anomaly else "_(empty)_",
    }
    parts = ["---", "wiki_version: 1", "cluster_id: test-cluster",
             f"patrol_count: {patrol_count}",
             'last_patrol: "2026-10-14T18:44:00+0000"', "---",
             "# Cluster Wiki", ""]
    for name in SECTIONS:
        parts += [f"## {name}", bodies[name], ""]
    wiki = "\n".join(parts)
    assert patrol.validate_wiki(wiki) == [], "test fixture must be skeleton-valid"
    return wiki


def big_wiki(target=25_000):
    """A skeleton-valid wiki padded past WIKI_CHAR_BUDGET — the padding is
    indented Topology prose (rule 13: prose sections are unvalidated), so
    the overshoot is legal bloat exactly like the real thing."""
    pad = "  Working-set padding prose: the wiki is a document, and this sentence makes it a long one."
    wiki = build_wiki()
    while len(wiki) < target:
        wiki = wiki.replace("## Baselines", pad + "\n## Baselines", 1)
    assert patrol.validate_wiki(wiki) == [], "test fixture must be skeleton-valid"
    return wiki


def split_wiki(content):
    """(frontmatter-text, body) — the frontmatter fence block at the top."""
    lines = content.split("\n")
    assert lines[0] == "---", "test fixture must start with a frontmatter fence"
    end = lines.index("---", 1)
    return "\n".join(lines[1:end]), "\n".join(lines[end + 1:])


def section_body(content, name):
    """The text of one level-2 section: between its heading and the next
    (fence-aware — a `## ` line inside a fence is prose, rule 11)."""
    body = split_wiki(content)[1].split("\n")
    start = body.index(f"## {name}")
    out, in_fence = [], False
    for line in body[start + 1:]:
        if line.strip().startswith("```"):
            in_fence = not in_fence
            out.append(line)
            continue
        if line.startswith("## ") and not in_fence:
            break
        out.append(line)
    return "\n".join(out)


def entry_blocks(section_text):
    """[(metadata line, [continuation lines])] — the section's top-level list
    items, fenced code blocks skipped (rule 11: fenced lines are prose)."""
    blocks, current, in_fence = [], None, False
    for line in section_text.split("\n"):
        if line.strip().startswith("```"):
            in_fence = not in_fence
            current = None
            continue
        if in_fence:
            continue
        if line.startswith("- ") or line.startswith("* "):
            current = [line, []]
            blocks.append(current)
        elif not line.strip():
            continue  # a blank line does not end the item
        elif line.startswith((" ", "\t")) and current is not None:
            current[1].append(line)
        else:
            current = None  # a column-0 prose line (e.g. a placeholder) ends it
    return [(b[0], b[1]) for b in blocks]


def pairs_of(meta):
    """{key: value} of one metadata line (values never contain the separator)."""
    return dict(p.split(": ", 1) for p in meta[len("- "):].split(f" {DOT} "))


def run_stats(wiki, *extra):
    """The stats mode as a human runs it — a subprocess with a minimal env
    carrying NO PATROL_* variables (rule 23: the mode is fully offline)."""
    env = {"PATH": os.environ.get("PATH", ""), "PYTHONIOENCODING": "utf-8"}
    return subprocess.run(
        [sys.executable, str(PATROL), "--wiki-stats", "--wiki", str(wiki), *extra],
        capture_output=True, text=True, timeout=240, env=env)


# ---------------------------------------------------------- config surface (rules 28-29)

def test_budget_constants_surface():
    """Rules 28-29: the anomaly-log keep budget (20 resolved/expired entries)
    and the soft whole-wiki character budget (20,000) are pinned constants —
    like the stats constants, retuning them is a spec change, not a config
    knob."""
    assert patrol.WIKI_ANOMALY_KEEP == 20
    assert patrol.WIKI_CHAR_BUDGET == 20_000


# ---------------------------------------------------------- the compaction pass (rule 28)

def test_compact_prunes_closed_entries_beyond_the_last_keep():
    """Rule 28: the Anomaly log keeps the LAST `keep` resolved/expired
    entries in document order — earlier closed entries are pruned, whole
    items (prose and sub-bullets included), and the pruned ids return in
    document order; the compacted wiki still validates."""
    wiki = build_wiki(anomaly=[closed(i) for i in range(1, 6)])
    out, pruned = patrol.compact_anomaly_log(wiki, keep=2)
    assert pruned == ["A-1", "A-2", "A-3"]
    ids = [pairs_of(m)["id"] for m, _ in
           entry_blocks(section_body(out, "Anomaly log"))]
    assert ids == ["A-4", "A-5"]
    assert "history prose 1" not in out and "sub-bullet 2" not in out
    assert "history prose 4" in out and "sub-bullet 5" in out
    assert patrol.validate_wiki(out) == []


def test_compact_keeps_watching_entries_and_never_touches_watch_list():
    """Rule 28: watching Anomaly-log entries are never pruned and never count
    against the budget; the pass never touches the Watch-list — a
    resolved/expired entry there is the model's placement to reconsider
    (mirroring rule 21's flip scope)."""
    live = entry(id="A-live", status="watching", recheck=None,
                 prose="  still live, never pruned")
    wiki = build_wiki(
        watch=[entry(id="W-1", status="resolved"),
               entry(id="W-2", status="expired"),
               entry(id="W-3")],
        anomaly=[live, closed(1), closed(2), closed(3)])
    out, pruned = patrol.compact_anomaly_log(wiki, keep=2)
    assert pruned == ["A-1"]
    wl_ids = [pairs_of(m)["id"] for m, _ in
              entry_blocks(section_body(out, "Watch-list"))]
    assert wl_ids == ["W-1", "W-2", "W-3"]
    al_ids = [pairs_of(m)["id"] for m, _ in
              entry_blocks(section_body(out, "Anomaly log"))]
    assert al_ids == ["A-live", "A-2", "A-3"]  # watching kept, outside the count
    assert patrol.validate_wiki(out) == []


def test_compact_within_budget_returns_content_unchanged():
    """Rule 28: nothing beyond the budget -> nothing pruned and the content
    returned unchanged (the minimal-rewrite guarantee, byte-for-byte)."""
    wiki = build_wiki(anomaly=[closed(1), closed(2)])
    out, pruned = patrol.compact_anomaly_log(wiki, keep=2)
    assert pruned == []
    assert out == wiki


def test_compact_zero_keep_prunes_all_closed_entries():
    """Rule 28: keep=0 prunes every resolved/expired entry (aggressive but
    legal) — watching entries still stay and the section stands (rule 11
    allows an itemless section; the process never adds prose)."""
    wiki = build_wiki(anomaly=[entry(id="A-live", status="watching",
                                     recheck=None), closed(2)])
    out, pruned = patrol.compact_anomaly_log(wiki, keep=0)
    assert pruned == ["A-2"]
    al = section_body(out, "Anomaly log")
    assert [pairs_of(m)["id"] for m, _ in entry_blocks(al)] == ["A-live"]
    assert patrol.validate_wiki(out) == []


def test_compact_preserves_everything_outside_pruned_items():
    """Rule 28: a minimal rewrite — the frontmatter, the prose sections, the
    Watch-list, non-item prose in the Anomaly log (a placeholder-class
    line), and the surviving entries with their prose are preserved
    byte-for-byte; only the pruned items vanish."""
    note = "Closed history beyond the budget lives in git."
    wiki = build_wiki(watch=[entry(id="W-1")],
                      anomaly=[note, closed(1), closed(2), closed(3)])
    out, pruned = patrol.compact_anomaly_log(wiki, keep=1)
    assert pruned == ["A-1", "A-2"]
    assert split_wiki(out)[0] == split_wiki(wiki)[0]  # frontmatter untouched
    for section in ("Topology", "Baselines", "Open questions", "Watch-list"):
        assert section_body(out, section) == section_body(wiki, section)
    al = section_body(out, "Anomaly log")
    assert note in al                          # non-item prose never touched
    survivors = {pairs_of(m)["id"]: (m, cont)
                 for m, cont in entry_blocks(al)}
    assert set(survivors) == {"A-3"}
    originals = {pairs_of(m)["id"]: (m, cont)
                 for m, cont in entry_blocks(section_body(wiki, "Anomaly log"))}
    assert survivors["A-3"] == originals["A-3"]  # survivor byte-for-byte
    assert patrol.validate_wiki(out) == []


def test_compact_is_fence_aware():
    """Rule 28: fenced list-looking lines are prose — never pruned, never
    counted toward the budget (the validator's scanning discipline,
    rule 11)."""
    fenced = "```\n- id: FENCED · status: expired · fenced prose, not an entry\n```"
    wiki = build_wiki(anomaly=[closed(1), fenced])
    out, pruned = patrol.compact_anomaly_log(wiki, keep=0)
    assert pruned == ["A-1"]
    assert fenced in section_body(out, "Anomaly log")
    assert patrol.validate_wiki(out) == []


def test_flipped_entries_land_newest_and_survive_the_cut():
    """Rule 28's ordering: the compaction pass runs AFTER the expiry pass,
    so an entry flipped this patrol (appended at the section's end, rule 21)
    is among the newest and survives a cut that prunes older closed
    entries."""
    new = build_wiki(watch=[entry(id="W-1", last_verified="2026-10-01")],
                     anomaly=[closed(i) for i in range(1, 4)])
    old = build_wiki(watch=[entry(id="W-1", last_verified="2026-10-01",
                                  verified_patrol=1)],
                     anomaly=[closed(i) for i in range(1, 4)])
    expired, flips = patrol.expire_stale_entries(new, old, patrol_count=7,
                                                 stale_after=5)
    assert flips == [("W-1", 6)]
    out, pruned = patrol.compact_anomaly_log(expired, keep=2)
    assert pruned == ["A-1", "A-2"]  # the oldest closed go, never the flip
    al_ids = [pairs_of(m)["id"] for m, _ in
              entry_blocks(section_body(out, "Anomaly log"))]
    assert al_ids == ["A-3", "W-1"]
    assert "auto-expired at patrol 7" in out
    assert patrol.validate_wiki(out) == []


# ---------------------------------------------------------- the soft budget (rule 29)

def test_budget_warnings_empty_within_budget():
    """Rule 29: within the soft budget there is nothing to say."""
    assert patrol.wiki_budget_warnings(build_wiki()) == []


def test_budget_warnings_one_line_naming_count_budget_and_instruction():
    """Rule 29: over budget -> exactly one warning naming the wiki's
    character count, the budget, and the summarize-and-prune instruction."""
    wiki = big_wiki()
    assert len(wiki) > patrol.WIKI_CHAR_BUDGET
    warnings = patrol.wiki_budget_warnings(wiki)
    assert len(warnings) == 1
    w = warnings[0]
    assert str(len(wiki)) in w, "the warning must name the actual size"
    assert str(patrol.WIKI_CHAR_BUDGET) in w, "the warning must name the budget"
    assert "summarize" in w and "prune" in w, \
        "the warning must carry the summarize-and-prune instruction"


def test_length_never_gates_validation():
    """Rule 29: the budget is soft — an over-budget wiki that passes the
    skeleton passes validate_wiki; length is not judged (rule 15)."""
    wiki = big_wiki()
    assert patrol.validate_wiki(wiki) == []
    assert patrol.wiki_budget_warnings(wiki)


# ---------------------------------------------------------- the size stat (rule 25 amendment)

def test_wiki_stats_reports_char_count_and_over_budget():
    """Rule 25 (#40 amendment): wiki_stats gains char_count (the whole
    document's character count) and over_budget — False under, True over
    the soft budget."""
    small = build_wiki()
    s = patrol.wiki_stats(small)
    assert s["char_count"] == len(small)
    assert s["over_budget"] is False
    big = big_wiki()
    s = patrol.wiki_stats(big)
    assert s["char_count"] == len(big)
    assert s["over_budget"] is True


# ---------------------------------------------------------- the stats mode (rule 24 amendment)

def test_stats_mode_prints_size_line_and_over_budget_warning(tmp_path):
    """Rule 24 (#40): --wiki-stats prints the size line — the char count and
    the floored percentage of budget — and, when over budget, one WARNING
    line carrying the rule-29 warning text (the summarize-and-prune
    instruction)."""
    wiki_path = tmp_path / "wiki" / "cluster-wiki.md"
    wiki_path.parent.mkdir(parents=True)
    content = big_wiki()
    wiki_path.write_text(content)
    r = run_stats(wiki_path)
    assert r.returncode == 0, r.stderr[-400:]
    out = r.stdout
    n, p = len(content), int(100 * len(content) / patrol.WIKI_CHAR_BUDGET)
    assert f"size: {n} chars" in out
    assert f"({p}% of budget)" in out
    assert "WARNING" in out and "over" in out
    assert "summarize" in out and "prune" in out


def test_stats_mode_size_line_under_budget_without_warning(tmp_path):
    """Rule 24: within budget the size line still prints (the measure, not
    just the alarm) and no warning fires."""
    wiki_path = tmp_path / "wiki" / "cluster-wiki.md"
    wiki_path.parent.mkdir(parents=True)
    content = build_wiki()
    wiki_path.write_text(content)
    r = run_stats(wiki_path)
    assert r.returncode == 0, r.stderr[-400:]
    assert f"size: {len(content)} chars" in r.stdout
    assert "% of budget" in r.stdout
    assert "WARNING" not in r.stdout


# ---------------------------------------------------------- the report contract (rule 5 amendment)

def test_report_contract_states_working_set_expectations():
    """reporting-notifications.md rule 5 (#40): the contract tells the model
    the wiki is a working set (git history is the archive), states the soft
    budget in characters and the anomaly-log keep budget, and instructs
    summarize-and-prune over accumulation."""
    contract = patrol.REPORT_CONTRACT
    assert "working set" in contract, \
        "REPORT_CONTRACT must state the working-set principle"
    assert "summarize" in contract and "prune" in contract, \
        "REPORT_CONTRACT must instruct summarize-and-prune"
    assert str(patrol.WIKI_CHAR_BUDGET) in contract, \
        "REPORT_CONTRACT must state the soft budget in characters"
    assert f"last {patrol.WIKI_ANOMALY_KEEP} resolved/expired" in contract, (
        "REPORT_CONTRACT must state the anomaly-log keep budget — the bare"
        " number is a substring of the char budget, so the phrase is pinned")
    assert "rather than deleting history" not in contract, (
        "the contract's old closing sentence must be reworded per the rule 5"
        " amendment — prose entries are expired by the model, closed"
        " Anomaly-log history is pruned by the patroller")
