"""Stale-entry expiry — contract tests for #7.

These tests pin the public surface of patrol.py's planned expiry pass:

    STALE_AFTER                                                         # rule 21 config
    expire_stale_entries(content, old_wiki, patrol_count, stale_after)  # rules 20-22
        -> (rewritten content, [(entry id, age), ...])

plus the report contract's staleness-clock instructions
(reporting-notifications.md rule 5). They fail before implementation
(AttributeError on the missing symbols, AssertionError on the missing
surfaces) and must pass after.
Contract: .ai/spec/what/wiki-memory.md rules 20-22 [PLANNED: #7].

Scope note: like test_wiki_schema.py, these tests pin the pure pass (clock
stamping + flip). The wiring into the patrol tail (expire -> stamp -> write
-> commit, the console log line) is verified end-to-end by the fake-model
e2e in test_patrol_deepagents.py.

Run: uv run --group dev pytest tests/ -q
"""
from pathlib import Path

import patrol


SECTIONS = ["Topology", "Baselines", "Watch-list", "Open questions", "Anomaly log"]

DOT = "·"  # U+00B7 middle dot — the metadata-line separator (rule 12)


def meta_line(id="W-1", status="watching", first_seen="2026-10-01",
              last_verified="2026-10-10", source="kubectl get nodes",
              confidence="high", recheck="get nodes", verified_patrol=None):
    """A well-formed entry metadata line (rules 12 + 12a; #7's verified_patrol
    pair is optional here — the model may carry, drop, or mangle it)."""
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


def build_wiki(watch=(), anomaly=(), patrol_count=3):
    """Assemble a skeleton-valid wiki (rules 10-12a) from entry blocks."""
    wiki = _assemble(watch, anomaly, patrol_count)
    assert patrol.validate_wiki(wiki) == [], "test fixture must be skeleton-valid"
    return wiki


def _assemble(watch, anomaly, patrol_count):
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
    return "\n".join(parts)


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


def expire(new, old, P, N):
    """The expiry pass under test (rule 22 signature)."""
    return patrol.expire_stale_entries(new, old, patrol_count=P, stale_after=N)


# ------------------------------------------------------------ config surface (rule 21)

def test_stale_after_config_surface():
    """Rule 21: the threshold defaults to 5 and is configurable via
    PATROL_WIKI_STALE_AFTER (0 disables the flip)."""
    assert patrol.STALE_AFTER == 5
    source = Path(patrol.__file__).read_text()
    assert "PATROL_WIKI_STALE_AFTER" in source, \
        "the threshold must be configurable via PATROL_WIKI_STALE_AFTER"


# ------------------------------------------------------------ staleness clock (rule 20)

def test_new_entry_clock_stamped_at_current_write():
    """Rule 20: an id absent from the old wiki is verified by the patrol that
    wrote it (clock = P) — and the model's own verified_patrol guess is
    overwritten in place, never trusted, with nothing else on the line touched."""
    new = build_wiki(watch=[entry(id="W-9", last_verified="2026-10-14",
                                  verified_patrol=999)], patrol_count=7)
    old = build_wiki(watch=[entry(id="W-1")], patrol_count=7)
    out, flipped = expire(new, old, P=8, N=5)
    assert flipped == []
    (meta, _), = entry_blocks(section_body(out, "Watch-list"))
    assert meta == ("- id: W-9 · status: watching · first_seen: 2026-10-01"
                    " · last_verified: 2026-10-14 · source: kubectl get nodes"
                    " · confidence: high · recheck: get nodes"
                    " · verified_patrol: 8")


def test_clock_carried_when_last_verified_unchanged():
    """Rule 20: same id, same last_verified -> the old wiki's clock carries."""
    new = build_wiki(watch=[entry(id="W-1", last_verified="2026-10-10")])
    old = build_wiki(watch=[entry(id="W-1", last_verified="2026-10-10",
                                  verified_patrol=3)])
    out, flipped = expire(new, old, P=7, N=5)  # age 4 < 5: survives
    assert flipped == []
    meta = entry_blocks(section_body(out, "Watch-list"))[0][0]
    assert pairs_of(meta)["verified_patrol"] == "3"


def test_clock_resets_when_last_verified_advances():
    """Rule 20: a strictly later last_verified is the one verification signal —
    the clock resets to the current write even when it would otherwise flip."""
    new = build_wiki(watch=[entry(id="W-1", last_verified="2026-10-15")])
    old = build_wiki(watch=[entry(id="W-1", last_verified="2026-10-10",
                                  verified_patrol=2)])
    out, flipped = expire(new, old, P=9, N=5)  # carried would be age 7
    assert flipped == []
    meta = entry_blocks(section_body(out, "Watch-list"))[0][0]
    assert pairs_of(meta)["verified_patrol"] == "9"


def test_clock_grace_when_old_pair_missing_garbled_or_legacy():
    """Rule 20: no parseable verified_patrol in the old wiki -> P (migration
    grace: an upgrade never mass-expires) — for a missing pair, a garbled
    value, and a legacy prose-only old wiki."""
    new = build_wiki(watch=[entry(id="W-1", last_verified="2026-10-01")])

    old = build_wiki(watch=[entry(id="W-1", last_verified="2026-10-01")])
    out, flipped = expire(new, old, P=5, N=1)
    assert flipped == []
    assert pairs_of(entry_blocks(section_body(out, "Watch-list"))[0][0])[
        "verified_patrol"] == "5"

    old = build_wiki(watch=[entry(id="W-1", last_verified="2026-10-01",
                                  verified_patrol="not-a-number")])
    out, flipped = expire(new, old, P=5, N=1)
    assert flipped == []
    assert pairs_of(entry_blocks(section_body(out, "Watch-list"))[0][0])[
        "verified_patrol"] == "5"

    out, flipped = expire(new, "# Cluster Wiki\n\n## Topology\n- legacy prose\n",
                          P=5, N=1)
    assert flipped == []
    assert pairs_of(entry_blocks(section_body(out, "Watch-list"))[0][0])[
        "verified_patrol"] == "5"


def test_clock_keyed_across_sections_reopen_expires():
    """Rule 20: the clock is keyed by id across BOTH metadata sections —
    re-opening an old Anomaly-log entry as watching, without advancing
    last_verified, does not reset it (verification is the only reset)."""
    new = build_wiki(watch=[entry(id="A-1", last_verified="2026-10-01",
                                  recheck="get pods -A")])
    old = build_wiki(anomaly=[entry(id="A-1", status="expired",
                                    last_verified="2026-10-01",
                                    recheck="get pods -A", verified_patrol=2)])
    out, flipped = expire(new, old, P=8, N=5)  # carried clock 2 -> age 6
    assert flipped == [("A-1", 6)]
    assert pairs_of(entry_blocks(section_body(out, "Anomaly log"))[-1][0])[
        "status"] == "expired"


# ------------------------------------------------------------ expiry flip (rule 21)

def test_stale_watching_entry_flips_moves_and_annotates():
    """Rule 21, the core: a watching Watch-list entry whose age reached N is
    flipped to expired, moved verbatim (prose and sub-bullets included, recheck
    carried) to the END of the Anomaly log with the exact auto-expired
    annotation, its clock carried — and the flipped wiki still validates."""
    prose = "  worker-1 NotReady for 40d.\n  - sub-bullet prose, moves too"
    new = build_wiki(watch=[entry(id="W-1", last_verified="2026-10-01",
                                  prose=prose),
                            entry(id="W-2", last_verified="2026-10-14")])
    old = build_wiki(watch=[entry(id="W-1", last_verified="2026-10-01",
                                  verified_patrol=3),
                            entry(id="W-2", last_verified="2026-10-14",
                                  verified_patrol=7)])
    out, flipped = expire(new, old, P=8, N=5)

    assert flipped == [("W-1", 5)]  # (id, age) — age = P - verified_patrol

    # the Watch-list kept only the fresh entry, watching, clock carried
    wl = {pairs_of(m)["id"]: m for m, _ in entry_blocks(section_body(out, "Watch-list"))}
    assert set(wl) == {"W-2"}
    assert pairs_of(wl["W-2"])["status"] == "watching"
    assert pairs_of(wl["W-2"])["verified_patrol"] == "7"

    # W-1 landed at the end of the Anomaly log: expired, recheck carried,
    # clock carried, prose verbatim, annotation appended as the last line
    meta, cont = entry_blocks(section_body(out, "Anomaly log"))[-1]
    assert pairs_of(meta)["id"] == "W-1"
    assert pairs_of(meta)["status"] == "expired"
    assert pairs_of(meta)["recheck"] == "get nodes"
    assert pairs_of(meta)["verified_patrol"] == "3"
    assert cont == prose.split("\n") + [
        "  _auto-expired at patrol 8: unverified for 5 patrols"
        " (stale_after 5)_"]
    assert patrol.validate_wiki(out) == []


def test_age_below_threshold_survives():
    """Rule 21: age N-1 is not stale — the entry stays watching in the
    Watch-list with its carried clock."""
    new = build_wiki(watch=[entry(id="W-1", last_verified="2026-10-10")])
    old = build_wiki(watch=[entry(id="W-1", last_verified="2026-10-10",
                                  verified_patrol=4)])
    out, flipped = expire(new, old, P=8, N=5)  # age 4
    assert flipped == []
    meta = entry_blocks(section_body(out, "Watch-list"))[0][0]
    assert pairs_of(meta)["status"] == "watching"
    assert pairs_of(meta)["verified_patrol"] == "4"


def test_model_statuses_and_anomaly_log_entries_are_not_flipped():
    """Rule 21: only Watch-list watching entries flip — resolved/expired are
    the model's choices, and a watching entry in the Anomaly log (a deliberate
    model placement) is left alone. Clocks are still stamped on all of them."""
    def both(status):
        return (build_wiki(watch=[entry(id="W-1", status=status,
                                        last_verified="2026-10-01"),
                                  entry(id="W-2", status="watching",
                                        last_verified="2026-10-10")],
                           anomaly=[entry(id="A-9", status="watching",
                                          last_verified="2026-10-01",
                                          recheck="get pods -A")]),
                build_wiki(watch=[entry(id="W-1", status=status,
                                        last_verified="2026-10-01",
                                        verified_patrol=1),
                                  entry(id="W-2", status="watching",
                                        last_verified="2026-10-10",
                                        verified_patrol=1)],
                           anomaly=[entry(id="A-9", status="watching",
                                          last_verified="2026-10-01",
                                          recheck="get pods -A",
                                          verified_patrol=1)]))
    for status in ("resolved", "expired"):
        new, old = both(status)
        out, flipped = expire(new, old, P=9, N=1)  # ages 8 and 8 would flip
        assert flipped == [("W-2", 8)]  # only the Watch-list watching one
        wl = {pairs_of(m)["id"]: (pairs_of(m)["status"],
                                  pairs_of(m)["verified_patrol"])
              for m, _ in entry_blocks(section_body(out, "Watch-list"))}
        assert wl["W-1"] == (status, "1")  # untouched, clock carried
        al = {pairs_of(m)["id"]: (pairs_of(m)["status"],
                                  pairs_of(m)["verified_patrol"])
              for m, _ in entry_blocks(section_body(out, "Anomaly log"))}
        assert al["A-9"] == ("watching", "1")  # never flipped, clock stamped


def test_stale_after_zero_disables_flip_but_stamps_clocks():
    """Rule 21: N=0 disables the flip; the verified_patrol clock is still
    maintained, so re-enabling resumes with true history."""
    new = build_wiki(watch=[entry(id="W-1", last_verified="2026-10-01")])
    old = build_wiki(watch=[entry(id="W-1", last_verified="2026-10-01",
                                  verified_patrol=1)])
    out, flipped = expire(new, old, P=9, N=0)
    assert flipped == []
    meta = entry_blocks(section_body(out, "Watch-list"))[0][0]
    assert pairs_of(meta)["status"] == "watching"
    assert pairs_of(meta)["verified_patrol"] == "1"


def test_flip_appends_after_anomaly_content_and_keeps_placeholder():
    """Rule 21: the moved item lands at the END of the Anomaly log, after
    existing entries; existing content — a placeholder line included — is
    untouched (the process never adds or removes prose)."""
    new = build_wiki(watch=[entry(id="W-1", last_verified="2026-10-01")],
                     anomaly=[entry(id="A-1", status="expired",
                                    last_verified="2026-10-01",
                                    recheck="get pods -A")])
    old = build_wiki(watch=[entry(id="W-1", last_verified="2026-10-01",
                                  verified_patrol=1)],
                     anomaly=[entry(id="A-1", status="expired",
                                    last_verified="2026-10-01",
                                    recheck="get pods -A")])
    out, flipped = expire(new, old, P=7, N=5)  # age 6
    assert flipped == [("W-1", 6)]
    al = section_body(out, "Anomaly log")
    assert [pairs_of(m)["id"] for m, _ in entry_blocks(al)] == ["A-1", "W-1"]
    assert patrol.validate_wiki(out) == []

    # and into a placeholder-only Anomaly log the placeholder stays — and a
    # Watch-list emptied by the flip keeps its heading, itemless (rule 11)
    new = build_wiki(watch=[entry(id="W-1", last_verified="2026-10-01")])
    old = build_wiki(watch=[entry(id="W-1", last_verified="2026-10-01",
                                  verified_patrol=1)])
    out, flipped = expire(new, old, P=6, N=5)
    assert flipped == [("W-1", 5)]
    al = section_body(out, "Anomaly log")
    assert "_(empty)_" in al
    assert [pairs_of(m)["id"] for m, _ in entry_blocks(al)] == ["W-1"]
    wl = section_body(out, "Watch-list")
    assert "## Watch-list" in out and entry_blocks(wl) == []
    assert patrol.validate_wiki(out) == []


def test_fenced_lines_are_prose_to_the_expiry_pass():
    """Rule 22: the pass scans with the validator's fence awareness — a
    list-looking line inside a fenced block is not an entry (no clock stamped,
    never flips), and a moved item is appended at the section's end, outside
    any fence."""
    fenced = "```\n- id: FENCED · status: watching · fenced prose, not an entry\n```"
    new = build_wiki(watch=[entry(id="W-1", last_verified="2026-10-01")
                            + "\n\n" + fenced],
                     anomaly=[fenced.replace("FENCED", "ALSO-FENCED")])
    old = build_wiki(watch=[entry(id="W-1", last_verified="2026-10-01",
                                  verified_patrol=1)])
    out, flipped = expire(new, old, P=6, N=5)
    assert flipped == [("W-1", 5)]

    # the fenced list-looking lines are untouched — no clock stamped on them
    assert fenced in section_body(out, "Watch-list")
    assert fenced.replace("FENCED", "ALSO-FENCED") in section_body(out, "Anomaly log")

    # the moved item is the section's last block, after the closing fence
    al = section_body(out, "Anomaly log")
    blocks = entry_blocks(al)
    assert [pairs_of(m)["id"] for m, _ in blocks] == ["W-1"]
    assert al.rindex("```") < al.index("- id: W-1")
    assert patrol.validate_wiki(out) == []


def test_flip_into_unclosed_fence_appends_at_section_end():
    """Rule 21's pathological shape: an Anomaly log that ends inside an
    unclosed code fence (valid only as the last section) still receives the
    moved item at the section's end — inside the fence. Validity and history
    are preserved; machine-readability of that one item is not (rule 11
    makes fenced lines prose)."""
    unclosed = "```\n- id: FENCED · status: watching · fenced prose, not an entry"
    new = build_wiki(watch=[entry(id="W-1", last_verified="2026-10-01")],
                     anomaly=[unclosed])
    old = build_wiki(watch=[entry(id="W-1", last_verified="2026-10-01",
                                  verified_patrol=1)])
    out, flipped = expire(new, old, P=6, N=5)
    assert flipped == [("W-1", 5)]
    al = section_body(out, "Anomaly log")
    assert al.rindex("```") < al.index("- id: W-1")  # appended inside the fence
    assert "auto-expired at patrol 6" in al
    assert patrol.validate_wiki(out) == []


def test_minimal_rewrite_outside_the_contract():
    """Rule 22: aside from verified_patrol pairs (replaced in place or
    appended as the final pair), flipped status/location, and annotations,
    the body is preserved byte-for-byte — and the frontmatter is not the
    expiry pass's to touch (the stamp owns it, rule 17)."""
    new = build_wiki(watch=[entry(id="W-1", last_verified="2026-10-01"),
                            entry(id="W-2", last_verified="2026-10-14")],
                     anomaly=[entry(id="A-1", status="expired",
                                    last_verified="2026-10-01",
                                    recheck="get pods -A")])
    old = build_wiki(watch=[entry(id="W-1", last_verified="2026-10-01",
                                  verified_patrol=1),
                            entry(id="W-2", last_verified="2026-10-14",
                                  verified_patrol=6)],
                     anomaly=[entry(id="A-1", status="expired",
                                    last_verified="2026-10-01",
                                    recheck="get pods -A")])
    out, flipped = expire(new, old, P=7, N=5)  # W-1 age 6 flips; W-2 age 1 stays
    assert flipped == [("W-1", 6)]

    assert split_wiki(out)[0] == split_wiki(new)[0]  # frontmatter untouched
    for section in ("Topology", "Baselines", "Open questions"):
        assert section_body(out, section) == section_body(new, section)
    # an untouched entry's line differs from the model's only by the
    # appended (carried) clock pair; its prose is byte-identical
    (meta, cont), = [b for b in entry_blocks(section_body(out, "Watch-list"))
                     if pairs_of(b[0])["id"] == "W-2"]
    assert meta == (meta_line(id="W-2", last_verified="2026-10-14")
                    + " · verified_patrol: 6")
    assert cont == ["  worker-1 NotReady for 40d — free prose hypothesis."]


# ------------------------------------------------------------ report contract (rule 5)

def test_multiple_flips_reported_in_watch_list_order():
    """Rule 22: the flipped (id, age) list is in Watch-list item order, and
    multiple stale entries all move, preserving their relative order."""
    new = build_wiki(watch=[entry(id="W-1", last_verified="2026-10-01"),
                            entry(id="W-2", last_verified="2026-10-02"),
                            entry(id="W-3", last_verified="2026-10-14")])
    old = build_wiki(watch=[entry(id="W-1", last_verified="2026-10-01",
                                  verified_patrol=1),
                            entry(id="W-2", last_verified="2026-10-02",
                                  verified_patrol=2),
                            entry(id="W-3", last_verified="2026-10-14",
                                  verified_patrol=7)])
    out, flipped = expire(new, old, P=7, N=5)
    assert flipped == [("W-1", 6), ("W-2", 5)]      # Watch-list order
    assert [pairs_of(m)["id"] for m, _ in
            entry_blocks(section_body(out, "Anomaly log"))] == ["W-1", "W-2"]
    assert [pairs_of(m)["id"] for m, _ in
            entry_blocks(section_body(out, "Watch-list"))] == ["W-3"]
    assert patrol.validate_wiki(out) == []


def test_report_contract_states_the_clock_rules():
    """reporting-notifications.md rule 5: the contract tells the model the
    verified_patrol pair is process-stamped (never computed by the model),
    that last_verified advances only on actual re-verification (the one
    signal the clock reads), and that unverified watching entries are
    auto-expired."""
    contract = patrol.REPORT_CONTRACT
    assert "verified_patrol" in contract, \
        "REPORT_CONTRACT must name the verified_patrol pair"
    assert "actually re-verified" in contract, \
        "REPORT_CONTRACT must tie advancing last_verified to actual re-verification"
    assert "auto-expired" in contract, \
        "REPORT_CONTRACT must state the auto-expiry consequence"
