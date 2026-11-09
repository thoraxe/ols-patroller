"""Notification dedup — contract tests for #35.

These tests pin the state-change notification contract in
`.ai/spec/what/reporting-notifications.md` rules 4a-4e, plus the wiki
metadata it reads (`.ai/spec/what/wiki-memory.md` rules 12b-12c, 16):

    notification_entry_id(message) -> str | None                 # rule 4a
    wiki_entry_states(content) -> dict[str, dict]                # rule 4a
    filter_state_change_notifications(n, old, new)               # rule 4a
        -> (delivered, suppressed, notified_ids)
    digest_notifications(old, new, *, today, digest_after, ...)  # rule 4c
        -> (digest, ids)
    stamp_last_notified(content, ids, day) -> str                # rule 4d
    carry_last_notified(new_wiki, old_wiki) -> str               # rule 4d (#93)
    NOTIFY_DIGEST_AFTER / PATROL_NOTIFY_DIGEST_AFTER             # rule 4c
    validate_wiki severity pair                                  # wiki rule 12b
    REPORT_CONTRACT dedup instructions                           # rule 4b

They fail before implementation (AttributeError on the missing symbols,
AssertionError on the missing surfaces) and must pass after. The patrol-tail
wiring (suppressed messages, digest delivery, last_notified written, clock
carried across patrols) is covered end-to-end in test_patrol_deepagents.py.

Run: uv run --group dev pytest tests/ -q
"""
import os
import subprocess
import sys
from pathlib import Path

import patrol


REPO = Path(__file__).resolve().parent.parent

SECTIONS = ["Topology", "Baselines", "Watch-list", "Open questions", "Anomaly log"]
DOT = "·"


def meta_line(id="W-1", status="watching", first_seen="2026-10-01",
              last_verified="2026-10-20", source="kubectl get nodes",
              confidence="high", recheck="get nodes", severity=None,
              verified_patrol=None, last_notified=None):
    """A well-formed metadata line (rules 12, 12a-12c)."""
    line = (f"- id: {id} · status: {status} · first_seen: {first_seen}"
            f" · last_verified: {last_verified} · source: {source}"
            f" · confidence: {confidence}")
    if recheck is not None:
        line += f" · recheck: {recheck}"
    if severity is not None:
        line += f" · severity: {severity}"
    if verified_patrol is not None:
        line += f" · verified_patrol: {verified_patrol}"
    if last_notified is not None:
        line += f" · last_notified: {last_notified}"
    return line


def entry(id="W-1", prose="  worker-1 NotReady for 40d.", **kw):
    return meta_line(id=id, **kw) + "\n" + prose


def build_wiki(watch=(), anomaly=(), patrol_count=3, watch_body=None,
               anomaly_body=None, valid=True):
    bodies = {
        "Topology": "- 3 masters, 2 workers (kubectl get nodes, 2026-10-14, high)",
        "Baselines": "_(none)_",
        "Watch-list": watch_body if watch_body is not None
                      else ("\n".join(watch) if watch else "_(empty)_"),
        "Open questions": "- (none yet)",
        "Anomaly log": anomaly_body if anomaly_body is not None
                       else ("\n".join(anomaly) if anomaly else "_(empty)_"),
    }
    parts = ["---", "wiki_version: 1", "cluster_id: test-cluster",
             f"patrol_count: {patrol_count}",
             'last_patrol: "2026-10-30T12:00:00+0000"', "---",
             "# Cluster Wiki", ""]
    for name in SECTIONS:
        parts += [f"## {name}", bodies[name], ""]
    wiki = "\n".join(parts)
    if valid:
        assert patrol.validate_wiki(wiki) == [], "test fixture must be skeleton-valid"
    return wiki


def section_body(content, name):
    lines = content.split("\n")
    start = lines.index(f"## {name}")
    out, in_fence = [], False
    for line in lines[start + 1:]:
        if line.strip().startswith("```"):
            in_fence = not in_fence
            out.append(line)
            continue
        if line.startswith("## ") and not in_fence:
            break
        out.append(line)
    return "\n".join(out)


# ------------------------------------------------------- rule 4a: tag parser

def test_notification_entry_id_matrix():
    assert patrol.notification_entry_id("[W-1] worker down") == "W-1"
    assert patrol.notification_entry_id("[A-2]  resolved") == "A-2"
    assert patrol.notification_entry_id("[W-1] two words here") == "W-1"
    assert patrol.notification_entry_id("[W-1]") is None
    assert patrol.notification_entry_id("[W-1] ") is None
    assert patrol.notification_entry_id("[] message") is None
    assert patrol.notification_entry_id("W-1 worker down") is None
    assert patrol.notification_entry_id("") is None


# --------------------------------------------------- rule 4a: wiki reader

def test_wiki_entry_states_reads_both_sections_and_optional_pairs():
    wiki = build_wiki(
        watch=[entry("W-1", severity="warn", last_notified="2026-10-24")],
        anomaly=[entry("A-1", status="resolved", recheck=None,
                       severity="crit", last_notified="2026-10-20")],
    )
    states = patrol.wiki_entry_states(wiki)
    assert set(states) == {"W-1", "A-1"}
    assert states["W-1"]["status"] == "watching"
    assert states["W-1"]["severity"] == "warn"
    assert states["W-1"]["first_seen"] == "2026-10-01"
    assert states["W-1"]["last_notified"] == "2026-10-24"
    assert states["A-1"]["status"] == "resolved"
    assert states["A-1"]["severity"] == "crit"
    assert states["A-1"]["last_notified"] == "2026-10-20"


def test_wiki_entry_states_missing_pairs_read_none_and_duplicate_first_wins():
    wiki = build_wiki(watch=[
        entry("W-1"),
        entry("W-1", severity="crit", prose="  a duplicate id."),
    ], valid=False)
    states = patrol.wiki_entry_states(wiki)
    assert states["W-1"]["severity"] is None
    assert states["W-1"]["last_notified"] is None


def test_wiki_entry_states_is_fence_aware():
    """Fenced list-looking lines are prose (rule 11), never entries."""
    fenced = ("```\n"
              + meta_line(id="F-1", severity="crit")
              + "\n```")
    wiki = build_wiki(watch_body=fenced)
    assert patrol.wiki_entry_states(wiki) == {}


# ------------------------------------------------ rule 4a: state-change filter

def _filter(notifications, old, new):
    return patrol.filter_state_change_notifications(notifications, old, new)


def test_filter_new_entry_delivers():
    old = build_wiki()
    new = build_wiki(watch=[entry("W-1", severity="warn")])
    delivered, suppressed, ids = _filter(["[W-1] new problem"], old, new)
    assert delivered == ["[W-1] new problem"]
    assert suppressed == []
    assert ids == ["W-1"]


def test_filter_standing_unchanged_is_suppressed():
    old = build_wiki(watch=[entry("W-1", severity="warn")])
    new = build_wiki(watch=[entry("W-1", severity="warn",
                                  prose="  reworded prose only.")])
    delivered, suppressed, ids = _filter(["[W-1] still down"], old, new)
    assert delivered == []
    assert suppressed == ["[W-1] still down"]
    assert ids == []


def test_filter_severity_change_delivers_including_gain_and_loss():
    warn = build_wiki(watch=[entry("W-1", severity="warn")])
    crit = build_wiki(watch=[entry("W-1", severity="crit")])
    none = build_wiki(watch=[entry("W-1")])
    for old, new in ((warn, crit), (crit, warn), (none, warn), (warn, none)):
        delivered, suppressed, ids = _filter(["[W-1] changed"], old, new)
        assert delivered == ["[W-1] changed"], (old, new)
        assert suppressed == []
        assert ids == ["W-1"]


def test_filter_status_change_delivers_resolution_and_reopen():
    watching = build_wiki(watch=[entry("W-1", severity="warn")])
    resolved = build_wiki(watch=[entry("W-1", status="resolved",
                                       severity="warn")])
    for old, new in ((watching, resolved), (resolved, watching)):
        delivered, suppressed, ids = _filter(["[W-1] status changed"], old, new)
        assert delivered == ["[W-1] status changed"]
        assert suppressed == []
        assert ids == ["W-1"]


def test_filter_removed_entry_delivers_without_stamp_id():
    old = build_wiki(watch=[entry("W-1", severity="warn")])
    new = build_wiki()
    delivered, suppressed, ids = _filter(["[W-1] entry vanished"], old, new)
    assert delivered == ["[W-1] entry vanished"]
    assert suppressed == []
    assert ids == [], "a removed entry has nothing in the new wiki to stamp"


def test_filter_untagged_and_unattributable_messages_fail_open():
    old = build_wiki(watch=[entry("W-1", severity="warn")])
    new = build_wiki(watch=[entry("W-1", severity="warn")])
    messages = ["manual note with no tag", "[W-9] unknown id"]
    delivered, suppressed, ids = _filter(messages, old, new)
    assert delivered == messages
    assert suppressed == []
    assert ids == []


def test_filter_preserves_order_and_notified_ids():
    old = build_wiki(watch=[entry("W-1", severity="warn")])
    new = build_wiki(watch=[
        entry("W-1", severity="crit"),          # severity change
        entry("W-2", severity="warn"),          # new entry
    ])
    messages = ["[W-1] escalated", "[W-2] new", "untagged note"]
    delivered, suppressed, ids = _filter(messages, old, new)
    assert delivered == messages
    assert suppressed == []
    assert ids == ["W-1", "W-2"]

    messages = ["[W-1] escalated", "[W-9] unknown", "[W-2] new"]
    delivered, suppressed, ids = _filter(messages, old, new)
    assert delivered == messages
    assert ids == ["W-1", "W-2"]


def test_filter_empty_new_wiki_fails_open():
    old = build_wiki(watch=[entry("W-1", severity="warn")])
    messages = ["[W-1] still down", "untagged"]
    delivered, suppressed, ids = _filter(messages, old, "")
    assert delivered == messages
    assert suppressed == []
    assert ids == []


def test_filter_same_wiki_suppresses_all_tagged():
    wiki = build_wiki(watch=[entry("W-1", severity="warn")],
                      anomaly=[entry("A-1", status="resolved", recheck=None,
                                     severity="crit")])
    messages = ["[W-1] standing", "[A-1] closed"]
    delivered, suppressed, ids = _filter(messages, wiki, wiki)
    assert delivered == []
    assert suppressed == messages
    assert ids == []


# --------------------------------------------------------- rule 4c: digest

def _digest(old, new, *, today="2026-10-30", digest_after=7, exclude_ids=(),
            patrol_count=None, stale_after=None):
    return patrol.digest_notifications(
        old, new, today=today, digest_after=digest_after,
        exclude_ids=exclude_ids, patrol_count=patrol_count,
        stale_after=stale_after)


def test_digest_disabled_at_zero():
    old = build_wiki(watch=[entry("W-1", severity="warn")])
    new = build_wiki(watch=[entry("W-1", severity="warn")])
    assert _digest(old, new, digest_after=0) == (None, [])


def test_digest_long_unfixed_entry_gets_one_migration_nudge():
    old = build_wiki(watch=[entry("W-1", severity="warn")])
    new = build_wiki(watch=[entry("W-1", severity="warn")])
    digest, ids = _digest(old, new)
    assert digest == "[digest] 1 entry unfixed for more than 7 days: W-1"
    assert ids == ["W-1"]


def test_digest_first_seen_boundary_is_strictly_older():
    old = build_wiki(watch=[entry("W-1", first_seen="2026-10-23",
                                  severity="warn")])
    new = old
    assert _digest(old, new) == (None, [])  # exactly 7 days old


def test_digest_respects_last_notified_cadence():
    recent = build_wiki(watch=[entry("W-1", severity="warn",
                                     last_notified="2026-10-24")])
    due = build_wiki(watch=[entry("W-1", severity="warn",
                                  last_notified="2026-10-23")])
    assert _digest(recent, recent) == (None, [])  # 6 days since notified
    digest, ids = _digest(due, due)               # 7 days since notified
    assert digest == "[digest] 1 entry unfixed for more than 7 days: W-1"
    assert ids == ["W-1"]


def test_digest_excludes_closed_and_already_notified_entries():
    old = build_wiki(watch=[entry("W-1", first_seen="2026-10-29",
                                  severity="warn")],
                     anomaly=[entry("A-1", status="resolved", recheck=None,
                                    severity="warn")])
    new = old
    assert _digest(old, new, exclude_ids=("W-1",)) == (None, [])
    assert _digest(old, new) == (None, [])  # A-1 is closed


def test_digest_orders_watch_list_before_anomaly_log_and_pluralizes():
    old = build_wiki(watch=[entry("W-2", severity="warn"),
                            entry("W-1", severity="warn")],
                     anomaly=[entry("A-1", severity="warn", recheck=None)])
    new = old
    digest, ids = _digest(old, new)
    assert digest == ("[digest] 3 entries unfixed for more than 7 days: "
                      "W-2, W-1, A-1")
    assert ids == ["W-2", "W-1", "A-1"]


def test_digest_skips_entries_the_expiry_pass_will_retire():
    old = build_wiki(watch=[entry("W-1", severity="warn",
                                  verified_patrol=4)])
    new = old
    assert _digest(old, new, patrol_count=10, stale_after=5) == (None, [])
    digest, ids = _digest(old, new, patrol_count=10, stale_after=0)
    assert ids == ["W-1"]


def test_digest_skip_uses_the_old_clock_not_the_model_copy():
    """Rule 4c: the expiry skip reads the process-maintained old-wiki clock;
    a model's `verified_patrol` guess in the new wiki is ignored."""
    old = build_wiki(watch=[entry("W-1", severity="warn",
                                  verified_patrol=4)])
    new = build_wiki(watch=[entry("W-1", severity="warn",
                                  verified_patrol=9)])
    assert _digest(old, new, patrol_count=10, stale_after=5) == (None, [])


def test_digest_does_not_skip_a_reverified_entry():
    """Rule 4c: advancing `last_verified` makes the expiry pass reset the
    clock, so the entry is not about to expire even when the old clock looks
    stale."""
    old = build_wiki(watch=[entry("W-1", severity="warn",
                                  verified_patrol=4,
                                  last_verified="2026-10-20")])
    new = build_wiki(watch=[entry("W-1", severity="warn",
                                  verified_patrol=4,
                                  last_verified="2026-10-21")])
    digest, ids = _digest(old, new, patrol_count=10, stale_after=5)
    assert ids == ["W-1"]


def test_digest_never_skips_an_anomaly_log_watching_entry():
    """Rule 4c: only Watch-list entries can be auto-expired (rule 21), so the
    expiry skip must not silence a stale-clock Anomaly-log watching entry."""
    old = build_wiki(anomaly=[entry("A-1", severity="warn", recheck=None,
                                    verified_patrol=4)])
    new = old
    digest, ids = _digest(old, new, patrol_count=10, stale_after=5)
    assert digest == "[digest] 1 entry unfixed for more than 7 days: A-1"
    assert ids == ["A-1"]


def test_digest_age_and_fallback_use_the_old_wiki_first_seen():
    """Rule 4c: the model cannot reset the digest age by rewriting
    first_seen in the new wiki; the old wiki's date is the clock."""
    old = build_wiki(watch=[entry("W-1", severity="warn",
                                  first_seen="2026-10-01")])
    new = build_wiki(watch=[entry("W-1", severity="warn",
                                  first_seen="2026-10-29")])
    digest, ids = _digest(old, new)
    assert digest == "[digest] 1 entry unfixed for more than 7 days: W-1"
    assert ids == ["W-1"]


# -------------------------------------------------- rule 4d: notify clock stamp

def test_stamp_last_notified_rewrites_existing_and_appends_absent():
    content = build_wiki(
        watch=[entry("W-1", severity="warn", last_notified="2026-10-20"),
               entry("W-2", severity="warn")],
        anomaly=[entry("A-1", status="resolved", recheck=None,
                       severity="crit", last_notified="2026-10-19")],
    )
    out = patrol.stamp_last_notified(content, ["W-1", "W-2"], "2026-10-30")
    assert "last_notified: 2026-10-30" in section_body(out, "Watch-list")
    assert "last_notified: 2026-10-20" not in section_body(out, "Watch-list")
    assert section_body(out, "Anomaly log").count("last_notified: 2026-10-19") == 1
    assert out.count("last_notified: 2026-10-30") == 2
    assert patrol.validate_wiki(out) == []


def test_stamp_last_notified_touches_only_target_entries_and_preserves_bytes():
    content = build_wiki(
        watch=[entry("W-1", severity="warn"), entry("W-2", severity="warn")],
        anomaly=[entry("A-1", status="resolved", recheck=None,
                       severity="crit")],
    )
    out = patrol.stamp_last_notified(content, ["W-2"], "2026-10-30")
    wl = section_body(out, "Watch-list")
    assert "last_notified" not in wl.split("W-2")[0]
    assert "last_notified: 2026-10-30" in wl
    assert "last_notified" not in section_body(out, "Anomaly log")
    # frontmatter and prose are untouched
    assert content.split("## Topology")[0] == out.split("## Topology")[0]
    assert "  worker-1 NotReady for 40d." in out


def test_stamp_last_notified_is_fence_aware_and_absent_id_noop():
    fenced = ("```\n" + meta_line(id="F-1") + "\n```")
    content = build_wiki(watch_body=fenced)
    assert patrol.stamp_last_notified(content, ["F-1"], "2026-10-30") == content
    once = patrol.stamp_last_notified(content, ["W-1"], "2026-10-30")
    # not targeted because W-1 is not an entry in this wiki
    assert once == content


def test_stamp_last_notified_missing_id_is_noop():
    content = build_wiki(watch=[entry("W-1", severity="warn")])
    assert patrol.stamp_last_notified(content, ["W-9"], "2026-10-30") == content


# ------------------------------------------- rule 4d: notify clock carry (#93)
# The digest cadence collapsed to every other patrol because the model drops the
# optional, process-owned `last_notified` pair on every rewrite and the write
# path only stamped the ids notified THIS patrol. The carry restores the old
# on-disk value onto every shared entry, so a rewrite cannot reset the cadence.

def _carry(new, old):
    return patrol.carry_last_notified(new, old)


def test_carry_restores_the_old_clock_and_replaces_the_model_copy():
    old = build_wiki(watch=[entry("W-1", severity="warn",
                                  last_notified="2026-11-05")])
    # a rewrite that dropped the pair, and one where the model invented a value
    dropped = build_wiki(watch=[entry("W-1", severity="warn")])
    invented = build_wiki(watch=[entry("W-1", severity="warn",
                                       last_notified="1999-01-01")])
    for new in (dropped, invented):
        out = _carry(new, old)
        body = section_body(out, "Watch-list")
        assert body.count("last_notified: 2026-11-05") == 1
        assert "1999-01-01" not in out
        assert patrol.validate_wiki(out) == []
    # a duplicate pair on one line leaves exactly one after the carry
    dup_pairs = build_wiki(watch_body=(
        meta_line(id="W-1", severity="warn", last_notified="1999-01-01")
        + " · last_notified: 1998-01-01\n  worker-1 NotReady for 40d."))
    out = _carry(dup_pairs, old)
    assert out.count("last_notified: 2026-11-05") == 1
    assert "1999-01-01" not in out and "1998-01-01" not in out


def test_carry_drops_pairs_the_old_wiki_has_no_clock_for():
    """A model-authored clock is never durable: an entry the old wiki has no
    parseable clock for (an existing entry without the pair, a brand-new
    entry) carries no pair at all after the carry."""
    old = build_wiki(watch=[entry("W-1", severity="warn")])
    new = build_wiki(
        watch=[entry("W-1", severity="warn", last_notified="2026-11-01"),
               entry("W-2", severity="crit", last_notified="2026-11-02")],
        anomaly=[entry("A-1", status="resolved", recheck=None,
                       severity="crit", last_notified="2026-11-03")],
    )
    out = _carry(new, old)
    assert "last_notified" not in section_body(out, "Watch-list")
    assert "last_notified" not in section_body(out, "Anomaly log")
    assert patrol.validate_wiki(out) == []


def test_carry_matrix_present_new_removed_and_unparseable_old_clock():
    old = build_wiki(
        watch=[entry("W-1", severity="warn", last_notified="2026-11-05"),
               # regex-shaped but not a calendar date: no clock
               entry("W-2", severity="warn", last_notified="2026-02-30"),
               # moved to the Anomaly log by the model this patrol
               entry("A-1", severity="crit", status="watching",
                     last_notified="2026-11-03")],
        anomaly=[entry("A-9", status="resolved", recheck=None,
                       severity="crit", last_notified="2026-11-04")],
    )
    # W-1 present in both (carried); W-2's old clock is not a calendar date
    # (dropped); A-1 moved Watch-list → Anomaly log (carried across sections by
    # id); A-9 was removed by the model (no target, nothing to assert about its
    # old clock); W-4 is new (its own notification is the path).
    new = build_wiki(
        watch=[entry("W-1", severity="warn"),
               entry("W-2", severity="warn", last_notified="2026-11-06"),
               entry("W-4", severity="warn", last_notified="2026-11-07")],
        anomaly=[entry("A-1", status="resolved", recheck=None,
                       severity="crit")],
    )
    out = _carry(new, old)
    assert "last_notified: 2026-11-05" in section_body(out, "Watch-list")
    assert out.count("last_notified: 2026-11-05") == 1
    assert "2026-11-06" not in out          # not a calendar date: dropped
    assert "2026-11-07" not in out          # new entry: no clock yet
    # the section move carries the clock: it is keyed by id, not by section
    assert "last_notified: 2026-11-03" in section_body(out, "Anomaly log")
    assert section_body(out, "Watch-list").count("2026-11-03") == 0
    assert patrol.validate_wiki(out) == []


def test_carry_preserves_everything_else_byte_for_byte():
    old = build_wiki(watch=[entry("W-1", severity="warn",
                                  last_notified="2026-11-05")])
    new = build_wiki(
        watch=[entry("W-1", severity="warn")],
        anomaly=[entry("A-1", status="resolved", recheck=None,
                       severity="crit")],
    )
    out = _carry(new, old)
    # frontmatter, prose, and untargeted entries are untouched
    assert new.split("## Topology")[0] == out.split("## Topology")[0]
    assert "  worker-1 NotReady for 40d." in out
    assert section_body(new, "Anomaly log") == section_body(out, "Anomaly log")
    # idempotent: carrying the carried wiki changes nothing
    assert _carry(out, old) == out
    # a wiki with no clocks anywhere is returned unchanged
    plain = build_wiki(watch=[entry("W-1", severity="warn")])
    assert _carry(plain, plain) == plain


def test_carry_is_fence_aware_and_first_occurrence_wins():
    old = build_wiki(watch=[entry("W-1", severity="warn",
                                  last_notified="2026-11-05")])
    fenced = "```\n" + meta_line(id="W-1") + "\n```"
    new = build_wiki(watch_body=fenced)
    assert _carry(new, old) == new       # a fenced list line is never an entry
    # duplicate id: the first occurrence is the entry the carry touches, the
    # later one is left byte-for-byte alone (rule 4d's first-occurrence rule)
    dup = build_wiki(watch=[entry("W-1", severity="warn"),
                            entry("W-1", severity="warn",
                                  last_notified="1997-01-01")], valid=False)
    out = _carry(dup, old)
    assert out.count("last_notified: 2026-11-05") == 1
    lines = [ln for ln in out.split("\n") if "id: W-1" in ln]
    assert "last_notified: 2026-11-05" in lines[0]
    assert "1997-01-01" not in lines[0]
    assert "1997-01-01" in lines[1]          # the later duplicate is untouched
    assert "2026-11-05" not in lines[1]


# ------------------------------------------------------- config surface (4c)

def test_digest_config_surface_defaults_to_weekly():
    assert patrol.NOTIFY_DIGEST_AFTER == 7
    source = Path(patrol.__file__).read_text(encoding="utf-8")
    assert "PATROL_NOTIFY_DIGEST_AFTER" in source, \
        "the digest cadence must be configurable via PATROL_NOTIFY_DIGEST_AFTER"


def test_digest_config_rejects_negative_values_at_startup(tmp_path):
    env = dict(os.environ, PATROL_NOTIFY_DIGEST_AFTER="-1")
    result = subprocess.run(
        [sys.executable, "-c", "import patrol"],
        cwd=REPO, env=env, capture_output=True, text=True,
    )
    assert result.returncode != 0
    assert "PATROL_NOTIFY_DIGEST_AFTER" in result.stderr


# ------------------------------------------------- wiki rule 12b: severity pair

def test_validate_accepts_missing_and_valid_severity():
    assert patrol.validate_wiki(build_wiki(watch=[entry("W-1")])) == []
    for severity in ("info", "warn", "crit"):
        assert patrol.validate_wiki(
            build_wiki(watch=[entry("W-1", severity=severity)])) == []


def test_validate_rejects_unknown_severity():
    wiki = build_wiki(watch=[entry("W-1", severity="urgent")], valid=False)
    errors = patrol.validate_wiki(wiki)
    assert errors, "an unknown severity value must fail validation"
    assert any("severity" in e and "W-1" in e for e in errors)


# --------------------------------------------------- rule 4b: contract pins

def test_report_contract_carries_dedup_rules():
    contract = patrol.REPORT_CONTRACT
    assert ("Every notification MUST begin with the wiki entry id it concerns "
            "in square brackets") in contract
    assert ("Notify only when an entry is new, its severity changed, or its "
            "status changed (resolution or re-opening).") in contract
    assert ("Do not notify about an entry that is standing and unchanged: "
            "update its wiki entry and leave notifications empty for it.") in contract
    assert ("Every Watch-list and Anomaly-log entry also carries a severity: "
            "<info|warn|crit> pair") in contract
