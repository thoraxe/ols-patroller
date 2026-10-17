"""Wiki skeleton schema + validation-on-write — contract tests for #5.

These tests pin the public surface of patrol.py's planned wiki-schema region:

    WIKI_VERSION                                                    # rule 10
    validate_wiki(content) -> list[str]                             # rule 15
    stamp_wiki(content, patrol_count, last_patrol, cluster_id) -> str  # rule 17

plus the surfaces the skeleton rides on: the seed (rule 14), the report
contract's format instructions (reporting-notifications.md rule 5), and the
runtime dependency surface (container-image.md rules 6-7 — PyYAML joins the
pinned set). They fail before implementation (AttributeError on the missing
symbols, AssertionError on the missing surfaces) and must pass after.
Contract: .ai/spec/what/wiki-memory.md rules 10-19 [PLANNED: #5].

Scope note: these tests pin the pure validator/stamper. The wiring into the
patrol tail (validate -> one repair pass -> stamp -> write, keep-old-wiki on
double failure) is verified end-to-end by the fake-model e2e in
test_patrol_deepagents.py.

Run: uv run --group dev pytest tests/ -q
"""
import re
from pathlib import Path

import pytest
import yaml

import patrol


SECTIONS = ["Topology", "Baselines", "Watch-list", "Open questions", "Anomaly log"]

DOT = "·"  # U+00B7 middle dot — the metadata-line separator (rule 12)


def meta_line(id="W-1", status="watching", first_seen="2026-10-01",
              last_verified="2026-10-14", source="kubectl get nodes",
              confidence="high", extra=""):
    """A well-formed entry metadata line (rule 12)."""
    line = (f"- id: {id} · status: {status} · first_seen: {first_seen}"
            f" · last_verified: {last_verified} · source: {source}"
            f" · confidence: {confidence}")
    return line + (f" · {extra}" if extra else "")


def default_bodies():
    """Section bodies for a canonical wiki — prose sections carry plain prose
    items (rule 13); metadata sections carry entries (rule 12)."""
    return {
        "Topology": "- 3 masters, 2 workers (kubectl get nodes, 2026-10-14, high)",
        "Baselines": "_(none yet)_",
        "Watch-list": (meta_line()
                       + "\n  worker-1 NotReady for 40d — free prose hypothesis."),
        "Open questions": "- why is worker-1 flapping?",
        "Anomaly log": (meta_line(id="A-1", source="kubectl get pods -A",
                                  confidence="medium")
                        + "\n  demo-app crashlooping."),
    }


def build_wiki(bodies=None, fm=None, order=None, omit=()):
    """Assemble a wiki from a {section: body} mapping and frontmatter lines.

    `fm=None` means the canonical frontmatter (rule 10); `fm=[]` omits the block.
    `omit` drops sections (for the missing-section matrix).
    """
    merged = {**default_bodies(), **(bodies or {})}
    order = order or SECTIONS
    if fm is None:
        fm = ["wiki_version: 1", "cluster_id: test-cluster", "patrol_count: 3",
              'last_patrol: "2026-10-14T18:44:00+0000"']
    parts = []
    if fm != []:
        parts += ["---", *fm, "---"]
    parts += ["# Cluster Wiki", ""]
    for name in order:
        if name in omit:
            continue
        parts += [f"## {name}", merged[name], ""]
    return "\n".join(parts)


def split_wiki(content):
    """(frontmatter-text, body) — the frontmatter fence block at the top."""
    lines = content.split("\n")
    assert lines[0] == "---", "test fixture must start with a frontmatter fence"
    end = lines.index("---", 1)
    return "\n".join(lines[1:end]), "\n".join(lines[end + 1:])


def frontmatter_of(content):
    return yaml.safe_load(split_wiki(content)[0])


def body_of(content):
    return split_wiki(content)[1]


# ------------------------------------------------------------ validate: accepts

def test_validate_accepts_canonical_wiki():
    assert patrol.validate_wiki(build_wiki()) == []


def test_validate_accepts_any_section_order():
    """Rule 11: section order is free."""
    order = ["Anomaly log", "Open questions", "Watch-list", "Baselines", "Topology"]
    assert patrol.validate_wiki(build_wiki(order=order)) == []


def test_validate_accepts_free_heading_levels_around_sections():
    """Rule 11: level-1 and level-3+ headings are unconstrained prose."""
    wiki = build_wiki(bodies={
        "Topology": "### worker detail\n- 3 masters, 2 workers (kubectl get nodes)",
        "Baselines": "- nothing bizarre",
    })
    assert patrol.validate_wiki(wiki) == []


def test_validate_accepts_trailing_whitespace_on_section_headings():
    wiki = build_wiki()
    wiki = wiki.replace("## Watch-list", "## Watch-list   ")
    assert patrol.validate_wiki(wiki) == []


def test_validate_accepts_prose_sections_without_metadata():
    """Rule 13: plain list items in Topology/Baselines/Open questions are fine."""
    wiki = build_wiki(bodies={
        "Topology": "- plain prose item without any metadata",
        "Open questions": "- another plain item",
    })
    assert patrol.validate_wiki(wiki) == []


def test_validate_accepts_extra_metadata_keys_and_sub_bullets():
    """Rule 12: additional key: value pairs may follow confidence (forward-compat
    for #6's recheck); prose continuation may include sub-bullets."""
    entry = (meta_line(extra="recheck: kubectl get nodes")
             + "\n  worker-1 NotReady for 40d — free prose hypothesis."
             + "\n  - a sub-bullet of prose, not an entry")
    wiki = build_wiki(bodies={"Watch-list": entry})
    assert patrol.validate_wiki(wiki) == []


def test_validate_accepts_star_bullets_and_placeholder_empty_sections():
    """Rule 12 allows `* ` items; rule 11 allows a non-list placeholder line."""
    wiki = build_wiki(bodies={
        "Watch-list": "_(empty)_",
        "Anomaly log": meta_line(id="A-9").replace("- ", "* ", 1),
    })
    assert patrol.validate_wiki(wiki) == []


def test_validate_accepts_unquoted_yaml_timestamp():
    """Rule 10: PyYAML types an unquoted ISO scalar as a datetime — accepted."""
    wiki = build_wiki(fm=["wiki_version: 1", "cluster_id: c", "patrol_count: 3",
                          "last_patrol: 2026-10-14T18:44:00+01:00"])
    assert patrol.validate_wiki(wiki) == []


def test_validate_skips_fenced_code_blocks():
    """Rule 11: fenced code blocks are prose — a `## ` line inside a fence is
    not a section heading, and a list-looking line inside a fence is not an
    entry (a fenced example of the format must not fail validation)."""
    entry = (meta_line()
             + "\n  worker-1 NotReady for 40d — free prose hypothesis.")
    wiki = build_wiki(bodies={
        "Topology": "```\n## Fenced heading, not a section\n```",
        "Watch-list": entry + "\n\n```\n- id: F · status: bogus · not an entry\n```",
    })
    assert patrol.validate_wiki(wiki) == []


# ------------------------------------------------------------ validate: frontmatter

def test_validate_rejects_missing_frontmatter_block():
    assert patrol.validate_wiki(build_wiki(fm=[]))


def test_validate_rejects_unterminated_frontmatter_fence():
    wiki = "---\nwiki_version: 1\n" + build_wiki()
    assert patrol.validate_wiki(wiki)


def test_validate_rejects_non_mapping_frontmatter():
    wiki = build_wiki(fm=["- a", "- b"])
    assert patrol.validate_wiki(wiki)


@pytest.mark.parametrize("key", ["wiki_version", "cluster_id", "patrol_count", "last_patrol"])
def test_validate_rejects_missing_frontmatter_key(key):
    fm = ["wiki_version: 1", "cluster_id: test-cluster", "patrol_count: 3",
          'last_patrol: "2026-10-14T18:44:00+0000"']
    fm.remove(next(l for l in fm if l.startswith(key + ":")))
    assert patrol.validate_wiki(build_wiki(fm=fm))


def test_validate_rejects_unknown_frontmatter_key():
    """Rule 10: the key set is closed — model-invented keys are failures."""
    fm = ["wiki_version: 1", "cluster_id: c", "patrol_count: 3",
          'last_patrol: "2026-10-14T18:44:00+0000"', "bogus_key: 1"]
    errors = patrol.validate_wiki(build_wiki(fm=fm))
    assert errors
    assert any("bogus_key" in e for e in errors), "errors must name the offending key"


def test_validate_rejects_wrong_wiki_version():
    assert patrol.validate_wiki(build_wiki(fm=["wiki_version: 2", "cluster_id: c",
                                               "patrol_count: 3",
                                               'last_patrol: "2026-10-14T18:44:00+0000"']))


def test_validate_rejects_non_integer_wiki_version():
    assert patrol.validate_wiki(build_wiki(fm=['wiki_version: "1"', "cluster_id: c",
                                               "patrol_count: 3",
                                               'last_patrol: "2026-10-14T18:44:00+0000"']))


@pytest.mark.parametrize("count", [0, -1, '"3"'])
def test_validate_rejects_bad_patrol_count(count):
    assert patrol.validate_wiki(build_wiki(fm=["wiki_version: 1", "cluster_id: c",
                                               f"patrol_count: {count}",
                                               'last_patrol: "2026-10-14T18:44:00+0000"']))


def test_validate_rejects_empty_cluster_id():
    assert patrol.validate_wiki(build_wiki(fm=["wiki_version: 1", 'cluster_id: ""',
                                               "patrol_count: 3",
                                               'last_patrol: "2026-10-14T18:44:00+0000"']))


def test_validate_rejects_unparseable_last_patrol():
    assert patrol.validate_wiki(build_wiki(fm=["wiki_version: 1", "cluster_id: c",
                                               "patrol_count: 3",
                                               'last_patrol: "yesterday"']))


# ------------------------------------------------------------ validate: sections

@pytest.mark.parametrize("section", SECTIONS)
def test_validate_rejects_missing_section(section):
    """Rule 11 — and the error names the missing section (rule 15)."""
    errors = patrol.validate_wiki(build_wiki(omit=[section]))
    assert errors
    assert any(section in e for e in errors)


def test_validate_rejects_duplicate_section():
    wiki = build_wiki(bodies={"Topology": "- a\n\n## Topology\n- b"})
    assert patrol.validate_wiki(wiki)


def test_validate_rejects_unknown_level2_section():
    wiki = build_wiki(bodies={"Topology": "- a\n\n## Extra\n- b"})
    assert patrol.validate_wiki(wiki)


def test_validate_rejects_wrong_case_section_heading():
    wiki = build_wiki().replace("## Watch-list", "## watch-list")
    assert patrol.validate_wiki(wiki)


def test_validate_rejects_annotated_section_heading():
    wiki = build_wiki().replace("## Topology", "## Topology (v2)")
    assert patrol.validate_wiki(wiki)


def test_validate_rejects_h3_standing_in_for_a_section():
    """Rule 11: a `###` heading is prose — it does not satisfy the section."""
    wiki = build_wiki().replace("## Watch-list", "### Watch-list")
    assert patrol.validate_wiki(wiki)


# ------------------------------------------------------------ validate: entries

def test_validate_rejects_plain_list_item_in_watch_list():
    """Rule 12: in metadata sections every top-level list item is an entry —
    the old `- (empty)` placeholder form is now a failure."""
    assert patrol.validate_wiki(build_wiki(bodies={"Watch-list": "- (empty)"}))


def test_validate_rejects_entry_missing_a_field():
    """Rule 12 — drop each of the six required fields in turn."""
    full = ("id: W-1", "status: watching", "first_seen: 2026-10-01",
            "last_verified: 2026-10-14", "source: kubectl get nodes",
            "confidence: high")
    for drop in full:
        pairs = [p for p in full if p != drop]
        line = "- " + f" {DOT} ".join(pairs)
        wiki = build_wiki(bodies={"Watch-list": line})
        assert patrol.validate_wiki(wiki), f"missing {drop!r} must be invalid"


def test_validate_rejects_fields_out_of_order():
    line = (f"- status: watching {DOT} id: W-1 {DOT} first_seen: 2026-10-01"
            f" {DOT} last_verified: 2026-10-14 {DOT} source: kubectl get nodes"
            f" {DOT} confidence: high")
    assert patrol.validate_wiki(build_wiki(bodies={"Watch-list": line}))


def test_validate_rejects_bad_status_vocabulary():
    line = meta_line(status="active")
    assert patrol.validate_wiki(build_wiki(bodies={"Watch-list": line}))


def test_validate_rejects_bad_confidence_vocabulary():
    line = meta_line(confidence="certain")
    assert patrol.validate_wiki(build_wiki(bodies={"Watch-list": line}))


@pytest.mark.parametrize("date", ["2026/10/01", "10-01-2026", "2026-13-40",
                                  "2026-02-30", "2026-10"])
def test_validate_rejects_bad_entry_dates(date):
    wiki = build_wiki(bodies={"Watch-list": meta_line(first_seen=date)})
    assert patrol.validate_wiki(wiki)


def test_validate_rejects_duplicate_id_across_sections():
    """Rule 12: ids are unique across the whole wiki."""
    wiki = build_wiki(bodies={"Anomaly log": meta_line(id="W-1")})
    errors = patrol.validate_wiki(wiki)
    assert errors
    assert any("W-1" in e for e in errors)


def test_validate_rejects_prose_on_the_metadata_line():
    """Rule 12: the metadata line carries nothing but key: value pairs."""
    assert patrol.validate_wiki(build_wiki(
        bodies={"Watch-list": meta_line() + " and some trailing prose"}))


def test_validate_rejects_wrong_separator():
    line = meta_line().replace(f" {DOT} ", " | ")
    assert patrol.validate_wiki(build_wiki(bodies={"Watch-list": line}))


def test_validate_does_not_judge_stamped_values():
    """Rule 15: the model's values for the stamped fields are not judged —
    an absurd-but-typed patrol_count/cluster_id/last_patrol is valid."""
    wiki = build_wiki(fm=["wiki_version: 1", "cluster_id: wrong-cluster",
                          "patrol_count: 999999",
                          'last_patrol: "2000-01-01T00:00:00+00:00"'])
    assert patrol.validate_wiki(wiki) == []


# ------------------------------------------------------------ stamp_wiki

def test_stamp_rewrites_owned_fields_and_preserves_the_rest():
    wiki = build_wiki()
    stamped = patrol.stamp_wiki(wiki, patrol_count=7,
                                last_patrol="2026-10-16T07:31:00-0400",
                                cluster_id="prod-cluster")
    fm = frontmatter_of(stamped)
    assert fm["patrol_count"] == 7
    assert fm["last_patrol"] == "2026-10-16T07:31:00-0400"
    assert fm["cluster_id"] == "prod-cluster"
    assert fm["wiki_version"] == 1            # preserved, not owned
    assert body_of(stamped) == body_of(wiki)  # body byte-for-byte


def test_stamp_output_is_itself_valid():
    stamped = patrol.stamp_wiki(build_wiki(), patrol_count=1,
                                last_patrol="2026-10-16T07:31:00-0400",
                                cluster_id="c")
    assert patrol.validate_wiki(stamped) == []


# ------------------------------------------------------------ constants & surfaces

def test_wiki_version_constant():
    """Rule 10: the skeleton schema version is 1."""
    assert patrol.WIKI_VERSION == 1


def test_seed_demonstrates_skeleton():
    """Rule 14: the seed shows frontmatter (placeholder values) + the sections;
    it is prompt context, never validated or written."""
    for key in ("wiki_version:", "cluster_id:", "patrol_count:", "last_patrol:"):
        assert key in patrol.WIKI_SEED, f"seed must show the {key} key"
    for section in SECTIONS:
        assert f"## {section}" in patrol.WIKI_SEED
    assert "patrol_count: 0" in patrol.WIKI_SEED
    assert "last_patrol: null" in patrol.WIKI_SEED


def test_report_contract_instructs_the_skeleton():
    """reporting-notifications.md rule 5: the contract tells the model the
    frontmatter keys, the section set, and the entry metadata fields."""
    contract = patrol.REPORT_CONTRACT
    for key in ("wiki_version", "cluster_id", "patrol_count", "last_patrol"):
        assert key in contract, f"REPORT_CONTRACT must name {key}"
    for section in SECTIONS:
        assert section in contract, f"REPORT_CONTRACT must name the {section} section"
    for field in ("id:", "status:", "first_seen:", "last_verified:", "source:",
                  "confidence:"):
        assert field in contract, f"REPORT_CONTRACT must name the {field} field"


def test_pyyaml_joins_the_runtime_dependency_surface():
    """Rule 15 / container-image.md rules 6-7: PyYAML is a runtime dependency —
    PEP 723 header, pyproject [project] mirror, and the exact image pin."""
    repo = Path(patrol.__file__).parent
    source = Path(patrol.__file__).read_text()
    assert re.search(r'"pyyaml>=', source), "patrol.py PEP 723 header must list pyyaml"
    pyproject = (repo / "pyproject.toml").read_text()
    m = re.search(r"^dependencies\s*=\s*\[(.*?)\]", pyproject, re.M | re.S)
    assert m and '"pyyaml>=6"' in m.group(1), \
        "pyproject [project] dependencies must mirror pyyaml>=6"
    reqs = (repo / "requirements.txt").read_text()
    assert re.search(r"^pyyaml==", reqs, re.M), \
        "requirements.txt must carry the exact pyyaml== pin"
