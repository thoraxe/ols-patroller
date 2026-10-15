"""CLI & default-surface — contract tests for patrol.py's defaults (#25, #27).

These pin the default patrol cadence and the default-on output stream. The
flag surface is checked behaviorally via `--help` (argparse exits during
parse_args, before the env check, so no PATROL_* env is needed); the interval
default is pinned on the argparse wiring (source assertion — same pattern as
the Containerfile static tests).

Contract: .ai/spec/what/system-overview.md (Configuration Surface),
.ai/spec/what/reporting-notifications.md rule 9a [PLANNED: #25, #27].

Run: uv run --group dev pytest tests/ -q
"""
import re
import subprocess
import sys
from pathlib import Path


REPO = Path(__file__).resolve().parent.parent
PATROL = REPO / "patrol.py"
SOURCE = PATROL.read_text()


# ------------------------------------------------------------------ #25: cadence

def test_interval_default_is_six_hours():
    # The PATROL_INTERVAL env fallback is the loop-mode default: 21600 s
    # (6 h), the resident-SRE cadence — not the old 900 s demo loop.
    m = re.search(
        r'"--interval",[^)]*?'
        r'default=int\(os\.environ\.get\(\s*"PATROL_INTERVAL",\s*"(\d+)"\s*\)\)',
        SOURCE,
    )
    assert m, (
        "patrol.py must default --interval from the PATROL_INTERVAL env "
        "fallback (system-overview.md Configuration Surface)"
    )
    assert m.group(1) == "21600", (
        f"the default patrol interval must be 21600 s (6 h, #25), "
        f"got {m.group(1)} s"
    )


# ------------------------------------------------------------------ #27: stream default

def test_help_shows_quiet_not_verbose():
    # The flag surface: -q/--quiet opts out of the per-turn output stream
    # (on by default); the old opt-in -v/--verbose is gone.
    r = subprocess.run(
        [sys.executable, str(PATROL), "--help"],
        capture_output=True, text=True, timeout=120,
    )
    assert r.returncode == 0, (
        f"--help must succeed (argparse exits before the env check), "
        f"got exit {r.returncode}: {r.stderr[-400:]!r}"
    )
    assert "-q, --quiet" in r.stdout, (
        "--help must document the short and long form '-q, --quiet' "
        "(reporting-notifications.md rule 9a, #27)"
    )
    assert "--verbose" not in r.stdout and "-v" not in r.stdout, (
        "the opt-in --verbose flag is gone — the stream is on by default (#27)"
    )


def test_verbose_defaults_on():
    # The stream is on unless --quiet is passed: verbose is derived from
    # args.quiet, and no args.verbose reference remains.
    assert "args.quiet" in SOURCE, (
        "verbose must be derived from --quiet — the stream is on by "
        "default (#27, reporting-notifications.md rule 9a)"
    )
    assert "not args.quiet" in SOURCE, (
        "the derivation must be 'not args.quiet' — the stream is ON by "
        "default; an inverted form would pass the flag surface while "
        "defaulting off (#27)"
    )
    assert "args.verbose" not in SOURCE, (
        "no args.verbose reference may remain — the opt-in flag is --quiet (#27)"
    )


def test_evidence_prints_by_default():
    # reporting-notifications.md rule 9 (#27): each finding's evidence prints
    # with the findings unless --quiet — the evidence print must be gated on
    # the default-on verbose path, not on a default-off flag.
    m = re.search(r'if (\w+) and f\.get\("evidence"\)', SOURCE)
    assert m, (
        "the evidence print must stay on the default-on verbose path "
        "(rule 9, #27)"
    )
    assert m.group(1) in ("verbose", "not_quiet", "not args.quiet"), (
        f"evidence must print by default — gated on the default-on stream "
        "flag, got {m.group(1)!r} (rule 9, #27)"
    )


# ------------------------------------------------------- #25/#27: doc surfaces

# system-overview.md rule 16: committed surfaces that name the default
# cadence or the output flag must state the post-#25/#27 truth.
DOC_SURFACES = ("README.md", "AGENTS.md", "ARCHITECTURE.md")
STALE_DOC_PATTERNS = (
    (r"--interval[^\n]*\s900\b", "the --interval 900 flag form"),
    (r"every[^\n]{0,30}(15\s*min|900)", "the 'every 15 min' / 'every 900' prose form"),
    (r"\(default[^\n)]{0,20}900\)", "the '(default 900)' help-text form"),
    (r"PATROL_INTERVAL[^\n]*900", "a PATROL_INTERVAL default of 900"),
    (r"patrol\.py[^\n]*\s-v\b", "the removed -v flag on a patrol.py command line"),
)


def test_doc_surfaces_carry_the_new_defaults():
    for name in DOC_SURFACES:
        text = (REPO / name).read_text()
        for pattern, what in STALE_DOC_PATTERNS:
            assert not re.search(pattern, text), (
                f"{name} carries a stale default: {what} — every surface "
                f"that names the cadence or the output flag states the "
                f"post-#25/#27 truth (system-overview.md rule 16)"
            )


def test_source_and_containerfile_carry_the_new_defaults():
    # patrol.py: module docstring + --interval help text + flag form.
    for pattern, what in (
        (r"--interval[^\n]*\s900\b", "the --interval 900 flag form"),
        (r"every[^\n]{0,30}(15\s*min|900)", "the 'every 15 min' prose form"),
        (r"\(default[^\n)]{0,20}900\)", "the '(default 900)' help-text form"),
    ):
        assert not re.search(pattern, SOURCE), (
            f"patrol.py carries a stale default: {what} "
            f"(system-overview.md rule 16)"
        )
    # Containerfile: the loop comment names the cadence.
    cf = (REPO / "Containerfile").read_text()
    assert not re.search(r"every[^\n]{0,30}900", cf), (
        "the Containerfile loop comment must not name the stale 900 s "
        "cadence (system-overview.md rule 16)"
    )
