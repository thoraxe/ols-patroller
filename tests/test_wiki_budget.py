"""Wiki length budget — contract tests for #40 (plus #85's and #111's prose contracts).

These tests pin the public surface of patrol.py's planned working-set
budgets:

    WIKI_ANOMALY_KEEP / WIKI_CHAR_BUDGET             # rules 28-29 constants
    compact_anomaly_log(content, keep)                # rule 28 (compaction pass)
        -> (rewritten content, [pruned entry ids])
    WIKI_ANOMALY_ENTRY_CHARS                          # rule 28a constant
    truncate_closed_entries(content, cap)             # rule 28a (character cap)
        -> (rewritten content, [truncated entry ids])
    wiki_budget_warnings(content) -> list[str]        # rule 29 (soft budget)
    wiki_stats: char_count / over_budget keys         # rule 25 amendment
    --wiki-stats: the size line + over-budget warning # rule 24 amendment
    REPORT_CONTRACT: working-set expectations         # reporting rule 5 amendment
    REPORT_CONTRACT: no-patrol-history clauses         # rule 5's #85 clauses
    REPORT_CONTRACT: plain-prose clause                # rule 5's #111 clause
    SYSTEM_PROMPT: current-state clause                # patrol-engine rule 3f

They fail before implementation (AttributeError on the missing symbols,
AssertionError on the missing surfaces) and must pass after.
Contract: .ai/spec/what/wiki-memory.md rules 28-29 (and rule 28a for the
closed-entry character bound, rule 29b for the no-patrol-history ban, rule 13a for
the plain-prose rule) plus the rule 24/25 amendments
[PLANNED: #40] [PLANNED: #85] [PLANNED: #86] [PLANNED: #111].

Scope note: like test_wiki_expiry.py, these tests pin the pure passes. The
wiring into the patrol tail (expire -> compact -> truncate -> carry -> stamp,
the prune and truncation console lines, the stderr warning, the repair-pass
inclusion, the minimal-write exclusion) is verified end-to-end by the
fake-model e2e in test_patrol_deepagents.py.

Run: uv run --group dev pytest tests/ -q
"""
import asyncio
import subprocess
import sys
from pathlib import Path

import patrol
from support import patrol_free_env
from langchain_core.messages import HumanMessage


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


def long_closed(i, lines=12, width=100):
    """A closed Anomaly-log entry whose continuation is far over the
    closed-entry character cap — the #86 evidence, one ~820-character entry."""
    body = "\n".join(f"  line {n:02d} " + "x" * width
                     for n in range(1, lines + 1))
    return entry(id=f"A-{i}", status="expired", recheck=None, prose=body)


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
    """The stats mode as a human runs it — a subprocess with the real
    environment minus `PATROL_*` (rule 23: the mode is fully offline).

    Output is decoded as UTF-8: the process pins the console to UTF-8/LF
    (#77, rule 9b). See `test_wiki_stats.run_stats` for the env rationale.
    """
    r = subprocess.run(
        [sys.executable, str(PATROL), "--wiki-stats", "--wiki", str(wiki), *extra],
        capture_output=True, timeout=240, env=patrol_free_env())
    return subprocess.CompletedProcess(r.args, r.returncode,
                                       r.stdout.decode("utf-8"),
                                       r.stderr.decode("utf-8", "replace"))


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


# ------------------------------------------ the closed-entry character cap (#86, rule 28a)

def test_anomaly_entry_cap_constant_surface():
    """Rule 28a (#86): the closed-entry character cap is a pinned constant, like
    the keep budget — retuning it is a spec change, not a config knob."""
    assert patrol.WIKI_ANOMALY_ENTRY_CHARS == 400


def _continuation(content):
    """[(entry id, [continuation lines])] of the Anomaly log's items."""
    return [(pairs_of(m)["id"], cont)
            for m, cont in entry_blocks(section_body(content, "Anomaly log"))]


def test_truncate_keeps_the_metadata_line_head_and_tail():
    """Rule 28a: an over-cap closed entry keeps its metadata line byte for
    byte, whole lines from the top and the bottom, and one marker line where
    the middle lines stood; the compacted wiki still validates."""
    wiki = build_wiki(anomaly=[long_closed(1)])
    meta_before = entry_blocks(section_body(wiki, "Anomaly log"))[0][0]
    out, truncated = patrol.truncate_closed_entries(wiki, cap=400)
    assert truncated == ["A-1"]
    blocks = entry_blocks(section_body(out, "Anomaly log"))
    assert len(blocks) == 1, "truncation must never grow or split an entry"
    meta, cont = blocks[0]
    assert meta == meta_before, "the metadata line carries the clocks, never cut"
    kept = [line for line in cont if "_truncated:" not in line]
    assert sum(len(line) for line in kept) <= 400
    assert kept[0].strip().startswith("line 01")   # head kept from the top
    assert kept[-1].strip().startswith("line 12")  # tail kept from the bottom
    assert kept[0].startswith("  ") and kept[-1].startswith("  "), \
        "a kept line never loses its indentation (rule 28a)"
    assert "line 06" not in out                    # a middle line was dropped
    marker = [line for line in cont if "_truncated:" in line]
    assert len(marker) == 1
    assert marker[0].startswith("  _truncated: ")
    assert "git history has the full entry" in marker[0]
    assert patrol.validate_wiki(out) == []


def test_truncate_marker_counts_the_dropped_characters():
    """Rule 28a: the marker names how many characters the pass dropped, in
    the cap's unit (line lengths, newlines excluded)."""
    lines = [f"  line {n:02d} " + "x" * 100 for n in range(1, 13)]
    wiki = build_wiki(anomaly=[entry(id="A-1", status="expired", recheck=None,
                                     prose="\n".join(lines))])
    out, truncated = patrol.truncate_closed_entries(wiki, cap=400)
    assert truncated == ["A-1"]
    kept = [line for line in out.split("\n")
            if line.startswith("  line ")]
    dropped = sum(len(line) for line in lines if line not in kept)
    assert dropped > 0
    assert f"_truncated: {dropped} characters elided" in out
    assert sum(len(line) for line in kept) <= 400


def test_truncate_leaves_a_short_closed_entry_unchanged():
    """Rule 28a: within the cap nothing is dropped and the content returns
    unchanged, byte for byte (rule 28's minimal-rewrite guarantee)."""
    wiki = build_wiki(anomaly=[closed(1), closed(2)])
    out, truncated = patrol.truncate_closed_entries(wiki, cap=400)
    assert truncated == []
    assert out == wiki


def test_truncate_is_idempotent():
    """Rule 28a: the pass ignores its own marker line when it measures, so a
    second pass over a truncated wiki changes nothing. The fixture keeps 360
    of the 400 characters, so counting the marker (~68) would push it over
    the cap and a marker-counting pass would truncate again."""
    wiki = build_wiki(anomaly=[long_closed(1, width=110)])
    once, first = patrol.truncate_closed_entries(wiki, cap=400)
    assert first == ["A-1"]
    kept = [line for line in once.split("\n") if line.startswith("  line ")]
    assert sum(len(line) for line in kept) == 360
    assert len(kept) < 12
    twice, truncated = patrol.truncate_closed_entries(once, cap=400)
    assert truncated == []
    assert twice == once


def test_truncate_never_touches_watching_entries_or_the_watch_list():
    """Rule 28a: `watching` entries keep their whole hypothesis and a closed
    entry placed in the Watch-list is the model's placement to reconsider —
    neither is truncated (rule 28's scope)."""
    live = entry(id="A-live", status="watching", recheck=None,
                 prose="  " + "y" * 900)
    watch = entry(id="W-1", status="resolved",
                  prose="  " + "z" * 900)
    wiki = build_wiki(watch=[watch], anomaly=[live])
    out, truncated = patrol.truncate_closed_entries(wiki, cap=400)
    assert truncated == []
    assert out == wiki


def test_truncate_preserves_everything_outside_truncated_entries():
    """Rule 28a: a minimal rewrite — the frontmatter, the prose sections, the
    Watch-list, non-item prose in the Anomaly log, and every entry under the
    cap stay byte for byte; only the truncated entry's middle lines vanish."""
    note = "Closed history beyond the cap lives in git."
    wiki = build_wiki(watch=[entry(id="W-1")],
                      anomaly=[note, long_closed(1), closed(2)])
    out, truncated = patrol.truncate_closed_entries(wiki, cap=400)
    assert truncated == ["A-1"]
    assert split_wiki(out)[0] == split_wiki(wiki)[0]  # frontmatter untouched
    for section in ("Topology", "Baselines", "Open questions", "Watch-list"):
        assert section_body(out, section) == section_body(wiki, section)
    al = section_body(out, "Anomaly log")
    assert note in al                       # non-item prose never touched
    before = dict(_continuation(wiki))
    after = dict(_continuation(out))
    assert set(after) == {"A-1", "A-2"}
    assert after["A-2"] == before["A-2"]    # under the cap: byte for byte
    assert patrol.validate_wiki(out) == []


def test_truncate_is_fence_aware():
    """Rule 28a: fenced list-looking lines are prose — never truncated, never
    counted as entries (the validator's scanning discipline, rule 11)."""
    fenced = "```\n- id: FENCED · status: expired · fenced prose, not an entry\n```"
    wiki = build_wiki(anomaly=[closed(1), fenced])
    out, truncated = patrol.truncate_closed_entries(wiki, cap=400)
    assert truncated == []
    assert out == wiki
    assert fenced in section_body(out, "Anomaly log")


def test_truncate_keeps_a_fence_balanced():
    """Rule 28a: a fenced block inside an entry is kept or dropped whole, so a
    truncated entry never leaves an open fence (which would swallow the rest
    of the document and fail rule 15)."""
    prose = "\n".join(
        ["  " + "a" * 100] * 6
        + ["  ```", "  fenced block line", "  ```"]
        + ["  " + "b" * 100] * 6)
    wiki = build_wiki(anomaly=[entry(id="A-1", status="expired", recheck=None,
                                     prose=prose)])
    out, truncated = patrol.truncate_closed_entries(wiki, cap=400)
    assert truncated == ["A-1"]
    assert out.count("```") % 2 == 0, "the wiki's fences stay balanced"
    assert patrol.validate_wiki(out) == []


def test_truncate_keeps_a_fitted_fence_whole():
    """Rule 28a: a fenced block that fits its share is kept whole, markers
    included — the pass drops blocks, never half of one."""
    prose = "\n".join(["  " + "a" * 20, "  ```", "  fence line", "  ```",
                       "  " + "b" * 20, "  " + "c" * 20, "  " + "d" * 20])
    wiki = build_wiki(anomaly=[entry(id="A-1", status="expired", recheck=None,
                                     prose=prose)])
    out, truncated = patrol.truncate_closed_entries(wiki, cap=100)
    assert truncated == ["A-1"]
    assert "```" in out and out.count("```") == 2
    assert "fence line" in out
    assert "b" * 20 not in out, "a middle line is dropped"
    assert "d" * 20 in out, "the tail line is kept"
    assert patrol.validate_wiki(out) == []


def test_truncate_counts_a_fenced_block_toward_the_share():
    """Rule 28a: a fenced block's lines count toward the share like any other
    line, so a block that does not fit is dropped whole and the pass stops at
    it — no free fence, no orphan fence marker."""
    prose = "\n".join(["  " + "a" * 40, "  ```", "  " + "b" * 30, "  ```",
                       "  " + "c" * 40])
    wiki = build_wiki(anomaly=[entry(id="A-1", status="expired", recheck=None,
                                     prose=prose)])
    out, truncated = patrol.truncate_closed_entries(wiki, cap=100)
    assert truncated == ["A-1"]
    assert "```" not in out, "the fence block does not fit: dropped whole"
    assert "b" * 30 not in out and "c" * 40 not in out
    assert "a" * 40 in out, "the head line before the block is kept"
    assert patrol.validate_wiki(out) == []


def test_truncate_cap_zero_leaves_only_the_marker():
    """Rule 28a: cap = 0 is legal — every closed entry keeps its metadata line
    and the marker alone, while `watching` entries keep their prose."""
    live = entry(id="A-live", status="watching", recheck=None,
                 prose="  still live, never truncated")
    wiki = build_wiki(anomaly=[long_closed(1), live])
    out, truncated = patrol.truncate_closed_entries(wiki, cap=0)
    assert truncated == ["A-1"]
    assert [entry_id for entry_id, _ in _continuation(out)] == ["A-1", "A-live"]
    cont = dict(_continuation(out))["A-1"]
    assert [line for line in cont if "_truncated:" not in line] == []
    assert "still live, never truncated" in out
    assert patrol.validate_wiki(out) == []


def test_truncate_returns_ids_in_document_order():
    """Rule 28a: the returned ids follow document order, like rule 28's
    pruned ids."""
    wiki = build_wiki(anomaly=[long_closed(3), closed(1), long_closed(2)])
    out, truncated = patrol.truncate_closed_entries(wiki, cap=400)
    assert truncated == ["A-3", "A-2"]
    assert patrol.validate_wiki(out) == []


def test_truncate_keeps_the_auto_expired_annotation():
    """Rule 28a's head+tail split: the rule-21 `auto-expired` annotation is
    the entry's last line, so the tail keeps it — the reason the pass is not
    head-only."""
    long_watch = entry(
        id="W-1", last_verified="2026-10-01",
        prose="\n".join(f"  hypothesis line {n} " + "x" * 100
                        for n in range(1, 8)))
    new = build_wiki(watch=[long_watch])
    old = build_wiki(watch=[entry(id="W-1", last_verified="2026-10-01",
                                  verified_patrol=1)])
    expired, flips = patrol.expire_stale_entries(new, old, patrol_count=7,
                                                 stale_after=5)
    assert flips == [("W-1", 6)]
    out, truncated = patrol.truncate_closed_entries(expired, cap=400)
    assert truncated == ["W-1"]
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
    wiki_path.write_text(content, encoding="utf-8", newline="\n")
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
    wiki_path.write_text(content, encoding="utf-8", newline="\n")
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


# reporting-notifications.md rule 5's #86 clause, verbatim: the model learns
# the closed-entry character cap, so it writes closed entries tight instead of
# delegating the cut to the process (wiki-memory.md rule 28a).
def closed_entry_budget_clause(cap):
    """The rule 5 clause with the cap value the constant carries — the number
    is part of the contract text, so the clause moves when the cap moves."""
    return (
        "An Anomaly-log entry with status resolved or expired keeps at most "
        f"{cap} characters of prose. The patroller truncates a longer entry on "
        "whole lines and leaves a marker line. Git history has the full entry."
    )


def test_report_contract_states_the_closed_entry_character_budget():
    """reporting-notifications.md rule 5 (#86): the working-set text also
    states the closed-entry character cap verbatim, with the number the
    constant carries — taught as well as enforced."""
    flat = " ".join(patrol.REPORT_CONTRACT.split())
    clause = closed_entry_budget_clause(patrol.WIKI_ANOMALY_ENTRY_CHARS)
    assert clause in flat, (
        "REPORT_CONTRACT must carry the closed-entry character-cap clause with"
        " the current WIKI_ANOMALY_ENTRY_CHARS value")


# ------------------------------- the no-patrol-history contract (#85, rule 29b)

# reporting-notifications.md rule 5's #85 clauses, verbatim. The wiki records
# current state, git history is the record, the intro stays short, and a
# level-3 status block is legal while a sixth level-2 section is not.
NO_PATROL_HISTORY_CONTRACT_CLAUSES = (
    'The wiki records current state only — never write patrol-history prose: '
    'no per-patrol headline log, no History: list, no "as of patrol #N" '
    'chronology. Git history is the record of what changed when.',
    "Keep the opening prose to a few lines: what the wiki is and the dominant "
    "standing severity.",
    "A ### status block after the Anomaly log is legal; a sixth ## section is "
    "not.",
)

# patrol-engine.md rule 3f's clause, verbatim — the system prompt's
# hard-rules / patrol-order teaching.
NO_PATROL_HISTORY_PROMPT_CLAUSE = (
    "Record current state only. The wiki is not a patrol log: never write a "
    'per-patrol headline, a History: list, or "as of patrol #N" chronology — '
    "git history already records what changed when. Keep the opening prose to "
    "a few lines."
)


def test_report_contract_bans_patrol_history_prose():
    """reporting-notifications.md rule 5 (#85): the working-set item carries
    the three no-patrol-history sentences verbatim — the contract is where
    the model learns what must never go into the wiki (wiki-memory.md rule
    29b)."""
    flat = " ".join(patrol.REPORT_CONTRACT.split())
    for clause in NO_PATROL_HISTORY_CONTRACT_CLAUSES:
        assert clause in flat, f"REPORT_CONTRACT must carry {clause!r}"


def test_system_prompt_teaches_current_state_not_patrol_history():
    """patrol-engine.md rule 3f (#85): the system prompt carries the
    current-state clause verbatim — the hard-rules / patrol-order block is
    where the model is told that git history is the record, the structured
    contract only repeats it."""
    flat = " ".join(patrol.SYSTEM_PROMPT.split())
    assert NO_PATROL_HISTORY_PROMPT_CLAUSE in flat, (
        "the system prompt must tell the model to record current state only"
        " and that git history is the record of what changed when")
    assert "per-patrol headline" in flat
    assert "History: list" in flat
    assert 'as of patrol #N' in flat
    # rule 3f's placement: the clause rides the standing-instruction block,
    # not the closing "write your final summary" prose — it is a rule about
    # what the wiki may contain, not about how to phrase the summary
    assert flat.index("Record current state only") < flat.index("When you are DONE exploring"), (
        "the clause belongs before the closing summary guidance — it is a"
        " wiki-content rule, not summary phrasing")


# ------------------------------------- the plain-prose contract (#111, rule 5)

# reporting-notifications.md rule 5's #111 clause, verbatim: the wiki's free
# prose uses plain English, one idea per sentence, the active voice, simple
# tenses, one word for one thing, and the same wording on the next patrol. The
# machine fields are named as out of scope in the same clause (wiki-memory.md
# rule 13a).
PLAIN_PROSE_CONTRACT_CLAUSE = (
    "Write the wiki's free prose in plain English. Use short sentences. Use one "
    "idea per sentence. Use the active voice. Use the simple present, past, or "
    "future tense. Do not use idioms or slang. Use one word for one thing. Use "
    "the same word on the next patrol instead of a synonym. Stable wording "
    "keeps the wiki readable and reduces churn. This clause covers free prose "
    "only. Frontmatter, entry metadata lines, recheck commands, code, and the "
    "skeleton stay exactly as they are."
)


def test_report_contract_asks_for_plain_wiki_prose():
    """reporting-notifications.md rule 5 (#111): the contract carries the
    plain-prose clause verbatim. The clause asks for free prose only, names
    every machine field it excludes, and asks for the same wording on the
    next patrol (wiki-memory.md rule 13a). Teaching only: no validation rule
    rides with it."""
    flat = " ".join(patrol.REPORT_CONTRACT.split())
    assert PLAIN_PROSE_CONTRACT_CLAUSE in flat, (
        "REPORT_CONTRACT must carry the #111 plain-prose clause verbatim")
    # rule 13a: the ask lives on the contract only — the exact clause must not
    # appear in the system prompt (a paraphrase is outside this test's scope)
    prompt = " ".join(patrol.SYSTEM_PROMPT.split())
    assert PLAIN_PROSE_CONTRACT_CLAUSE not in prompt, (
        "the plain-prose ask belongs to REPORT_CONTRACT only (rule 13a)")


# ------------------------------------ the model-facing size note (#67, rule 29a)

def test_size_note_within_budget_names_count_budget_headroom():
    """Rule 29a (#67): within budget the note names the content's character
    count, the soft budget, the floored percentage, and the headroom — plus
    the do-not-grow-without-pruning instruction."""
    content = build_wiki()
    count, budget = len(content), patrol.WIKI_CHAR_BUDGET
    share = int(100 * count / budget)
    assert patrol.wiki_size_note(content) == (
        f"Current wiki size: {count} characters ({share}% of the "
        f"{budget}-character soft budget) — {budget - count} characters "
        "of headroom. Do not grow it without pruning something else.")
    assert "OVER budget" not in patrol.wiki_size_note(content)


def test_size_note_over_budget_names_the_excess_and_prune_target():
    """Rule 29a (#67): over budget the note names the exact excess and a
    named prune target — the number the model must shrink by."""
    content = big_wiki(target=25_000)
    count, budget = len(content), patrol.WIKI_CHAR_BUDGET
    excess = count - budget
    share = int(100 * count / budget)
    assert patrol.wiki_size_note(content) == (
        f"Current wiki size: {count} characters ({share}% of the "
        f"{budget}-character soft budget) — {excess} characters OVER "
        f"budget. Prune at least {excess} characters: rewrite existing "
        "prose tighter and drop what no longer earns its place.")


def test_size_note_empty_content_and_budget_parameter():
    """Rule 29a (#67): empty content renders nothing (no note); the budget
    parametrizes the pure function so tests are not pinned to the constant."""
    assert patrol.wiki_size_note("") == ""
    over = patrol.wiki_size_note("x" * 120, budget=100)
    assert "120 characters" in over and "(120% of the 100-character" in over
    assert "20 characters OVER budget" in over
    under = patrol.wiki_size_note("x" * 10, budget=100)
    assert "10 characters" in under and "(10% of the 100-character" in under
    assert "90 characters of headroom" in under
    # the boundary is within budget (rule 29's strict `count > budget`)
    at_budget = patrol.wiki_size_note("x" * 100, budget=100)
    assert "0 characters of headroom" in at_budget
    assert "OVER budget" not in at_budget


def test_size_note_reads_the_default_budget_at_call_time(monkeypatch):
    """Rule 29a (#67): like the #40 budgets, WIKI_CHAR_BUDGET is read at call
    time — monkeypatching it changes the note."""
    monkeypatch.setattr(patrol, "WIKI_CHAR_BUDGET", 100)
    note = patrol.wiki_size_note("x" * 120)
    assert "100-character soft budget" in note
    assert "20 characters OVER budget" in note


def test_report_contract_names_the_consequence_chain_and_shrink_rule():
    """reporting-notifications.md rule 5 (#67): the contract names the
    consequence chain and requires the returned wiki to be smaller than the
    current one when the size line says over budget."""
    contract = patrol.REPORT_CONTRACT
    assert "size line" in contract
    assert "over budget" in contract
    assert "gateway" in contract and "request ceiling" in contract
    assert "next patrol" in contract and "re-read" in contract
    assert "must be smaller than the current wiki" in contract
    assert "prune at least the stated excess" in contract


class _StubShapeModel:
    """A shape_report stub: with_structured_output returns a runnable whose
    ainvoke serves scripted results and records every prompt it received."""

    def __init__(self, results):
        self.results = list(results)
        self.prompts = []

    def with_structured_output(self, schema, method=None, include_raw=None):
        stub = self

        class _Structured:
            async def ainvoke(self, msgs):
                stub.prompts.append(list(msgs))
                served = stub.results.pop(0)
                if isinstance(served, Exception):
                    raise served
                return served

        return _Structured()


def _report(wiki):
    return patrol.PatrolReport(findings=[], notifications=[], updated_wiki=wiki)


def _prompt(stub):
    return stub.prompts[0][-1]["content"]


def test_shape_pass_appends_size_note_after_the_contract_for_on_disk_wiki():
    """Rule 29a (#67): the report pass appends the on-disk wiki's size note
    after REPORT_CONTRACT, a blank line between — the model sees the number
    it is being asked to shrink."""
    old = build_wiki()
    stub = _StubShapeModel([_report("full wiki")])
    result = asyncio.run(patrol.shape_report(
        stub, [HumanMessage("mission")], budget_cut=False, current_wiki=old))
    assert result.updated_wiki == "full wiki"
    prompt = _prompt(stub)
    note = patrol.wiki_size_note(old)
    assert note and prompt.endswith("\n\n" + note)
    assert prompt.count("Current wiki size:") == 1


def test_shape_pass_no_size_note_without_current_wiki():
    """Rule 29a (#67): empty content (no wiki in play) renders no note —
    existing callers that pass no `current_wiki` are unchanged."""
    stub = _StubShapeModel([_report("full wiki")])
    asyncio.run(patrol.shape_report(
        stub, [HumanMessage("mission")], budget_cut=False))
    assert "Current wiki size:" not in _prompt(stub)


def test_validation_repair_size_note_describes_the_rejected_wiki():
    """Rule 29a (#67): the validation-repair pass reports the rejected wiki
    it is asked to fix — not the on-disk wiki."""
    on_disk = build_wiki()
    rejected = big_wiki(target=25_000)
    stub = _StubShapeModel([_report("fixed wiki")])
    asyncio.run(patrol.shape_report(
        stub, [HumanMessage("mission")], budget_cut=False,
        current_wiki=on_disk, repair=(rejected, ["bogus_key: 1"])))
    prompt = _prompt(stub)
    assert patrol.wiki_size_note(rejected) in prompt
    assert patrol.wiki_size_note(on_disk) not in prompt


def test_shape_pass_size_note_persists_on_the_retry():
    """Rule 29a (#67): the size note describes the wiki-in-play, not the
    response — it appears exactly once on the corrective retry too."""
    old = build_wiki()
    stub = _StubShapeModel([RuntimeError("nope"), _report("full wiki")])
    result = asyncio.run(patrol.shape_report(
        stub, [HumanMessage("mission")], budget_cut=False, current_wiki=old))
    assert result.updated_wiki == "full wiki"
    note = patrol.wiki_size_note(old)
    assert len(stub.prompts) == 2
    for prompt in stub.prompts:
        content = prompt[-1]["content"]
        assert content.endswith("\n\n" + note)
        assert content.count("Current wiki size:") == 1
    assert "not a valid PatrolReport" in stub.prompts[1][-1]["content"]


def test_wiki_repair_pass_carries_no_size_note():
    """Rule 29a (#67): the rule-7d wiki-repair pass carries no size note —
    its ask is a minimal merge preserving the old wiki (bar the
    patrol-history prose rule 29b drops), so a prune instruction would
    contradict it."""
    stub = _StubShapeModel([_report("merged wiki")])
    asyncio.run(patrol.shape_report(
        stub, [HumanMessage("mission")], budget_cut=False,
        current_wiki=big_wiki(target=25_000),
        wiki_repair=("OLD WIKI", [], [])))
    assert "Current wiki size:" not in _prompt(stub)
