"""Cost control — token soft-stop, decoupled soft target, head+tail
truncation (#10 spec PR).

Real-run evidence (#10): patrols ranged 216K–1M input tokens (patrol #6
burned turns fighting OUT_CAP truncation on node stats dumps), and GLM
self-terminated at 34/36 turns keying off the "~30 turns" soft target in
the prompt — the stated target, not the hard budget, was driving
termination.

The contracts (patrol-engine.md rules 8a + 8b, kubectl-guard.md rule 11,
ADR 0027):
- Rule 8a: the prompt's turn target is the hard-coded `SOFT_TARGET_TURNS`
  (20), stated distinctly from the hard `max_steps` budget.
- Rule 8b: exploration is bounded by cumulative input tokens
  (`PATROL_TOKEN_BUDGET` / `--token-budget`, default 500,000; `0`
  disables); crossing ends the patrol through the budget-cut path.
- Rule 11 (guard): truncated tool output keeps `OUT_HEAD` 9,000 head chars
  and `OUT_TAIL` 5,000 tail chars around a middle-elision marker; the #20
  recovery hint follows the tail; dry-mode fixture capping unchanged.

Unit pins here: the constants, the CLI flag surface, and the guard's
head+tail truncation matrix. The prompt and token-cut engine wiring are
pinned by the e2e in test_patrol_deepagents.py; the flipped head-only
truncation tests live in test_kubectl_guard.py.

Run: uv run --group dev pytest tests/ -q
"""
import re
from pathlib import Path

import patrol


REPO = Path(__file__).resolve().parent.parent
PATROL = REPO / "patrol.py"
SOURCE = PATROL.read_text()


# ------------------------------------------------------------- rule 8a: soft target

def test_soft_target_constant():
    """Rule 8a: the soft turn target is its own hard-coded constant, 20 —
    not derived from max_steps."""
    assert patrol.SOFT_TARGET_TURNS == 20


def test_soft_target_is_not_the_max_steps_default():
    """Rule 8a: decoupled — the target (20) differs from the hard budget's
    default (30), so tuning one cannot silently move the other."""
    assert patrol.SOFT_TARGET_TURNS != patrol.MAX_STEPS


# ------------------------------------------------------------- rule 8b: token budget

def test_token_budget_default_constant():
    """Rule 8b: the cumulative input-token budget default, as a specced
    constant."""
    assert patrol.TOKEN_BUDGET == 500_000


def test_token_budget_cli_default_from_env():
    """Rule 8b: --token-budget defaults from the PATROL_TOKEN_BUDGET env
    fallback (the --max-steps pattern); 0 disables the stop."""
    m = re.search(
        r'"--token-budget",[^)]*?'
        r'default=TOKEN_BUDGET',
        SOURCE,
    )
    assert m, (
        "patrol.py must wire --token-budget with the TOKEN_BUDGET default "
        "(patrol-engine.md rule 8b)"
    )
    m2 = re.search(
        r'TOKEN_BUDGET\s*=\s*int\(os\.environ\.get\(\s*"PATROL_TOKEN_BUDGET",\s*"(\d+)"\s*\)\)',
        SOURCE,
    )
    assert m2, (
        "TOKEN_BUDGET must read the PATROL_TOKEN_BUDGET env fallback at "
        "import (the MAX_STEPS pattern, patrol-engine.md rule 8b)"
    )
    assert m2.group(1) == "500000"


# --------------------------------------------------- rule 11 (guard): head+tail cap

def test_head_tail_constants():
    """Rule 11: the head+tail split, as specced constants — and the split
    stays at or under the OUT_CAP envelope."""
    assert patrol.OUT_HEAD == 9_000
    assert patrol.OUT_TAIL == 5_000
    assert patrol.OUT_HEAD + patrol.OUT_TAIL <= patrol.OUT_CAP


def _fake_cli(tmp_path):
    """A fake cluster CLI whose `get nodes` prints a distinguishable
    head...tail payload (the test_kubectl_guard.py pattern, with markers)."""
    cli = tmp_path / "kubectl"
    payload = "HEADMARK" + "x" * (patrol.OUT_CAP + 5000) + "TAILMARK"
    cli.write_text(f'#!/bin/sh\nprintf \'%s\\n\' "{payload}"\n')
    cli.chmod(0o755)
    return str(cli), payload + "\n"


def test_truncation_keeps_head_and_tail(tmp_path):
    """Rule 11 (#10): truncated output keeps BOTH informative ends — the
    head (table headers, oldest rows) and the tail (logs/events recency).
    Head-only truncation kept one and destroyed the other."""
    cli, payload = _fake_cli(tmp_path)
    out = patrol.kubectl_readonly("get nodes", kubectl=cli, dry=False,
                                  verbose=False)
    assert "HEADMARK" in out, "the head of the output must survive the cap"
    assert "TAILMARK" in out, "the tail of the output must survive the cap"


def test_truncation_elision_marker_names_total_and_middle(tmp_path):
    """Rule 11 (#10): the marker between head and tail names the true total
    and the elided middle: `...[truncated, {N} chars total: middle {M} chars
    elided, head+tail kept]` with M = N - OUT_HEAD - OUT_TAIL."""
    cli, payload = _fake_cli(tmp_path)
    out = patrol.kubectl_readonly("get nodes", kubectl=cli, dry=False,
                                  verbose=False)
    n = len(payload)
    m = n - patrol.OUT_HEAD - patrol.OUT_TAIL
    assert f"[truncated, {n} chars total: middle {m} chars elided, head+tail kept]" in out
    # the marker sits between the two kept ends
    assert out.index("HEADMARK") < out.index("[truncated,") < out.index("TAILMARK")


def test_truncation_hint_follows_the_tail(tmp_path):
    """Rule 11 (#10): the #20 recovery hint still follows — after the tail,
    naming the narrowing patterns."""
    cli, payload = _fake_cli(tmp_path)
    out = patrol.kubectl_readonly("get nodes", kubectl=cli, dry=False,
                                  verbose=False)
    assert "field-selector" in out
    assert "TAILMARK" in out
    assert "jsonpath" in out
    assert out.index("TAILMARK") < out.index("field-selector")


def test_truncation_length_bound(tmp_path):
    """Rule 11 (#10): head + tail + marker + hint never exceeds
    OUT_HEAD + OUT_TAIL + 250 — the suffix can never rival the cap it
    explains."""
    cli, payload = _fake_cli(tmp_path)
    out = patrol.kubectl_readonly("get nodes", kubectl=cli, dry=False,
                                  verbose=False)
    assert len(out) <= patrol.OUT_HEAD + patrol.OUT_TAIL + 250


def test_short_output_is_not_truncated(tmp_path):
    """Rule 11 (unchanged): output under the cap passes through whole."""
    cli = tmp_path / "kubectl"
    cli.write_text('#!/bin/sh\necho "all good"\n')
    cli.chmod(0o755)
    out = patrol.kubectl_readonly("get nodes", kubectl=str(cli), dry=False,
                                  verbose=False)
    assert out == "all good"
    assert "truncated" not in out
