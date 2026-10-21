"""Efficient-sweep contract tests (#20).

The layered-sweep and field-selection teaching in the system prompt
(patrol-engine.md rules 3a-3e) and the dry-cluster fixtures that model the
taught sweep (rules 13-13a), driven directly against `kubectl_readonly` in dry
mode — no engine, no model. The guard truncation marker's recovery hint
(kubectl-guard.md rule 11) is covered in test_kubectl_guard.py with the other
rule-11 checks.

These tests fail until #20's implementation lands: the current prompt teaches a
broad flat sweep, the fixture table has no sweep fixtures (every command
containing `get pods -A` gets the flat table), and warning events have no
fixture at all.

Contract: .ai/spec/what/patrol-engine.md rules 3a-3e, 13-13a.

Run: uv run --group dev pytest tests/ -q
"""
import patrol


def guard(command):
    """One guarded call in dry mode (the fixture table answers)."""
    return patrol.kubectl_readonly(command, kubectl="oc", dry=True, verbose=False)


# The jsonpath sweep template exactly as rule 3c teaches it (and the prompt
# shows it): waiting reasons and restart counts, one line per pod.
TAUGHT_SWEEP = (
    "get pods -A -o jsonpath='{range .items[*]}{.metadata.namespace}/"
    "{.metadata.name}{\" \"}{.status.containerStatuses[*].state.waiting.reason}"
    "{\" restarts=\"}{.status.containerStatuses[*].restartCount}{\"\\n\"}{end}'"
)


# ------------------------------------------------- prompt teaching (rules 3a-3e)

def test_prompt_teaches_the_layered_sweep():
    """Rule 3a: the prompt names the six layers — operators, nodes, namespaces,
    workload objects, problem pods, warning events — not a vague broad sweep,
    and names them in the taught order (first occurrence of each layer's
    command)."""
    p = patrol.SYSTEM_PROMPT
    for token in ("clusteroperators", "get nodes", "get namespaces",
                  "deployments", "daemonsets", "statefulsets", "type=Warning"):
        assert token in p, f"the prompt must teach the layered sweep ({token!r} missing)"
    positions = [p.index(t) for t in
                 ("clusteroperators", "get nodes", "get namespaces",
                  "deployments", "type=Warning")]
    assert positions == sorted(positions), "layers must be taught in the rule 3a order"


def test_prompt_teaches_problem_pod_selection():
    """Rule 3b: per-phase equality selectors for problem pods, with the oc
    `!=` portability caveat."""
    p = patrol.SYSTEM_PROMPT
    assert "field-selector" in p
    assert "status.phase=Pending" in p
    assert "status.phase=Failed" in p
    assert "status.phase!=Running" in p   # the inequality-selector caveat, exact form


def test_prompt_teaches_the_crashloop_sweep():
    """Rule 3c: CrashLoopBackOff is invisible to phase selectors — the prompt
    shows the client-side jsonpath reasons/restarts sweep."""
    p = patrol.SYSTEM_PROMPT
    assert "CrashLoopBackOff" in p
    assert "jsonpath" in p
    assert "containerStatuses" in p  # the sweep template's field
    assert "restartCount" in p


def test_prompt_teaches_field_selection_over_full_dumps():
    """Rule 3d: full -o json/-o yaml reserved for single-object drilldowns,
    never lists; client-side formatters for list-shaped needs."""
    p = patrol.SYSTEM_PROMPT
    assert "single-object" in p
    assert "custom-columns" in p


def test_prompt_teaches_leaning_on_the_wiki():
    """Rule 3e: on a populated wiki, rechecks and Baselines first — the full
    sweep is for a dry or contradicted wiki."""
    p = patrol.SYSTEM_PROMPT
    assert "Baseline" in p
    assert "re-derive" in p


# ------------------------------------------- dry-cluster fixtures (rules 13-13a)

def test_workload_object_fixtures_answer():
    """Rule 13: the workload-object tables exist (one row per workload), and
    the demo deployment's 1/2 READY flags the crash-looping workload — the
    payoff layer 4 promises."""
    for cmd in ("get deployments -A", "get daemonsets -A", "get statefulsets -A"):
        out = guard(cmd)
        assert "no fixture" not in out, f"{cmd!r} must have a fixture, got {out!r}"
    out = guard("get deployments -A")
    assert "demo-app" in out and "1/2" in out


def test_phase_sweeps_answer_no_resources_not_the_flat_table():
    """Rule 13: the phase sweeps answer 'no resources found' — the demo's
    problem pods are crash-looping (phase Running), rule 3c's lesson live."""
    for phase in ("Pending", "Failed"):
        out = guard(f"get pods -A --field-selector=status.phase={phase}")
        assert "No resources found" in out, f"{phase} sweep: {out!r}"
        assert "CrashLoopBackOff" not in out, (
            f"{phase} sweep fell through to the flat pods table (rule 13a ordering)")


def test_phase_sweep_matches_both_flag_forms():
    """Rule 13a: sweep keys are selector values, not flag forms — both
    `--field-selector=x` and `--field-selector x` match the phase fixtures."""
    eq = guard("get pods -A --field-selector=status.phase=Pending")
    sp = guard("get pods -A --field-selector status.phase=Pending")
    assert "No resources found" in eq
    assert "No resources found" in sp


def test_crashloop_sweep_fixture_answers():
    """Rule 13: the jsonpath sweep of rule 3c answers with one
    `namespace/name <reason> restarts=N` line per pod — consistent with the
    flat pods table (same pods, same counts), and not a table dump."""
    out = guard(TAUGHT_SWEEP)
    assert "demo-app/demo-6b5c-xyz CrashLoopBackOff restarts=128" in out, out
    assert "restarts=0" in out         # healthy pods are included in the sweep
    assert "READY" not in out          # the sweep view, not the flat table


def test_warning_events_fixture_answers():
    """Rule 13: warning events answer with the demo's two problems — the
    crash-loop BackOff and the NotReady worker's NodeNotReady."""
    out = guard("get events -A --field-selector=type=Warning")
    assert "no fixture" not in out, out
    assert "BackOff" in out and "NodeNotReady" in out


def test_flat_pods_table_still_answers():
    """Rule 13: the flat `get pods -A` table stays answerable — the guard
    permits flat enumeration and the demo data stays honest (problem and
    healthy pods both present)."""
    out = guard("get pods -A")
    assert "CrashLoopBackOff" in out and "Running" in out


def test_unfiltered_event_firehose_has_no_fixture():
    """Rule 13a: the events fixture is keyed on `type=Warning`, not
    `get events` — the unfiltered firehose is layer 6's anti-pattern and dry
    mode deliberately does not serve it."""
    out = guard("get events -A")
    assert "no fixture" in out
