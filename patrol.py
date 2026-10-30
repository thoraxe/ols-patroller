#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = [
#     "deepagents>=0.7",
#     "langchain-openai>=1.0",
#     "pydantic>=2",
#     "pyyaml>=6",
#     "apprise>=1.9",
# ]
# ///
"""
Cluster Sentinel — hacky PoC (deepagents engine).

A proactive, non-deterministic SRE "patrol": on each run an LLM agentically explores
the live cluster read-only (it decides what to look at), validates reality against a
persistent markdown "wiki" (its memory), rewrites the wiki, and notifies a human about
anything that looks wrong.

Read-only by design: the agent is built with `create_deep_agent()` and NO backend, so
it has no shell and no filesystem tools. The single tool exposed to the model is a
whitelisted `oc/kubectl` that only permits non-mutating verbs. Nothing here ever
changes the cluster. The final report (findings / notifications / updated wiki) is
collected as typed structured output instead of hand-parsed JSON.

Engine alignment: same upstream `deepagents` library the OLS agentic sandbox uses
(depending on it directly — no OLS code is imported).

Run:
    export PATROL_API_KEY=...        # API key for your OpenAI-compatible endpoint
    export PATROL_BASE_URL=...       # e.g. https://api.example.com/v1
    export PATROL_MODEL=...          # model id on that endpoint
    uv run patrol.py --once
    uv run patrol.py --once --dry-cluster     # no cluster needed; uses canned fixtures
    uv run patrol.py --loop --interval 21600   # every 6 h (the default cadence)

Point it at any OpenAI-compatible chat-completions endpoint via env (see below).
"""
from __future__ import annotations

import argparse
import asyncio
import os
import re
import shlex
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Literal

import apprise
import yaml
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langchain_core.tools import tool
from langchain_openai import ChatOpenAI
from langgraph.errors import GraphRecursionError
from pydantic import BaseModel, Field

from deepagents import (
    GeneralPurposeSubagentProfile,
    HarnessProfile,
    create_deep_agent,
    register_harness_profile,
)
from deepagents.middleware.filesystem import FilesystemMiddleware

# ------------------------------------------------------------- notify helpers

def parse_notify_urls(value: str) -> list[str]:
    """Rule 10a (#9): split PATROL_NOTIFY_URLS on commas and newlines, strip
    whitespace, drop empty entries. Unset/empty -> zero sinks (console only)."""
    return [part for part in (p.strip() for p in value.replace("\n", ",").split(",")) if part]


def report_notify_type(findings: list[dict]) -> str:
    """Rule 10b (#9): patrol-level severity -> apprise notify_type. Any crit
    finding -> FAILURE, else WARNING (INFO never occurs — notifications are
    warn/crit only, rule 4)."""
    return "FAILURE" if any(f.get("severity") == "crit" for f in findings) else "WARNING"


# --------------------------------------------------------------------------- config
BASE_URL   = os.environ.get("PATROL_BASE_URL", "")   # e.g. https://api.example.com/v1
MODEL      = os.environ.get("PATROL_MODEL", "")      # model id on that endpoint
API_KEY    = os.environ.get("PATROL_API_KEY", "")
KUBECTL    = os.environ.get("KUBECTL", "")  # resolved later
NOTIFY_URLS = parse_notify_urls(os.environ.get("PATROL_NOTIFY_URLS", ""))
MAX_STEPS  = int(os.environ.get("PATROL_MAX_STEPS", "30"))  # LLM turns per patrol
TOOL_TIMEOUT = int(os.environ.get("PATROL_TOOL_TIMEOUT", "60"))
# Per-call-shape request bounds (reporting-notifications.md rule 8a, #49; ADR
# 0018). Hard-coded, not env-configurable: exploration turns are small and
# fast, so a hung endpoint fails snappily at 300 s — while the single
# tool-free structured reporting call (and its repair pass) carries the
# entire patrol conversation and legitimately runs minutes on
# reasoning-capable models, so it gets the 900 s headroom. A reporting pass
# that cannot finish under the bound never writes the wiki, and a fresh
# cluster never bootstraps its memory.
REQUEST_TIMEOUT = 300  # seconds per exploration turn
REPORT_TIMEOUT  = 900  # seconds per structured reporting/repair call
REQUEST_MAX_TOKENS = 16_000  # completion tokens per exploration turn
REPORT_MAX_TOKENS  = 64_000  # completion tokens per structured reporting/repair call
OUT_CAP    = 15_000  # max chars of tool output fed back to the model
# Shape-pass compaction cap (reporting-notifications.md rule 7e, #66): the
# tool-result keep budget for `compact_messages` — every shape-pass invocation
# carries the compacted conversation. Hard-coded, not env-configurable (the
# same convention as the rules-8a/8b request/token bounds): the shape pass's
# input grew with the wiki until it flirted with the model gateway's ~900 s
# request ceiling (both attempts 504ing at ~902 s on one soaked patrol), and a
# client-side timeout raise cannot help while the *gateway* caps the request —
# only shrinking what the pass carries can. A 1500-character excerpt of any
# tool result is more citation than the report contract asks for; the full
# text stays in the patrol's own transcript (pod logs) and the wiki's prose.
SHAPE_TOOL_KEEP = 1500  # chars of each tool result the shape pass carries
CLUSTER_ID = os.environ.get("PATROL_CLUSTER_ID", "local")  # wiki commit identity
WIKI_REMOTE = os.environ.get("PATROL_WIKI_REMOTE", "")     # optional wiki replication
# Wiki push bounds (wiki-memory.md rule 4e). Read at call time, not import time,
# so tests can bound retries.
PUSH_ATTEMPTS = 3  # max push attempts
PUSH_TIMEOUT  = 30 # seconds per push attempt
PUSH_BACKOFF  = 5  # seconds between push attempts

# Wiki skeleton schema (wiki-memory.md rules 10-12, #5). WIKI_VERSION is the
# skeleton schema version: bumping it is a deliberate spec migration, never a
# patrol decision (rule 10). Not env-configurable.
WIKI_VERSION = 1
WIKI_SECTIONS = ("Topology", "Baselines", "Watch-list", "Open questions", "Anomaly log")
WIKI_METADATA_SECTIONS = ("Watch-list", "Anomaly log")  # sections carrying entry metadata
WIKI_FRONTMATTER_KEYS = ("wiki_version", "cluster_id", "patrol_count", "last_patrol")
WIKI_ENTRY_KEYS = ("id", "status", "first_seen", "last_verified", "source", "confidence")
WIKI_ENTRY_STATUSES = ("watching", "resolved", "expired")
WIKI_ENTRY_CONFIDENCES = ("high", "medium", "low")
WIKI_ENTRY_RECHECK = "recheck"  # the recheck metadata key (rule 12a, #6)
WIKI_ENTRY_VERIFIED_PATROL = "verified_patrol"  # the staleness-clock key (rule 20, #7)

# Wiki health stats (wiki-memory.md rules 23-27, #8; ADR 0015). Hard-coded
# like OUT_CAP, not env-configurable — retuning them is a spec change, not a
# config knob. The churn window mirrors the default PATROL_WIKI_STALE_AFTER
# grace: one commit ~ one accepted write, so both count the same unit.
WIKI_CHURN_WINDOW = 5               # revisions (one per commit) churn examines
WIKI_STATS_LOPSIDED_MIN = 5         # graded entries before a spread can be lopsided
WIKI_STATS_LOPSIDED_SHARE = 2 / 3   # dominant-level share that fires the warning

# Wiki working-set budgets (wiki-memory.md rules 28-29, #40; ADR 0017). Git
# history is the archive; the wiki is the working set: the Anomaly log is
# mechanically bounded (the keep budget — every watching entry plus the last
# N resolved/expired entries survive each accepted write), and the whole wiki
# is softly budgeted (the drift alarm — warns, never gates validation).
# Hard-coded like the stats constants, not env-configurable — retuning them
# is a spec change, not a config knob. Read at call time (the PUSH_* pattern)
# so tests can bound them.
WIKI_ANOMALY_KEEP = 20     # last resolved/expired Anomaly-log entries kept
WIKI_CHAR_BUDGET = 20_000  # soft whole-wiki character budget (whole file)


def _stale_after() -> int:
    """PATROL_WIKI_STALE_AFTER as a non-negative int (rule 21, #7).

    The accepted-write threshold for stale-entry expiry: a Watch-list entry
    whose last_verified has not advanced for this many accepted writes is
    auto-expired into the Anomaly log. Default 5; 0 disables the flip (the
    verified_patrol clock is still stamped, so re-enabling resumes with true
    history). Read once at import; an unparseable or negative value fails
    startup with a config error — the patrol never runs on a silently wrong
    expiry threshold.
    """
    raw = os.environ.get("PATROL_WIKI_STALE_AFTER", "5")
    try:
        value = int(raw)
    except ValueError:
        value = -1
    if value < 0:
        print(f"[patrol] ERROR: PATROL_WIKI_STALE_AFTER must be a non-negative "
              f"integer (got {raw!r})", file=sys.stderr)
        sys.exit(1)
    return value


STALE_AFTER = _stale_after()

READONLY_VERBS = {
    "get", "describe", "logs", "top", "version", "api-resources",
    "api-versions", "explain", "status", "cluster-info", "whoami",
}

SYSTEM_PROMPT = """\
You are a resident Site Reliability Engineer doing a ROUTINE PATROL of an OpenShift/Kubernetes cluster.
You are NOT responding to an alert. You are proactively "doing your rounds", like a good SRE who keeps
looking at things instead of waiting for alarms.

Hard rules:
- You can ONLY observe. You have a single tool, `kubectl_readonly`, restricted to read-only verbs.
- You cannot and must not change anything in the cluster.
- The WIKI provided below is your own prior notes. It may be WRONG, STALE, or INCOMPLETE.
  The live cluster is the single source of truth. Re-validate wiki claims against reality and
  correct/expire anything stale. Never trust the wiki over what you actually observe.
- Do NOT re-investigate anything already covered by the listed in-flight AgenticRuns/alerts.

Your patrol, in order:
1. Map how the cluster is deployed and wired (topology): operators, key workloads, namespaces,
   how major pieces connect. Map it as a LAYERED SWEEP — one bounded query per layer, drilling
   into whatever looks off — never a flat `get pods -A` enumeration (on a thousands-of-pods
   cluster that is thousands of rows, truncated mid-table at the output cap, while the layered
   sweep answers the same questions in hundreds of rows):
     1. `get clusteroperators` — cluster-level health
     2. `get nodes` — capacity/scheduling
     3. `get namespaces` — footprint
     4. `get deployments -A`, `get daemonsets -A`, `get statefulsets -A` — workload topology:
        one row per workload instead of N rows per replica, and the READY column flags rollout
        problems for free
     5. problem pods only (see below)
     6. `get events -A --field-selector=type=Warning` — warnings, not the event firehose
   Targeted drilldowns (`describe`, single-object `get`) are always allowed on top of the sweep.
   Select problem pods with per-phase equality selectors (`--field-selector=status.phase=Pending`,
   `--field-selector=status.phase=Failed`); avoid inequality selectors — `status.phase!=Running`
   is not reliable on `oc`; equality per problem phase is the portable form.
   CrashLoopBackOff is invisible to phase selectors: a crash-looping pod's status.phase stays
   Running while its containers crash-loop. Sweep waiting reasons and restart counts client-side
   (output stays bounded, and you get absolute values instead of the table's relative ages):
     get pods -A -o jsonpath='{range .items[*]}{.metadata.namespace}/{.metadata.name}{" "}{.status.containerStatuses[*].state.waiting.reason}{" restarts="}{.status.containerStatuses[*].restartCount}{"\\n"}{end}'
   Prefer field selection over full dumps: full `-o json`/`-o yaml` is reserved for single-object
   drilldowns (`get node worker-1 -o yaml`) — never dump whole lists (they cost roughly 5-10x
   the table tokens for the same objects). For list-shaped needs use `--field-selector`,
   `-o jsonpath` or custom-columns, and `--no-headers` for name-only lists.
2. Compare what you see against the WIKI. Note drift, new/removed components, anomalies.
   Watch-list entries carry a `recheck:` command — the read-only invocation that
   re-verifies the entry. Run an entry's recheck FIRST when re-checking it, and
   rewrite a recheck whose verification path has changed (keep it current).
   On a populated wiki, lean on your memory instead of re-deriving it: run Watch-list rechecks
   first, verify Baselines rather than re-mapping topology, and reserve a full sweep for an
   area where the wiki is stale, contradicted, or silent — never re-derive a map the wiki
   already holds. The layered sweep above is the dry-wiki (first-patrol) map.
3. Decide what a human should actually care about (don't cry wolf).
4. Produce an UPDATED wiki and a refined watch-list for the next patrol.

When you are DONE exploring, stop calling tools and write your final summary as plain text:
your key findings with severity and the evidence you saw, what you verified or corrected
against the wiki, and what the next patrol should watch. Use the severity vocabulary
info (note/baseline/expected), warn (drift/degradation/suspicious), crit (broken,
needs a human now). Do not keep exploring once you have covered the main areas — a
complete, honest summary of a focused patrol beats an exhaustive sweep. A separate
reporting step will format your patrol into its final structured report, so write
for that reader: concrete, specific, no filler.
"""

WIKI_SEED = """\
---
wiki_version: 1
cluster_id: local
patrol_count: 0
last_patrol: null
---

# Cluster Wiki

_No patrols have run yet. This file is the sentinel's memory and will be wholly rewritten
each patrol. The frontmatter counters are stamped by the patroller, not the model._

## Topology
_(unknown — first patrol will populate)_

## Baselines
_(unknown)_

## Watch-list
_(empty)_

## Open questions
- (none yet)

## Anomaly log
_(empty)_
"""

# ------------------------------------------------------------------- dry-cluster fixtures
# Minimal canned outputs so the agent loop can be demoed without a real cluster.
# Keyed by a substring of the command; first match wins. The set models the
# taught sweep (patrol-engine.md rules 13-13a): workload-object tables, phase
# sweeps, the crash-loop sweep, and warning events. Every problem-pod sweep
# key sits BEFORE the flat `get pods -A` key — each problem-pod sweep command
# contains that substring too, so such a key placed after it would never fire
# (the events key `type=Warning` is position-independent: events commands
# never contain `get pods -A`). Keys are the
# distinctive selector values (`status.phase=Pending`, `type=Warning`), not
# flag forms, so both `--field-selector=x` and `--field-selector x` match; the
# unfiltered event firehose (`get events` with no selector) is deliberately
# unserved.
FIXTURES: list[tuple[str, str]] = [
    ("version", "Client Version: 4.16.0\nServer Version: 4.16.7\nKubernetes Version: v1.29.6"),
    ("get clusteroperators", (
        "NAME            VERSION   AVAILABLE   PROGRESSING   DEGRADED\n"
        "authentication  4.16.7    True        False         False\n"
        "console         4.16.7    True        False         False\n"
        "ingress         4.16.7    True        True          False\n"
        "monitoring      4.16.7    True        False         True\n"
    )),
    ("get nodes", (
        "NAME       STATUS   ROLES                  AGE   VERSION\n"
        "master-0   Ready    control-plane,master   40d   v1.29.6\n"
        "master-1   Ready    control-plane,master   40d   v1.29.6\n"
        "master-2   Ready    control-plane,master   40d   v1.29.6\n"
        "worker-0   Ready    worker                 40d   v1.29.6\n"
        "worker-1   NotReady worker                 40d   v1.29.6\n"
    )),
    ("get namespaces", (
        "NAME                     STATUS   AGE\n"
        "openshift-lightspeed     Active   12d\n"
        "openshift-monitoring     Active   40d\n"
        "openshift-ingress        Active   40d\n"
        "demo-app                 Active   3d\n"
    )),
    # Rule 3a layer 4: workload topology, one row per workload. The demo
    # deployment's 1/2 READY is the crash-looping workload — its two replicas
    # are the flat pods table's two demo-app pods, one of them down.
    ("get deployments -A", (
        "NAMESPACE              NAME             READY   UP-TO-DATE   AVAILABLE   AGE\n"
        "openshift-lightspeed   lightspeed-app   1/1     1            1           2d\n"
        "demo-app               demo             1/2     1            1           3d\n"
    )),
    ("get daemonsets -A", "No resources found in all namespaces."),
    ("get statefulsets -A", (
        "NAMESPACE              NAME             READY   AGE\n"
        "openshift-monitoring   prometheus-k8s   1/1     40d\n"
    )),
    # Rule 3b phase sweeps: the demo's problem pods are crash-looping (phase
    # Running), so both sweeps come up empty — rule 3c's lesson live.
    ("status.phase=Pending", "No resources found in all namespaces."),
    ("status.phase=Failed", "No resources found in all namespaces."),
    # Rule 3c crash-loop sweep: one namespace/name reason restarts line per
    # pod, healthy pods included — same pods, same counts as the flat table.
    ("containerStatuses", (
        "openshift-lightspeed/lightspeed-app-7c9-abc  restarts=0\n"
        "openshift-monitoring/prometheus-k8s-0  restarts=0\n"
        "demo-app/demo-6b5c-xyz CrashLoopBackOff restarts=128\n"
        "demo-app/demo-6b5c-pqr  restarts=0\n"
    )),
    # Flat enumeration stays answerable: the guard permits it and the demo
    # data stays honest.
    ("get pods -A", (
        "NAMESPACE              NAME                         READY   STATUS             RESTARTS   AGE\n"
        "openshift-lightspeed   lightspeed-app-7c9-abc       1/1     Running            0          2d\n"
        "openshift-monitoring   prometheus-k8s-0             6/6     Running            0          40d\n"
        "demo-app               demo-6b5c-xyz                0/1     CrashLoopBackOff   128        3d\n"
        "demo-app               demo-6b5c-pqr                1/1     Running            0          3d\n"
    )),
    # Rule 3a layer 6: warnings only, naming the demo's two problems.
    ("type=Warning", (
        "NAMESPACE     LAST SEEN   TYPE      REASON         OBJECT             MESSAGE\n"
        "demo-app      2m          Warning   BackOff        pod/demo-6b5c-xyz  Back-off restarting failed container\n"
        "default       5m          Warning   NodeNotReady   node/worker-1      Node worker-1 status is now: NotReady\n"
    )),
    ("get agenticruns", "No resources found.  (agentic.openshift.io CRD may not be installed)"),
]


# --------------------------------------------------------------------------- report schema
class Finding(BaseModel):
    """One observation from the patrol."""
    severity: Literal["info", "warn", "crit"] = Field(
        description="info = note/baseline/expected; warn = drift/degradation/suspicious; "
                    "crit = broken, needs a human now")
    summary: str = Field(description="Short human-readable summary")
    evidence: str = Field(default="", description="Short citation of what was actually observed")


class PatrolReport(BaseModel):
    """Final structured output of one patrol."""
    findings: list[Finding] = Field(default_factory=list,
                                    description="Everything worth recording from this patrol")
    notifications: list[str] = Field(
        default_factory=list,
        description="ONLY warn/crit items a human should actually see, as human-readable messages")
    updated_wiki: str = Field(default="",
                              description="FULL new markdown content of the cluster wiki")


# --------------------------------------------------------------------------- helpers
def die(msg: str, code: int = 1) -> None:
    print(f"[patrol] ERROR: {msg}", file=sys.stderr)
    sys.exit(code)


def resolve_kubectl() -> str:
    if KUBECTL:
        return KUBECTL
    for c in ("oc", "kubectl"):
        if subprocess.run(["bash", "-lc", f"command -v {c}"],
                          capture_output=True).returncode == 0:
            return c
    return "oc"  # will fail loudly later if truly absent


def fixture_for(args: list[str]) -> str:
    cmd = " ".join(args)
    for key, out in FIXTURES:
        if key in cmd:
            return out
    avail = ", ".join(f"`{k}`" for k, _ in FIXTURES)
    return (f"(dry-cluster) no fixture for: {cmd}. This demo cluster only has canned "
            f"fixtures for: {avail}. Treat any other resource as absent — do not retry "
            f"variations of this command.")


def guard_refusal(command: str) -> str | None:
    """The guard's pre-execution refusal grammar (kubectl-guard.md rules 2-6a):
    parseability, non-empty, verb whitelist, secret-value protection, raw-path
    refusal. Returns the REFUSED message for a command the guard would refuse,
    None for one it accepts. Pure — no I/O, nothing executed.

    Single source of truth for "a command kubectl_readonly accepts"
    (ADR 0013): the wiki validator judges `recheck` citations with this same
    predicate (wiki-memory.md rule 12a), never by executing them.
    kubectl_readonly delegates its refusals here; behavior is unchanged.
    """
    try:
        args = shlex.split(command)
    except ValueError as e:
        return f"REFUSED: could not parse command ({e})"
    if not args:
        return "REFUSED: empty command"

    verb = args[0]
    if verb not in READONLY_VERBS:
        return (f"REFUSED: '{verb}' is not a read-only verb. "
                f"Allowed: {sorted(READONLY_VERBS)}")

    # Never leak Secret VALUES.
    lower = " ".join(args).lower()
    if "secret" in lower and any(f in lower for f in ("-o yaml", "-o json", "jsonpath", "-o go-template")):
        return "REFUSED: reading Secret values is off-limits. Use `get secrets` (names/metadata) only."

    # Never serve raw proxy/stats paths: they dump multi-hundred-KB output that
    # truncates at OUT_CAP and gets retried (a real patrol burned three turns in
    # exactly that loop).
    if "--raw" in lower:
        return ("REFUSED: --raw paths are off-limits. They dump high-volume output "
                "that truncates. Use typed read commands (get/describe/logs/top) "
                "instead of retrying raw paths.")
    return None


def kubectl_readonly(arg_string: str, *, kubectl: str, dry: bool, verbose: bool) -> str:
    """The ONLY tool the model gets. Read-only enforcement is the safety boundary."""
    refusal = guard_refusal(arg_string)
    if refusal:
        return refusal
    args = shlex.split(arg_string)

    if verbose:
        print(f"[patrol]   tool: {kubectl} {' '.join(args)}", file=sys.stderr)

    if dry:
        return fixture_for(args)[:OUT_CAP]

    try:
        proc = subprocess.run([kubectl, *args], capture_output=True, text=True,
                              timeout=TOOL_TIMEOUT)
    except FileNotFoundError:
        return f"REFUSED: '{kubectl}' not found on PATH. Use --dry-cluster or install oc/kubectl."
    except subprocess.TimeoutExpired:
        return f"ERROR: command timed out after {TOOL_TIMEOUT}s"
    out = (proc.stdout or "") + (("\n[stderr]\n" + proc.stderr) if proc.stderr else "")
    if len(out) > OUT_CAP:
        # Rule 11 (#20): after the unchanged marker, a recovery hint naming the
        # narrowing patterns the prompt teaches (patrol-engine.md rules 3a-3d)
        # — the model recovers systematically instead of retrying the same
        # dump. Marker plus hint stay under 250 chars so the suffix can never
        # rival the cap it explains. Output shaping only — the refusal grammar
        # is untouched (ADR 0016); dry-mode fixture capping above stays
        # marker-free and hint-free (rule 13: fixture text is demo data).
        out = (out[:OUT_CAP]
               + f"\n...[truncated, {len(out)} chars total]"
               + "\nRecover by narrowing, not retrying: --field-selector=..., "
                 "-o jsonpath or custom-columns projections, or a single-object "
                 "drilldown (describe / get <one object>).")
    return out.strip() or "(no output)"


def make_kubectl_tool(*, kubectl: str, dry: bool, verbose: bool):
    """Wrap the guarded read-only kubectl as the single langchain tool."""
    @tool("kubectl_readonly")
    def _kubectl_tool(command: str) -> str:
        """Run a READ-ONLY oc/kubectl command and return its output.

        Only non-mutating verbs are permitted (get, describe, logs, top, version,
        api-resources, api-versions, explain, status, cluster-info, whoami).
        Pass everything after `oc` as a single string, e.g. 'get pods -A' or
        'describe node worker-1'.

        Args:
            command: args after oc/kubectl, e.g. 'get clusteroperators'
        """
        return kubectl_readonly(command, kubectl=kubectl, dry=dry, verbose=verbose)
    return _kubectl_tool


def make_model(*, timeout: float = REQUEST_TIMEOUT,
               max_tokens: int = REQUEST_MAX_TOKENS) -> ChatOpenAI:
    """Any OpenAI-compatible endpoint, same env contract as the raw-requests
    version. The request bounds are parametrized per call shape (rules 8a
    + 8b, #49/#63): the defaults are the exploration bounds; the shape pass
    passes the reporting pair. Both are client-level properties, so the two
    call shapes mean two instances from this one seam."""
    return ChatOpenAI(
        model=MODEL,
        base_url=BASE_URL,
        api_key=API_KEY,
        temperature=0.3,
        max_tokens=max_tokens,
        timeout=timeout,
    )


def _disable_default_subagent(model: ChatOpenAI) -> None:
    """Stop deepagents from auto-adding its general-purpose `task` subagent.

    Keeps the patrol single-threaded and the tool surface exactly one tool. Uses a
    private helper to compute the profile key; if deepagents internals drift, we
    degrade gracefully — the default subagent inherits our read-only middleware and
    the same single tool, so it stays safe, just noisier.
    """
    try:
        from deepagents._models import get_model_identifier, get_model_provider

        provider = get_model_provider(model)
        identifier = get_model_identifier(model)
        # Register both the exact model key and the provider-wide fallback, so the
        # profile is found however deepagents resolves the lookup.
        keys = {provider, f"{provider}:{identifier}"} - {"", "None", "None:None"}
        profile = HarnessProfile(
            general_purpose_subagent=GeneralPurposeSubagentProfile(enabled=False)
        )
        for key in keys:
            register_harness_profile(key, profile)
    except Exception as e:  # noqa: BLE001
        print(f"[patrol] note: could not disable default subagent ({e}); "
              "the read-only tool set still applies", file=sys.stderr)


def preview(s: str, n: int = 200) -> str:
    """One-line, length-capped preview for live output."""
    s = s if isinstance(s, str) else str(s)
    s = " ".join((s or "").split())
    return s if len(s) <= n else s[:n] + "…"


def notify(messages: list[str], *, notify_type: str = "WARNING") -> None:
    """Console 🔔 NOTIFY lines (unconditional, rule 9a's pod-log surface) plus
    best-effort apprise delivery to every configured sink (rules 10a–10c, #9).
    Sink failures are logged to stderr and never fatal."""
    for msg in messages:
        print(f"\n[patrol] 🔔 NOTIFY: {msg}")
    if not NOTIFY_URLS:
        return
    app = apprise.Apprise()
    for url in NOTIFY_URLS:
        try:
            if not app.add(url):
                print(f"[patrol]   (apprise refused sink URL {url}; skipped)",
                      file=sys.stderr)
        except Exception as e:  # noqa: BLE001
            print(f"[patrol]   (apprise refused sink URL {url}: {e}; skipped)",
                  file=sys.stderr)
    for msg in messages:
        try:
            if not app.notify(title="Cluster patrol", body=msg,
                              notify_type=notify_type):
                print(f"[patrol]   (apprise delivery failed for: {msg})",
                      file=sys.stderr)
        except Exception as e:  # noqa: BLE001
            print(f"[patrol]   (apprise delivery failed for: {msg}: {e})",
                  file=sys.stderr)


def report_fields(report: object) -> tuple[list[dict], list[str], str]:
    """Normalize the structured response (pydantic model or dict) into plain data."""
    if isinstance(report, PatrolReport):
        return ([f.model_dump() for f in report.findings],
                list(report.notifications), report.updated_wiki)
    if isinstance(report, dict):
        return (report.get("findings") or [],
                [m for m in (report.get("notifications") or []) if m],
                report.get("updated_wiki") or "")
    return ([], [], "")


REPORT_CONTRACT = f"""\
Produce the final PatrolReport for the cluster patrol above:
- "findings": everything worth recording. severity: "info" (note / baseline /
  expected state), "warn" (drift, degradation, something suspicious), "crit"
  (broken, needs a human now). Give each finding short "evidence" citing what was
  actually observed.
- "notifications": ONLY the warn/crit findings a human should actually see (don't
  cry wolf), as human-readable messages.
- "updated_wiki": the FULL new markdown content of the cluster wiki in the
  skeleton format. updated_wiki is required on every report: return the FULL
  wiki content even when nothing needs changing — copy unchanged sections verbatim.
  An empty updated_wiki is never a valid answer: a patrol with nothing to change
  returns the wiki unchanged. It is validated before it may replace the wiki on
  disk, and MUST have exactly this structure:
  1. YAML frontmatter at the very top, between '---' fence lines, with exactly
     four keys and no others: wiki_version: 1, cluster_id (this cluster's id),
     patrol_count (a positive integer), last_patrol (an ISO-8601 datetime). The
     patroller re-stamps cluster_id, patrol_count and last_patrol after
     validation, so fill them sensibly but do not fuss over exact values.
  2. The body contains exactly these five level-2 sections, each exactly once,
     spelled exactly: ## Topology, ## Baselines, ## Watch-list, ## Open
     questions, ## Anomaly log. No other level-2 headings (a ### heading is
     prose, not a section). Section order is free; an empty section may carry a
     non-list placeholder line such as _(empty)_.
  3. In ## Watch-list and ## Anomaly log, every top-level list item (- or *) is
     an entry whose first line is a metadata line: exactly six ' · '-separated
     (space, middle dot, space) key: value pairs in this fixed order —
     id: <unique non-empty id> · status: watching|resolved|expired ·
     first_seen: YYYY-MM-DD · last_verified: YYYY-MM-DD · source: <what was
     observed, typically the command> · confidence: high|medium|low.
     Further 'key: value' pairs may follow confidence; no other trailing text.
     The entry's free prose (the natural-language hypothesis) goes on indented
     continuation lines below the metadata line, never on the line itself.
     Every Watch-list entry's metadata line must also carry a
     recheck: <command> pair: the exact kubectl_readonly argument string that
     re-verifies the entry, with no leading kubectl or oc token — the value is
     everything after the CLI name, e.g. 'recheck: get pods -n demo-app'.
     The recheck is validated against the guard's refusal grammar (read-only
     verbs only, no Secret-value reads, no --raw paths), so it must be a
     command the guard would accept. Anomaly-log entries may carry a recheck
     too (optional). The recheck lives on the metadata line, never in prose.
     Entry metadata lines may also carry a verified_patrol: <int> pair — the
     patroller re-stamps it after validation, so copy or drop it freely and
     never compute it yourself. last_verified must advance ONLY when the entry
     was actually re-verified this patrol (it is the one signal the
     patroller's staleness clock reads): a watching Watch-list entry whose
     last_verified stops advancing is auto-expired by the patroller after
     PATROL_WIKI_STALE_AFTER accepted writes. Re-verify what is worth
     keeping; resolve or expire the rest deliberately.
  4. Topology, Baselines, and Open questions are free prose — no metadata lines
     there; keep tagging their entries with (source, timestamp, confidence)
     inline. Expire stale entries (flip status to expired), never delete them —
     pruning closed Anomaly-log history is the patroller's job, not yours.
  5. The wiki is a working set, not a log: git history is the archive, and the
     wiki is what the next patrol reads. So summarize and prune rather than
     accumulate — rewrite entries tighter instead of appending new prose on
     top of old. Keep the whole wiki within the soft budget of {WIKI_CHAR_BUDGET}
     characters (frontmatter included). The Anomaly log keeps only the
     last {WIKI_ANOMALY_KEEP} resolved/expired entries (the patroller prunes
     older closed ones) — never hoard closed history there.
"""


def classify_empty_result(raw, *, max_tokens: int = REPORT_MAX_TOKENS) -> str:
    """Rule 7c (#63): classify a None/unparsed structured result's failure
    cause from its raw model response. Pure (no I/O, no model calls) and
    duck-typed over the langchain message shape: the finish reason lives in
    response_metadata, the completion-token count (reasoning included) in
    usage_metadata's output_tokens. Returns the reason string for the
    attempt-failure log line — never raises; missing/garbled metadata
    defaults to no-tool-call."""
    metadata = getattr(raw, "response_metadata", None) or {}
    usage = getattr(raw, "usage_metadata", None) or {}
    finish_reason = metadata.get("finish_reason")
    output_tokens = usage.get("output_tokens")
    if finish_reason == "length" or (
            isinstance(output_tokens, int) and output_tokens >= max_tokens):
        return "empty result (truncated at max_tokens)"
    return "empty result (no tool call)"


def _render_observed(items: list) -> str:
    """Rule 7d (#60): render the observed findings/notifications for the
    wiki-repair prompt — a bullet per item, a finding dict as
    '[severity] summary'."""
    lines = []
    for item in items:
        if isinstance(item, dict):
            lines.append(f"- [{item.get('severity', '?')}] {item.get('summary', '')}")
        else:
            lines.append(f"- {item}")
    return "\n".join(lines) if lines else "- (none)"


def compact_messages(messages: list, *, keep_chars: int = SHAPE_TOOL_KEEP) -> list:
    """Rule 7e (#66): pure conversation compaction for every shape-pass
    invocation. Mechanical and lossy in exactly one dimension: every message
    is kept, in order, with its role — but a tool-result body (a langchain
    ToolMessage, or a dict with role == "tool") longer than `keep_chars`
    characters is replaced by its first `keep_chars` characters plus the
    marker line naming both lengths. Non-tool messages — the system prompt,
    the initial user mission, every model turn, and the patrol's plain-text
    final summary included — pass through verbatim, object identity included
    for everything not truncated. Returns a new list; the input (the patrol's
    exploration conversation, kept intact for pod-log visibility) is never
    mutated. Duck-types both langchain message objects and plain dicts."""
    out = []
    for m in messages:
        content = None
        if isinstance(m, ToolMessage):
            content = m.content
        elif isinstance(m, dict) and m.get("role") == "tool":
            content = m.get("content")
        if not (isinstance(content, str) and len(content) > keep_chars):
            out.append(m)  # at-or-under-cap tool bodies and all non-tool
            continue       # messages pass through unchanged
        marker = (f"[truncated by the patroller: first {keep_chars} "
                  f"of {len(content)} characters — full text not shown]")
        truncated = content[:keep_chars] + "\n" + marker
        if isinstance(m, ToolMessage):
            out.append(ToolMessage(content=truncated, tool_call_id=m.tool_call_id,
                                    name=m.name))
        else:
            copy = dict(m)
            copy["content"] = truncated
            out.append(copy)
    return out


def classify_exception(exc: BaseException) -> str:
    """Rule 7f (#66): pure classifier for an exception failure's reason —
    string-based because the SDK's exception types are not importable
    guarantees across langchain-openai versions. An OpenAI-style 5xx status
    (`Error code: 5NN`) or a timeout marker (case-insensitive `timed out` /
    `timeout`) means the model gateway failed the request: the reason becomes
    `gateway failure (<original message>)`. Any other exception (4xx, plain
    errors) keeps its own string. Never raises, no I/O."""
    text = str(exc)
    lowered = text.lower()
    if (re.search(r"Error code: 5\d\d", text)
            or "timed out" in lowered or "timeout" in lowered):
        return f"gateway failure ({text})"
    return text


async def shape_report(model: ChatOpenAI, messages: list, budget_cut: bool,
                       repair: tuple[str, list[str]] | None = None,
                       wiki_repair: tuple[str, list, list] | None = None):
    """Tool-free structured-output pass over the finished patrol conversation.

    Same pattern as the OLS agentic sandbox's "shape pass": the agent explores
    freely (auto tool choice, natural termination); this pass converts the
    conversation — including the patrol's plain-text final summary, if it wrote
    one — into the typed PatrolReport. It runs identically whether the patrol
    completed naturally or was cut off by the step budget, so there is one
    reporting code path and no salvage special-case.

    `repair=(rejected_wiki, errors)` re-invokes this pass as the wiki-validation
    repair pass (wiki-memory.md rule 18): the user message states the previous
    wiki failed skeleton validation and carries the error list and the rejected
    wiki, asking for a corrected PatrolReport. At most one repair pass per
    patrol — the caller owns that bound.

    `wiki_repair=(old_wiki, findings, notifications)` re-invokes this pass as
    the wiki-repair pass (reporting-notifications.md rule 7d, #60): the user
    message states the reporting pass could not emit the wiki and carries the
    old on-disk wiki verbatim plus the observed report's findings and
    notifications, asking for the old wiki rewritten minimally — the findings
    merged in, everything else preserved. At most one wiki-repair invocation
    per patrol — the caller owns that bound; the mode shares everything else
    (the PatrolReport binding, the internal parse/empty retry of rules 7/7b).
    The caller never delivers this call's own findings or notifications (it
    never saw the cluster) — only its wiki is used.

    A parsed result whose `updated_wiki` is empty is a failed attempt too
    (reporting-notifications.md rule 7b, #51) — the patrol cannot distinguish
    an omitted field from a deliberate no-op, so the contract removes the
    no-op meaning entirely. It is retried once with a corrective message that
    names the empty wiki and demands the full content (distinct from the
    generic invalid-report corrective); the repair pass shares the
    classification. Across failure compositions the pass returns the LAST
    result that parsed — a wiki-less report included, so its findings still
    deliver — and None only when no attempt parsed (rule 7's total failure).

    A None/unparsed result's failure reason is classified from the raw model
    response (reporting-notifications.md rule 7c, #63): the structured output
    is invoked with include_raw=True, so an unparsed result is observed
    alongside its raw response — `classify_empty_result` names the cause
    (truncated at max_tokens vs no tool call). Exceptions and structured-
    output parsing errors reported in the envelope (rather than raised) keep
    their own message as the reason. Read-only diagnostics: the retry, the
    corrective message, and the keep-old-wiki outcomes are untouched.
    """
    if repair is None and wiki_repair is None:
        prefix = ("You are the REPORTING PASS for the cluster patrol recorded above. "
                  "The patrol completed and wrote its final summary. "
                  if not budget_cut else
                  "You are the REPORTING PASS for the cluster patrol recorded above. "
                  "The patrol was CUT OFF by its step budget mid-exploration; commands "
                  "marked 'not executed' never ran. Report only on what was actually "
                  "observed. ")
    elif wiki_repair is not None:
        old_wiki, obs_findings, obs_notifications = wiki_repair
        prefix = ("You are the REPORTING PASS for the cluster patrol recorded above. "
                  "The reporting pass could not emit the updated_wiki — it returned "
                  "an empty one even after its retry. Produce a corrected PatrolReport "
                  "whose updated_wiki is the old wiki below rewritten MINIMALLY: fold "
                  "the observed findings in, preserve everything else verbatim.\n\n"
                  "The old on-disk wiki is:\n\n" + old_wiki + "\n\n"
                  "The patrol's observed findings were:\n"
                  + _render_observed(obs_findings)
                  + "\n\nThe patrol's notifications were:\n"
                  + _render_observed(obs_notifications)
                  + "\n\nDo not invent new findings — merge the observed ones above "
                    "into the old wiki.")
    else:
        rejected_wiki, wiki_errors = repair
        prefix = ("You are the REPORTING PASS for the cluster patrol recorded above. "
                  "Your previous updated_wiki FAILED the wiki skeleton validation and "
                  "was REJECTED — it was not written. Produce a corrected PatrolReport "
                  "whose updated_wiki fixes EVERY validation error below:\n"
                  + "\n".join(f"- {e}" for e in wiki_errors)
                  + "\n\nThe rejected wiki was:\n\n" + rejected_wiki + "\n")
    # Rule 7e (#66): the pass carries the COMPACTED conversation, never the
    # raw one — the compaction is uniform across every mode (report, repair,
    # wiki-repair) and input-side only. The corrective retry messages this
    # pass appends below ride after the compacted base, untruncated; the
    # caller's messages list is never mutated (the exploration conversation
    # stays intact for pod-log visibility).
    base = compact_messages(messages)
    msgs: list = [*base, {"role": "user", "content": prefix + REPORT_CONTRACT}]
    # Rule 7b (#51): the empty-wiki classification keys on the wiki field alone
    # (zero findings is legal and never triggers it). A wiki-less parse is a
    # failed attempt — retried with its own named corrective — and the last
    # result that parsed is what the patrol keeps, so a wiki omission never
    # costs the patrol its observations.
    last_parsed = None
    for attempt in (1, 2):
        reason = None
        corrective = "That was not a valid PatrolReport. Produce it now."
        structured = model.with_structured_output(
            PatrolReport, method="function_calling", include_raw=True)
        started = time.monotonic()  # rule 7f (#66): the ceiling is measurable
        try:
            result = await structured.ainvoke(msgs)
            # include_raw envelope (rule 7c, #63): a dict carrying a 'parsed'
            # key is the raw envelope — its parsed value is the report, its
            # raw response feeds the classifier, its parsing_error (if any) is
            # treated as the exception it replaces. Anything else is the
            # directly-parsed value (older stubs, direct pydantic results).
            if isinstance(result, dict) and "parsed" in result:
                parsing_error = result.get("parsing_error")
                raw = result.get("raw")
                result = result.get("parsed")
                if parsing_error is not None:
                    reason = str(parsing_error)
                elif result is None:
                    reason = classify_empty_result(raw)
            if result is not None:
                if report_fields(result)[2]:
                    return result
                last_parsed = result
                reason = "empty wiki"
                corrective = ("Your report parsed, but its updated_wiki was empty. "
                              "updated_wiki is required on every report: return the "
                              "FULL wiki content even when nothing needs changing — "
                              "copy unchanged sections verbatim. Produce the "
                              "complete updated_wiki now.")
            else:
                if reason is None:
                    reason = "empty result"
        except Exception as e:  # noqa: BLE001
            reason = classify_exception(e)  # rule 7f (#66)
        elapsed = int(time.monotonic() - started)
        print(f"[patrol] reporting pass attempt {attempt} failed ({reason}, "
              f"took {elapsed} s)", file=sys.stderr)
        msgs = [*msgs, {"role": "user", "content": corrective}]
    return last_parsed


# --------------------------------------------------------------------------- wiki schema
# Skeleton validation-on-write (wiki-memory.md rules 10-19, #5; ADR 0011). The
# wiki stays a document, not a database: the skeleton (frontmatter, the five
# canonical sections, entry metadata lines in Watch-list and Anomaly log) frames
# free prose, it never replaces it — prose, section order, heading levels other
# than 2, prose-section list items, and the values of the process-stamped fields
# are never judged. Validation is pure (no I/O, no model calls) and gates the
# write path: a rejected wiki gets one model-facing repair pass (rule 18), then
# the old wiki wins (rule 19) — persistence problems degrade, never block.

_ENTRY_PAIR_RE = re.compile(r"^([^\s:]+): (.+)$")  # one 'key: value' metadata pair
_ENTRY_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")  # YYYY-MM-DD, zero-padded


def _is_calendar_date(value: object) -> bool:
    """A valid zero-padded YYYY-MM-DD calendar date (rule 12)."""
    if not isinstance(value, str) or not _ENTRY_DATE_RE.match(value):
        return False
    try:
        datetime.strptime(value, "%Y-%m-%d")
        return True
    except ValueError:
        return False


def _split_frontmatter(content: str) -> tuple[list[str], list[str]] | None:
    """(frontmatter-lines, body-lines), or None when no well-formed block.

    A well-formed block: the file opens with a '---' fence line and closes it
    with another (rule 10). Anything else — including no fences at all — is not
    a frontmatter document.
    """
    lines = content.split("\n")
    if not lines or lines[0].strip() != "---":
        return None
    try:
        end = next(i for i in range(1, len(lines)) if lines[i].strip() == "---")
    except StopIteration:
        return None
    return lines[1:end], lines[end + 1:]


def _frontmatter_errors(fm_text: str) -> tuple[list[str], dict | None]:
    """Rule 10 checks on one frontmatter block: (errors, parsed-mapping-or-None)."""
    try:
        fm = yaml.safe_load(fm_text)
    except yaml.YAMLError as e:
        return [f"frontmatter: not parseable YAML ({e})"], None
    if not isinstance(fm, dict):
        return [f"frontmatter: not a YAML mapping (got {type(fm).__name__})"], None
    errors: list[str] = []
    for key in sorted(set(fm) - set(WIKI_FRONTMATTER_KEYS)):
        errors.append(f"frontmatter: unknown key '{key}' "
                      f"(closed set: {', '.join(WIKI_FRONTMATTER_KEYS)})")
    for key in WIKI_FRONTMATTER_KEYS:
        if key not in fm:
            errors.append(f"frontmatter: missing key '{key}'")
    if "wiki_version" in fm:
        v = fm["wiki_version"]
        if isinstance(v, bool) or not isinstance(v, int):
            errors.append(f"frontmatter: wiki_version must be an integer (got {v!r})")
        elif v != WIKI_VERSION:
            errors.append(f"frontmatter: wiki_version must be {WIKI_VERSION} (got {v!r})")
    # Rule 10b (#62): the gate does not judge the process-owned values —
    # cluster_id, patrol_count, and last_patrol pass with any YAML value.
    # The stamp (rule 17) discards those values before writing, and the
    # model is told the counters are process-owned (the seed says so), so a
    # model faithfully echoing the seed's placeholders must not be rejected.
    # Presence stays checked (a missing key is a malformed skeleton), the
    # closed key set stays, and wiki_version keeps its value check.
    return errors, fm


def _entry_errors(section: str, line: str, seen_ids: set[str]) -> list[str]:
    """Rule 12 checks on one metadata-section entry line."""
    marker = "- " if line.startswith("- ") else "* "
    parts = line[len(marker):].split(" · ")
    pairs: list[tuple[str, str]] = []
    structural: list[str] = []
    for i, part in enumerate(parts):
        m = _ENTRY_PAIR_RE.match(part)
        if m:
            pairs.append((m.group(1), m.group(2)))
        else:
            structural.append(f"part {i + 1} ({part!r}) is not a 'key: value' pair "
                              f"separated by ' · '")
    keys = [k for k, _ in pairs]
    if not structural and keys[:len(WIKI_ENTRY_KEYS)] != list(WIKI_ENTRY_KEYS):
        structural.append("metadata keys must be " + " · ".join(f"{k}:" for k in WIKI_ENTRY_KEYS)
                          + " in that fixed order "
                          + f"(got {' · '.join(f'{k}:' for k in keys) or 'none'})")
    if structural:
        return [f"{section}: entry metadata line not well-formed — {p}" for p in structural]
    values = dict(pairs)
    errors: list[str] = []
    entry_id = values.get("id", "")
    if entry_id in seen_ids:
        errors.append(f"{section}: duplicate entry id '{entry_id}' "
                      "(ids are unique across the whole wiki)")
    else:
        seen_ids.add(entry_id)
    if values.get("status") not in WIKI_ENTRY_STATUSES:
        errors.append(f"{section}: entry '{entry_id}' status must be one of "
                      f"{', '.join(WIKI_ENTRY_STATUSES)} (got {values.get('status')!r})")
    if values.get("confidence") not in WIKI_ENTRY_CONFIDENCES:
        errors.append(f"{section}: entry '{entry_id}' confidence must be one of "
                      f"{', '.join(WIKI_ENTRY_CONFIDENCES)} "
                      f"(got {values.get('confidence')!r})")
    for date_key in ("first_seen", "last_verified"):
        if not _is_calendar_date(values.get(date_key)):
            errors.append(f"{section}: entry '{entry_id}' {date_key} must be a valid "
                          f"YYYY-MM-DD date (got {values.get(date_key)!r})")
    # Rule 12a (#6): the recheck pair. Required (exactly one) on every
    # Watch-list entry, at most one on an Anomaly-log entry. The prefix check
    # (tool-argument form) is its own check and runs before the guard-grammar
    # check so its error names the fix; the grammar itself is the guard's
    # refusal grammar (guard_refusal, ADR 0013) — judged, never executed.
    rechecks = [v for k, v in pairs if k == WIKI_ENTRY_RECHECK]
    if len(rechecks) > 1:
        errors.append(f"{section}: entry '{entry_id}' carries {len(rechecks)} "
                      f"{WIKI_ENTRY_RECHECK}: pairs (exactly one allowed)")
    elif section == "Watch-list" and not rechecks:
        errors.append(f"{section}: entry '{entry_id}' is missing its "
                      f"{WIKI_ENTRY_RECHECK}: <command> pair — every Watch-list "
                      "entry carries the kubectl_readonly argument string that "
                      "re-verifies it")
    elif len(rechecks) == 1:
        value = rechecks[0]
        first = value.split()
        if first and first[0] in ("kubectl", "oc"):
            errors.append(f"{section}: entry '{entry_id}' recheck must be in "
                          f"tool-argument form — drop the leading '{first[0]}' "
                          "(the value is everything after the kubectl/oc CLI name)")
        else:
            refusal = guard_refusal(value)
            if refusal:
                errors.append(f"{section}: entry '{entry_id}' recheck was refused "
                              f"by the guard: {refusal}")
    return errors


def validate_wiki(content: str) -> list[str]:
    """Rule 15: validate a wiki document against the skeleton (rules 10-12
    plus the recheck pair of rule 12a, #6).

    Returns human-readable validation errors; an empty list means valid. Pure —
    no I/O, no model calls. Checks exactly the skeleton: frontmatter block,
    closed key set, types, wiki_version; the five canonical sections exactly
    once each and no other level-2 headings; every top-level list item in
    Watch-list and Anomaly log a well-formed metadata line with valid
    vocabularies, date formats, and unique ids — plus exactly one
    guard-acceptable recheck pair on every Watch-list entry and at most one on
    an Anomaly-log entry, judged with the guard's refusal grammar
    (guard_refusal — shared code, never an execution). Prose, section order,
    other heading levels, prose-section list items, fenced code blocks, and the
    values of the process-stamped fields are not judged.
    """
    split = _split_frontmatter(content)
    if split is None:
        lines = content.split("\n")
        if lines and lines[0].strip() == "---":
            return ["frontmatter: unterminated fence (no closing '---' line)"]
        return ["frontmatter: missing YAML frontmatter block "
                "(the file must open with a '---' fence line)"]
    fm_lines, body_lines = split
    errors, _ = _frontmatter_errors("\n".join(fm_lines))

    # Rule 11: scan the body for level-2 headings and (rule 12) metadata-section
    # entries, skipping fenced code blocks — fenced lines are prose.
    headings: list[str] = []
    entries: list[tuple[str, str]] = []
    in_fence = False
    section = None
    for line in body_lines:
        if line.strip().startswith("```"):
            in_fence = not in_fence
            continue
        if in_fence:
            continue
        if line.startswith("## "):  # level-2 only: '###' does not start with '## '
            section = line[3:].rstrip()
            headings.append(section)
            continue
        if (section in WIKI_METADATA_SECTIONS
                and (line.startswith("- ") or line.startswith("* "))):
            entries.append((section, line))

    counts: dict[str, int] = {}
    for name in headings:
        counts[name] = counts.get(name, 0) + 1
    for name in WIKI_SECTIONS:
        if counts.get(name, 0) == 0:
            errors.append(f"body: missing section '## {name}'")
        elif counts[name] > 1:
            errors.append(f"body: section '## {name}' appears {counts[name]} times "
                          "(exactly once required)")
    for name in sorted(counts):
        if name not in WIKI_SECTIONS:
            errors.append(f"body: unknown level-2 section '## {name}' "
                          f"(canonical set: {', '.join(WIKI_SECTIONS)})")

    seen_ids: set[str] = set()
    for section_name, line in entries:
        errors.extend(_entry_errors(section_name, line, seen_ids))
    return errors


def stamp_wiki(content: str, patrol_count: int, last_patrol: str, cluster_id: str) -> str:
    """Rule 17: rewrite the process-owned frontmatter fields after validation.

    `patrol_count`, `last_patrol`, and `cluster_id` are process-owned — models
    are unreliable counters — so the patroller stamps them authoritatively
    before the atomic write. `wiki_version` and the body are preserved
    byte-for-byte. Called only on skeleton-valid content (the write-path gate);
    if the frontmatter block is somehow absent, the content is returned
    unchanged rather than mangled.
    """
    split = _split_frontmatter(content)
    if split is None:
        return content
    fm_lines, body_lines = split
    owned = {
        "patrol_count": f"patrol_count: {patrol_count}",
        "last_patrol": f'last_patrol: "{last_patrol}"',  # quoted: stays a string
        "cluster_id": f"cluster_id: {cluster_id}",
    }
    out: list[str] = []
    stamped: set[str] = set()
    for line in fm_lines:
        key = line.split(":", 1)[0].strip() if ":" in line else ""
        if key in owned:
            if key not in stamped:  # first occurrence wins; duplicates drop
                out.append(owned[key])
                stamped.add(key)
        else:
            out.append(line)
    for key, line in owned.items():  # defensive: a validated wiki always has them
        if key not in stamped:
            out.append(line)
    return "\n".join(["---", *out, "---", *body_lines])


def frontmatter_patrol_count(content: str) -> int:
    """Rule 17: the on-disk wiki's patrol_count if a positive integer, else 0.

    An absent file, a pre-#5 legacy wiki, or unparseable frontmatter all count
    as 0 — the first skeleton-compliant write stamps 1.
    """
    split = _split_frontmatter(content)
    if split is None:
        return 0
    try:
        fm = yaml.safe_load("\n".join(split[0]))
    except yaml.YAMLError:
        return 0
    if not isinstance(fm, dict):
        return 0
    v = fm.get("patrol_count")
    if isinstance(v, bool) or not isinstance(v, int) or v < 1:
        return 0
    return v


# ---- stale-entry expiry (wiki-memory.md rules 20-22, #7; ADR 0014) ----
# The refinement loop's expire leg, process-enforced: a watch-list entry the
# model stops re-verifying is retired by the patroller, deterministically, at
# write time. The patroller owns the staleness clock the way it owns
# patrol_count (models are unreliable counters), so the flip is never the
# model's job to remember and never a validation concern — verified_patrol is
# not judged (rule 15). Everything here is pure: no I/O, no model calls, the
# same fence-aware section/entry scanning as the validator.


def _scan_sections(lines: list[str]) -> list[tuple[str, int, int]]:
    """[(name, body_start, body_end)] for each level-2 section, fence-aware —
    the same scanning discipline as validate_wiki (rule 11: a '## ' line
    inside a fenced code block is prose). A leading frontmatter block (rule
    10) is skipped so its YAML can never read as headings or entries;
    body_end is the next section heading's index or len(lines)."""
    start = 0
    if lines and lines[0].strip() == "---":
        try:
            start = next(i for i in range(1, len(lines))
                         if lines[i].strip() == "---") + 1
        except StopIteration:
            start = 0  # unterminated fence: treat the whole file as body
    sections: list[tuple[str, int, int]] = []
    in_fence = False
    current: tuple[str, int] | None = None
    for i in range(start, len(lines)):
        line = lines[i]
        if line.strip().startswith("```"):
            in_fence = not in_fence
            continue
        if in_fence:
            continue
        if line.startswith("## "):  # level-2 only, like the validator
            if current is not None:
                sections.append((current[0], current[1], i))
            current = (line[3:].rstrip(), i + 1)
    if current is not None:
        sections.append((current[0], current[1], len(lines)))
    return sections


def _scan_section_entries(lines: list[str], start: int, end: int) -> list[int]:
    """Indices of a section's top-level list items (rule 12: '- '/'* ' at
    column 0), fence-aware — fenced list-looking lines are prose, never
    entries."""
    entries: list[int] = []
    in_fence = False
    for i in range(start, end):
        line = lines[i]
        if line.strip().startswith("```"):
            in_fence = not in_fence
            continue
        if in_fence:
            continue
        if line.startswith("- ") or line.startswith("* "):
            entries.append(i)
    return entries


def _item_extent(lines: list[str], idx: int) -> int:
    """The end (exclusive) of the list item starting at lines[idx]: the
    metadata line plus its indented continuation lines (rule 21: the whole
    item moves verbatim). A blank line does not end the item, but trailing
    blanks are left in the document — only indented content belongs to the
    item. A column-0 non-blank line (another item, a heading, a fence, prose)
    ends it."""
    end = idx + 1
    for i in range(idx + 1, len(lines)):
        line = lines[i]
        if line.startswith((" ", "\t")):
            end = i + 1
        elif not line.strip():
            continue
        else:
            break
    return end


def _metadata_pairs(line: str) -> dict[str, str]:
    """The ' · '-separated key: value pairs of a metadata line, first
    occurrence per key (defensive: the old on-disk wiki may predate
    validation, so parts may be missing or garbled)."""
    if not (line.startswith("- ") or line.startswith("* ")):
        return {}
    pairs: dict[str, str] = {}
    for part in line[2:].split(" · "):
        m = _ENTRY_PAIR_RE.match(part)
        if m and m.group(1) not in pairs:
            pairs[m.group(1)] = m.group(2)
    return pairs


def _old_entry_clocks(old_wiki: str) -> dict[str, tuple[str | None, int | None]]:
    """Rule 20's carry source: id -> (last_verified, verified_patrol) parsed
    from the old on-disk wiki's metadata sections, keyed by id across BOTH
    sections (a section move or status flip by the model does not reset the
    clock — only verification does). Defensive by design: a legacy,
    hand-edited, or pre-#7 wiki yields whatever parses; a missing id skips
    the entry, a missing or garbled value yields None (the grace cases)."""
    clocks: dict[str, tuple[str | None, int | None]] = {}
    lines = old_wiki.split("\n")
    for name, start, end in _scan_sections(lines):
        if name not in WIKI_METADATA_SECTIONS:
            continue
        for idx in _scan_section_entries(lines, start, end):
            pairs = _metadata_pairs(lines[idx])
            entry_id = pairs.get("id")
            if not entry_id:
                continue
            try:
                verified = int(pairs.get(WIKI_ENTRY_VERIFIED_PATROL))  # type: ignore[arg-type]
            except (TypeError, ValueError):
                verified = None
            clocks[entry_id] = (pairs.get("last_verified"), verified)
    return clocks


def _entry_clock(entry_id: str, last_verified: str | None,
                 old_clocks: dict[str, tuple[str | None, int | None]],
                 patrol_count: int) -> int:
    """Rule 20: the verified_patrol value this accepted write stamps on one
    entry. New id or advanced last_verified -> the current write's count
    (advancing last_verified is the one verification signal); otherwise the
    old wiki's clock carries; a missing or garbled old clock -> the current
    count (migration grace: an upgrade never mass-expires)."""
    old = old_clocks.get(entry_id)
    if old is None:
        return patrol_count
    old_last_verified, old_clock = old
    if (_is_calendar_date(old_last_verified) and _is_calendar_date(last_verified)
            and last_verified > old_last_verified):
        return patrol_count
    if old_clock is not None:
        return old_clock
    return patrol_count


def _stamp_verified_patrol(line: str, value: int) -> str:
    """Rule 20: rewrite the verified_patrol pair of one metadata line. An
    existing pair is overwritten in place (the model's value is never
    trusted); an absent one is appended as the line's final pair. Nothing
    else on the line is touched."""
    pair = f"{WIKI_ENTRY_VERIFIED_PATROL}: {value}"
    parts = line[2:].split(" · ")  # callers pass list-item lines: skip the marker
    out: list[str] = []
    stamped = False
    for part in parts:
        if part.startswith(f"{WIKI_ENTRY_VERIFIED_PATROL}: "):
            if not stamped:
                out.append(pair)
                stamped = True
            # a duplicate pair (never valid, never judged) drops: exactly one
            # survives the stamp
        else:
            out.append(part)
    if not stamped:
        out.append(pair)
    return line[:2] + " · ".join(out)


def _flip_status_expired(line: str) -> str:
    """Rule 21: status flips to expired on the metadata line — the only
    metadata value the process rewrites besides verified_patrol. The first
    'status: ' pair is the canonical one (the six required keys lead a
    validated line)."""
    parts = line[2:].split(" · ")
    for i, part in enumerate(parts):
        if part.startswith("status: "):
            parts[i] = "status: expired"
            break
    return line[:2] + " · ".join(parts)


def expire_stale_entries(content: str, old_wiki: str, patrol_count: int,
                         stale_after: int) -> tuple[str, list[tuple[str, int]]]:
    """Rules 20-22 (#7): the stale-entry expiry pass — pure and deterministic.

    Maintains the per-entry verified_patrol clock (new id or advanced
    last_verified -> the current write's count, else carried, missing/garbled
    -> grace) on every entry of both metadata sections, and flips watching
    Watch-list entries whose age `patrol_count - verified_patrol` has reached
    `stale_after` (`0` disables the flip; the clock is still stamped): status
    -> expired, the whole item moved verbatim to the end of the Anomaly log
    with one `auto-expired` annotation line appended as its last continuation
    line. Returns (rewritten content, [(entry id, age)] flipped, in Watch-list
    item order). Aside from the verified_patrol pairs, the flipped entries'
    status values and location, and the annotation lines, the body is
    preserved byte-for-byte — the frontmatter included (the stamp owns it,
    rule 17). Inputs: the VALIDATED wiki about to be written and the old
    on-disk wiki (the clock's carry source); runs only on accepted writes.
    """
    lines = content.split("\n")
    ranges: dict[str, tuple[int, int]] = {}
    for name, start, end in _scan_sections(lines):
        ranges.setdefault(name, (start, end))
    old_clocks = _old_entry_clocks(old_wiki)

    replacements: dict[int, str] = {}   # metadata-line index -> stamped line
    moves: list[tuple[int, int, list[str]]] = []  # (start, end, moved block)
    flipped: list[tuple[str, int]] = []

    watch = ranges.get("Watch-list")
    anomaly = ranges.get("Anomaly log")
    if watch:
        for idx in _scan_section_entries(lines, *watch):
            pairs = _metadata_pairs(lines[idx])
            entry_id = pairs.get("id", "")
            clock = _entry_clock(entry_id, pairs.get("last_verified"),
                                 old_clocks, patrol_count)
            age = patrol_count - clock
            if (stale_after > 0 and anomaly is not None
                    and pairs.get("status") == "watching" and age >= stale_after):
                end = _item_extent(lines, idx)
                annotation = (f"  _auto-expired at patrol {patrol_count}: "
                              f"unverified for {age} patrols "
                              f"(stale_after {stale_after})_")
                block = ([_flip_status_expired(_stamp_verified_patrol(lines[idx], clock))]
                         + lines[idx + 1:end] + [annotation])
                moves.append((idx, end, block))
                flipped.append((entry_id, age))
            else:
                replacements[idx] = _stamp_verified_patrol(lines[idx], clock)
    if anomaly:
        for idx in _scan_section_entries(lines, *anomaly):
            pairs = _metadata_pairs(lines[idx])
            clock = _entry_clock(pairs.get("id", ""), pairs.get("last_verified"),
                                 old_clocks, patrol_count)
            replacements[idx] = _stamp_verified_patrol(lines[idx], clock)

    if not moves:
        if not replacements:
            return content, []
        out = [replacements.get(i, line) for i, line in enumerate(lines)]
        return "\n".join(out), []

    # The moved items land at the END of the Anomaly log: after its last
    # content line (existing entries, a placeholder, or the inside of an
    # unclosed fence — rule 21's pathological-but-valid shape), never before
    # it; a section with no content receives them right after its heading.
    a_start, a_end = anomaly or (0, 0)
    insert_at = a_start
    for i in range(a_end - 1, a_start - 1, -1):
        if lines[i].strip():
            insert_at = i + 1
            break
    cut: set[int] = set()
    for start, end, _block in moves:
        cut.update(range(start, end))
    out: list[str] = []
    inserted = False
    for i, line in enumerate(lines):
        if i in cut:
            continue
        out.append(replacements.get(i, line))
        if not inserted and i + 1 == insert_at:
            for _start, _end, block in moves:
                out.extend(block)
            inserted = True
    if not inserted:  # empty Anomaly log: the heading is the last content line
        for _start, _end, block in moves:
            out.extend(block)
    return "\n".join(out), flipped


# ---- wiki health stats (wiki-memory.md rules 25-26, #8; ADR 0015) ----
# The three document stats plus the churn parser: pure functions over wiki
# text — no I/O, no model calls — measuring the discipline the wiki imposes
# (verify what you watch, resolve or expire deliberately, grade confidence
# honestly). Defensive by design (rule 25): a top-level list item of a
# metadata section is an entry iff its line parses into ' · '-separated
# 'key: value' pairs carrying an id — anything else is invisible to every
# stat — and each stat uses only the field it needs (a garbage or missing
# value skips that stat alone).


def _stats_entry_pairs(line: str) -> dict[str, str] | None:
    """Rule 25's defensive entry test: the list item's metadata pairs, or
    None when the line is not an entry (some part fails to parse as a
    'key: value' pair, or no id is carried). First occurrence wins per key."""
    if not (line.startswith("- ") or line.startswith("* ")):
        return None
    pairs: dict[str, str] = {}
    for part in line[2:].split(" · "):
        m = _ENTRY_PAIR_RE.match(part)
        if not m:
            return None
        pairs.setdefault(m.group(1), m.group(2))
    if not pairs.get("id"):
        return None
    return pairs


def wiki_stats(content: str) -> dict:
    """Rule 25 (#8, #40 amendment): the document health numbers — pure and
    defensive.

    Reads skeleton-valid wikis exactly and hand-edited ones as far as they
    parse (the `_stats_entry_pairs` entry test). Returns a dict with exactly
    ten keys: `patrol_count` (the frontmatter clock under
    frontmatter_patrol_count semantics: unparseable or absent counts 0),
    `cluster_id` (the frontmatter string, "" when absent), `watch_total` /
    `watch_verified` / `verification_rate` (over Watch-list entries only:
    verified means the verified_patrol pair parses as an integer equal to
    patrol_count — Anomaly-log entries are never in the denominator; the
    rate is None iff watch_total == 0), `status_counts` and
    `confidence_counts` (over BOTH metadata sections, bucketed by value,
    keys always present), `lopsided` (the dominant confidence level when
    the warning fires — at least WIKI_STATS_LOPSIDED_MIN graded entries and
    one level at WIKI_STATS_LOPSIDED_SHARE of the graded total, for ANY
    level — else None), and #40's size pair: `char_count` (the whole
    document's character count, frontmatter included — always computable,
    whatever the document's validity) and `over_budget` (True iff
    char_count exceeds WIKI_CHAR_BUDGET, rule 29 — the working-set drift
    measure behind rule 24's size line).
    """
    patrol_count = frontmatter_patrol_count(content)
    cluster_id = ""
    split = _split_frontmatter(content)
    if split is not None:
        try:
            fm = yaml.safe_load("\n".join(split[0]))
        except yaml.YAMLError:
            fm = None
        if isinstance(fm, dict) and isinstance(fm.get("cluster_id"), str):
            cluster_id = fm["cluster_id"]

    watch_total = 0
    watch_verified = 0
    status_counts = {status: 0 for status in WIKI_ENTRY_STATUSES}
    confidence_counts = {level: 0 for level in WIKI_ENTRY_CONFIDENCES}
    lines = content.split("\n")
    for name, start, end in _scan_sections(lines):
        if name not in WIKI_METADATA_SECTIONS:
            continue
        for idx in _scan_section_entries(lines, start, end):
            pairs = _stats_entry_pairs(lines[idx])
            if pairs is None:
                continue  # not an entry: invisible to every stat
            if name == "Watch-list":
                watch_total += 1
                try:
                    verified = int(pairs[WIKI_ENTRY_VERIFIED_PATROL])
                except (KeyError, TypeError, ValueError):
                    verified = None  # missing or garbled: not verified
                if verified == patrol_count:
                    watch_verified += 1
            status = pairs.get("status")
            if status in status_counts:
                status_counts[status] += 1
            confidence = pairs.get("confidence")
            if confidence in confidence_counts:
                confidence_counts[confidence] += 1

    graded = sum(confidence_counts.values())
    lopsided = None
    if graded >= WIKI_STATS_LOPSIDED_MIN:
        for level in WIKI_ENTRY_CONFIDENCES:
            if confidence_counts[level] >= WIKI_STATS_LOPSIDED_SHARE * graded:
                lopsided = level  # at >= 2/3 the dominant level is unique
                break
    return {
        "patrol_count": patrol_count,
        "cluster_id": cluster_id,
        "watch_total": watch_total,
        "watch_verified": watch_verified,
        "verification_rate": (watch_verified / watch_total) if watch_total else None,
        "status_counts": status_counts,
        "confidence_counts": confidence_counts,
        "lopsided": lopsided,
        "char_count": len(content),
        "over_budget": len(content) > WIKI_CHAR_BUDGET,
    }


def _churn_scan(content: str) -> tuple[dict[str, tuple[str | None, tuple[str, ...]]],
                                        list[str]]:
    """Rule 26: one revision's churn view — ({id: (status, prose)}, ordered
    ids). The status reads as None when absent or garbled (absent on both
    sides of a pair then compares equal); the prose is the entry's indented
    continuation block — the same item extent the expiry pass moves verbatim
    (rule 21) — compared byte-exact. A duplicate id resolves to its first
    occurrence in document order; the id order is Watch-list document order,
    then Anomaly-log document order (the live loop first)."""
    lines = content.split("\n")
    entries: dict[str, tuple[str | None, tuple[str, ...]]] = {}
    watch_ids: list[str] = []
    anomaly_ids: list[str] = []
    for name, start, end in _scan_sections(lines):
        if name not in WIKI_METADATA_SECTIONS:
            continue
        bucket = watch_ids if name == "Watch-list" else anomaly_ids
        for idx in _scan_section_entries(lines, start, end):
            pairs = _stats_entry_pairs(lines[idx])
            if pairs is None:
                continue
            entry_id = pairs["id"]
            if entry_id in entries:
                continue  # duplicate id: first occurrence in document order
            status = pairs.get("status")
            if status not in WIKI_ENTRY_STATUSES:
                status = None
            item_end = _item_extent(lines, idx)
            entries[entry_id] = (status, tuple(lines[idx + 1:item_end]))
            bucket.append(entry_id)
    return entries, watch_ids + anomaly_ids


def wiki_churn(revisions: list[str]) -> list[str] | None:
    """Rule 26 (#8): the photocopy-of-a-photocopy detector — pure.

    Over a series of wiki contents oldest→newest (rule 27's revision series):
    an id is flagged iff it is present in EVERY revision and every adjacent
    revision-pair is a churn pair for it — the prose differs AND the parsed
    status values are equal. The comparison is prose-only by design:
    process stamps (verified_patrol, frontmatter) and meaningful metadata
    advances (last_verified, recheck rewrites, section moves) never count as
    rewording; a status change in any pair breaks the flag (resolution and
    expiry are learning events, never churn). Returns the flagged ids in the
    newest revision's order, or None when the series has fewer than two
    revisions (nothing measurable). Entries born or vanished mid-window are
    never flagged: drift detection lags by design.
    """
    if len(revisions) < 2:
        return None
    scanned = [_churn_scan(revision) for revision in revisions]
    common = set(scanned[0][0])
    for entries, _order in scanned[1:]:
        common &= set(entries)
    flagged: list[str] = []
    for entry_id in scanned[-1][1]:  # the newest revision's order
        if entry_id not in common:
            continue
        if all(scanned[i][0][entry_id][1] != scanned[i + 1][0][entry_id][1]  # prose
               and scanned[i][0][entry_id][0] == scanned[i + 1][0][entry_id][0]
               for i in range(len(scanned) - 1)):                            # status
            flagged.append(entry_id)
    return flagged


# ---- working-set budget (wiki-memory.md rules 28-29, #40; ADR 0017) ----
# The wiki is a working set, not a log: growth taxes every patrol (the wiki
# is injected whole into the prompt and re-emitted whole by the shape pass
# under its completion-token bound, REPORT_MAX_TOKENS), while the wiki repo
# already retains every accepted
# write. So the Anomaly log — append-only by nature — gets the mechanical
# bound, enforced by the process the way expiry is (ADR 0014's rationale:
# models are unreliable at self-limiting bookkeeping), and everything else
# gets a soft budget that warns and teaches but never gates (a hard failure
# would burn repair passes on formatting instead of hypotheses). Everything
# here is pure: no I/O, no model calls, the same fence-aware section/entry
# scanning as the validator.


def compact_anomaly_log(content: str, keep: int) -> tuple[str, list[str]]:
    """Rule 28 (#40): the anomaly-log keep budget — pure and deterministic.

    On every accepted write the Anomaly log is bounded: every watching entry
    stays (live placements, the model's to manage — never counted against the
    budget) plus the LAST `keep` resolved/expired entries in document order
    (the section is append-ordered, so "last" means most recent). Closed
    entries earlier in the section are pruned: the whole list item — metadata
    line plus indented continuation lines, the same item extent the expiry
    pass moves verbatim (rule 21) — is removed from the written wiki.
    Pruning is deletion from the working set, never from history (rule 4d's
    one commit per accepted write retains it). Returns (rewritten content,
    [pruned entry ids] in document order). The Watch-list, the prose
    sections, the frontmatter, and non-item prose are never touched; when
    nothing is pruned the content is returned unchanged. keep = 0 prunes
    every closed entry (aggressive but legal — watching entries still stay).
    """
    lines = content.split("\n")
    anomaly = next(((start, end) for name, start, end in _scan_sections(lines)
                    if name == "Anomaly log"), None)
    if anomaly is None:
        return content, []
    closed: list[tuple[int, str]] = []  # (metadata-line index, entry id)
    for idx in _scan_section_entries(lines, *anomaly):
        pairs = _metadata_pairs(lines[idx])
        if pairs.get("status") in ("resolved", "expired"):
            closed.append((idx, pairs.get("id", "")))
    beyond = len(closed) - keep
    if beyond <= 0:
        return content, []
    cut: set[int] = set()
    for idx, _entry_id in closed[:beyond]:  # whole items, earliest closed first
        cut.update(range(idx, _item_extent(lines, idx)))
    out = [line for i, line in enumerate(lines) if i not in cut]
    return "\n".join(out), [entry_id for _idx, entry_id in closed[:beyond]]


def wiki_budget_warnings(content: str) -> list[str]:
    """Rule 29 (#40): the soft whole-wiki character budget — pure.

    Empty within the budget; otherwise exactly one warning naming the wiki's
    character count, the budget, and the summarize-and-prune instruction.
    The budget is deliberately soft — it never gates validation (rule 15)
    and never blocks a write: the anomaly-log keep budget (rule 28) is the
    mechanical bound, this one the drift alarm. WIKI_CHAR_BUDGET is read at
    call time so tests can bound it.
    """
    count = len(content)
    if count <= WIKI_CHAR_BUDGET:
        return []
    return [f"wiki over length budget: {count} chars vs {WIKI_CHAR_BUDGET} budget "
            "— summarize and prune; git history is the archive"]


# --------------------------------------------------------------------------- wiki git
# Git-backed wiki persistence (wiki-memory.md rules 4a-4e, #3). Git is process-level
# durability, never an agent capability: everything here runs in the patrol process
# and the model's tool surface stays [read_file, kubectl_readonly]. Local-first: the
# local wiki repo is the source of truth; the optional push is replication only.


def _git_err(r) -> str:
    """Human-readable failure reason for a git invocation (None = git unusable)."""
    if r is None:
        return "git unusable (not on PATH, or timed out)"
    return (r.stderr or r.stdout or "unknown git error").strip()


def _run_git(wiki_dir: Path, *args: str, timeout: float | None = None,
             encoding: str | None = None):
    """Run git in wiki_dir with patrol-owned config only (never the user's).

    Every invocation passes `safe.directory` for the wiki directory so git does
    not depend on the invoking user's config or HOME (non-root, random-UID safe,
    rule 4d). Returns None when git itself is unusable — callers degrade to
    file-only mode instead of failing the patrol (rule 4b). `encoding` opts a
    call into deterministic decoding (the stats history reader decodes UTF-8,
    the wiki's own encoding, whatever the ambient locale is).
    """
    cmd = ["git", "-C", str(wiki_dir),
           "-c", f"safe.directory={wiki_dir.resolve()}", *args]
    try:
        return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout,
                              encoding=encoding)
    except (OSError, subprocess.SubprocessError):
        return None


def ensure_wiki_repo(wiki_dir) -> bool:
    """Rule 4a/4b: make the wiki directory a git repo, before the first patrol.

    Creates the directory if absent, inits a repo (default branch `main`) if the
    directory contains none, and adopts any repo already there (the wiki dir
    must be dedicated to the wiki — patrol commits land in whatever repo
    governs it). Returns True when git backing is on. Returns False — with a
    warning, never an exception — when git is missing or init fails: the process
    then runs file-only (no init/commit/push), and the wiki file itself keeps
    working.
    """
    wiki_dir = Path(wiki_dir)
    try:
        wiki_dir.mkdir(parents=True, exist_ok=True)
    except OSError as e:
        print(f"[patrol] WARNING: cannot create wiki directory {wiki_dir} ({e}); "
              "running file-only (no wiki history)", file=sys.stderr)
        return False
    if (wiki_dir / ".git").exists():
        return True  # already a repository of any origin: adopt it
    r = _run_git(wiki_dir, "init", "-b", "main")
    if r is None or r.returncode != 0:
        print(f"[patrol] WARNING: wiki repo init failed ({_git_err(r)}); "
              "running file-only (no wiki history)", file=sys.stderr)
        return False
    return True


def write_wiki_atomic(wiki_path, content: str) -> None:
    """Rule 4c: temp file + rename — a crash mid-write never truncates the wiki.

    Content is written to a temporary file in the wiki directory and renamed
    over the wiki file. If the rename fails, the temporary file is removed, the
    old wiki stands, and the error propagates to the caller. The commit history
    is the second net: a bad rewrite is always revertible.
    """
    wiki_path = Path(wiki_path)
    tmp = wiki_path.parent / f".{wiki_path.name}.tmp"
    try:
        tmp.write_text(content)
        os.replace(tmp, wiki_path)  # atomic on POSIX; overwrites
    except BaseException:
        try:
            tmp.unlink(missing_ok=True)
        except OSError:
            pass
        raise


def commit_wiki(wiki_path, findings, cluster_id: str) -> bool:
    """Rule 4d: one wiki-changing patrol, one commit. Returns False, never raises.

    Stages exactly the wiki file — never anything else in the wiki directory —
    and commits with the patrol identity `ols-patroller <patrol@CLUSTER_ID>`,
    passed per command together with `safe.directory` so no user config or HOME
    is consulted. Message: `patrol N: X findings, Y warn` (N = commits already
    in the repo + 1, X = finding count, Y = warn+crit count). No change on disk
    (empty update, failed report, or byte-identical rewrite) → no commit. No
    repo (file-only mode) → no commit; a repo is never initted here.
    """
    wiki_path = Path(wiki_path).resolve()
    wiki_dir = wiki_path.parent
    if not (wiki_dir / ".git").exists():
        return False  # file-only mode: nothing to commit into, never init one

    def _sev(f) -> str:
        return f.get("severity", "") if isinstance(f, dict) else getattr(f, "severity", "")

    r = _run_git(wiki_dir, "rev-list", "--count", "HEAD")
    prior = int(r.stdout.strip()) if (r is not None and r.returncode == 0
                                      and r.stdout.strip().isdigit()) else 0
    r = _run_git(wiki_dir, "add", "--", str(wiki_path))
    if r is None or r.returncode != 0:
        print(f"[patrol] WARNING: could not stage wiki ({_git_err(r)}); no commit",
              file=sys.stderr)
        return False
    r = _run_git(wiki_dir, "diff", "--cached", "--quiet")
    if r is not None and r.returncode == 0:
        return False  # nothing staged: the rewrite was byte-identical
    warned = sum(1 for f in findings if _sev(f) in ("warn", "crit"))
    message = f"patrol {prior + 1}: {len(findings)} findings, {warned} warn"
    r = _run_git(wiki_dir,
                 "-c", "user.name=ols-patroller", "-c", f"user.email=patrol@{cluster_id}",
                 "commit", "-m", message)
    if r is None or r.returncode != 0:
        print(f"[patrol] WARNING: wiki commit failed ({_git_err(r)}); "
              "the wiki file stands without a history entry", file=sys.stderr)
        return False
    print(f"[patrol] wiki commit: {message}")
    return True


def push_wiki(wiki_dir, remote: str) -> bool:
    """Rule 4e: bounded, best-effort replication of the local wiki repo.

    Pushes the current branch to the same-named branch on `remote`, at most
    PUSH_ATTEMPTS attempts of PUSH_TIMEOUT seconds each with PUSH_BACKOFF
    between them (bounds are read at call time). Never raises and never blocks
    the patrol loop: any failure is logged and the local commit stands — it is
    the source of truth (local-first).
    """
    wiki_dir = Path(wiki_dir)
    r = _run_git(wiki_dir, "symbolic-ref", "--short", "HEAD")
    if r is None or r.returncode != 0:
        print(f"[patrol] WARNING: cannot resolve wiki branch ({_git_err(r)}); "
              "skipping push", file=sys.stderr)
        return False
    branch = r.stdout.strip()
    if not branch:
        return False
    for attempt in range(1, PUSH_ATTEMPTS + 1):
        r = _run_git(wiki_dir, "push", remote, f"{branch}:{branch}", timeout=PUSH_TIMEOUT)
        if r is not None and r.returncode == 0:
            print(f"[patrol] wiki pushed → {remote}")
            return True
        print(f"[patrol] WARNING: wiki push attempt {attempt}/{PUSH_ATTEMPTS} failed "
              f"({_git_err(r)})", file=sys.stderr)
        if attempt < PUSH_ATTEMPTS:
            time.sleep(PUSH_BACKOFF)
    print(f"[patrol] WARNING: wiki push gave up after {PUSH_ATTEMPTS} attempts; "
          "local commit stands (source of truth)", file=sys.stderr)
    return False


def wiki_history(wiki_path, window: int) -> list[str]:
    """Rule 27 (#8): the wiki file's blobs at the last `window` commits that
    changed it, oldest→newest — the churn series.

    Read-only history access under the patrol's own git discipline
    (per-command config, safe.directory, never a remote call; UTF-8 decoding —
    the wiki is a UTF-8 text document whatever the ambient locale). No repo,
    no commits, or any git failure yields an empty list — never a partial
    window and never an error (degradation, rule 4b's philosophy: reading
    stats must not mutate anything and must not fail the run).
    """
    try:
        wiki_path = Path(wiki_path).resolve()
        wiki_dir = wiki_path.parent
        if not (wiki_dir / ".git").exists():
            return []  # file-only mode: no history, never init one
        r = _run_git(wiki_dir, "log", "-n", str(window), "--format=%H", "--",
                     str(wiki_path))
        if r is None or r.returncode != 0:
            return []
        shas = [line.strip() for line in r.stdout.splitlines() if line.strip()]
        revisions: list[str] = []
        for sha in reversed(shas):  # git log is newest-first; the series is oldest-first
            b = _run_git(wiki_dir, "show", f"{sha}:./{wiki_path.name}",
                         encoding="utf-8")
            if b is None or b.returncode != 0:
                return []  # never a partial window
            revisions.append(b.stdout)
        return revisions
    except Exception:  # noqa: BLE001 — any failure degrades to no history
        return []


# --------------------------------------------------------------------------- one patrol
async def run_patrol(*, kubectl: str, wiki_path: Path, dry: bool, verbose: bool,
                     max_steps: int) -> None:
    ts = time.strftime("%Y-%m-%dT%H:%M:%S%z")
    print(f"\n[patrol] === patrol start {ts} (model={MODEL}, dry={dry}, engine=deepagents) ===")

    wiki = wiki_path.read_text() if wiki_path.exists() else WIKI_SEED

    # best-effort dedup context: what's already being handled?
    inflight = kubectl_readonly("get agenticruns -A", kubectl=kubectl, dry=dry, verbose=verbose)

    dry_note = ""
    if dry:
        avail = ", ".join(f"`{k}`" for k, _ in FIXTURES)
        dry_note = (
            "\n=== DRY-CLUSTER MODE ===\n"
            "This is a canned demo cluster, not a real one. ONLY these commands have "
            f"fixtures: {avail}. Any other command returns 'no fixture' — treat that "
            "resource as absent and do NOT probe other resource types. Base your "
            "patrol on the fixtures alone.\n"
        )

    user = (
        f"Current time: {ts}\n{dry_note}\n"
        f"=== IN-FLIGHT AGENTIC RUNS / WORK ALREADY IN PROGRESS ===\n{inflight}\n\n"
        f"=== CLUSTER WIKI (your prior notes — may be stale, verify against reality) ===\n{wiki}\n\n"
        f"Begin your patrol. Explore with kubectl_readonly. You have a hard budget of "
        f"~{max_steps} turns — once you have covered the main areas or hit diminishing "
        f"returns, stop exploring and write your final summary."
    )

    # Two models, two bounds (rules 8a + 8b, #49/#63; ADRs 0018 + 0021): the
    # request timeout and the completion-token bound are both client-level
    # properties in langchain-openai, so the per-call-shape bounds mean two
    # ChatOpenAI instances from the same make_model seam — the agent's at the
    # exploration bounds (hung endpoints fail snappily, runaway turns fail
    # cheaply) and the shape pass's at the reporting bounds (the structured
    # call carries the whole conversation and must emit the entire wiki,
    # reasoning tokens included — legitimately running minutes and ~20k
    # tokens on reasoning-capable models; the repair pass reuses the shape
    # instance).
    model = make_model(timeout=REQUEST_TIMEOUT, max_tokens=REQUEST_MAX_TOKENS)
    shape_model = make_model(timeout=REPORT_TIMEOUT, max_tokens=REPORT_MAX_TOKENS)
    _disable_default_subagent(model)
    # No response_format here: an in-agent ToolStrategy forces tool_choice="any" on
    # every turn, which removes the model's natural stopping signal (GLM under it
    # never self-terminated — patrols always ran to the budget). Instead the agent
    # explores with auto tool choice and ends on its own; the typed PatrolReport
    # comes from the separate tool-free shape_report pass below — the same pattern
    # the OLS agentic sandbox uses.
    agent = create_deep_agent(
        model=model,
        tools=[make_kubectl_tool(kubectl=kubectl, dry=dry, verbose=verbose)],
        system_prompt=SYSTEM_PROMPT,
        # Replace deepagents' default FilesystemMiddleware (which would expose
        # ls/read_file/write_file/edit_file/delete/glob/grep/execute) with the minimum
        # it allows: read_file only, on the default in-memory StateBackend. Combined
        # with no `backend` (nothing touches disk) and no `subagents`, the patrol's
        # only meaningful tool is the guarded kubectl_readonly above — no shell, no
        # filesystem writes, read-only by construction. (deepagents refuses a
        # FilesystemMiddleware without read_file; it backs tool-result offloading.)
        middleware=[FilesystemMiddleware(tools=["read_file"])],
    )

    # Each LLM round costs ~2 graph steps (model node + tools node); the structured
    # output pass adds a few more. max_steps counts LLM turns, like the old loop.
    recursion_limit = max_steps * 2 + 12

    tokens_in = tokens_out = 0
    seen = 0
    messages: list = []
    budget_cut = False
    try:
        async for state in agent.astream(
            {"messages": [{"role": "user", "content": user}]},
            config={"recursion_limit": recursion_limit},
            stream_mode="values",
        ):
            messages = state.get("messages", [])
            for m in messages[seen:]:
                seen += 1
                if isinstance(m, HumanMessage):
                    continue
                if isinstance(m, AIMessage):
                    um = m.usage_metadata or {}
                    tokens_in += um.get("input_tokens") or 0
                    tokens_out += um.get("output_tokens") or 0
                    if verbose:
                        rc = (m.additional_kwargs or {}).get("reasoning_content")
                        if rc:
                            print(f"[patrol] 💭 {preview(rc, 400)}", file=sys.stderr)
                        if m.tool_calls:
                            for tc in m.tool_calls:
                                print(f"[patrol] → {tc.get('name')} "
                                      f"{preview(str(tc.get('args')))}", file=sys.stderr)
                        elif m.content:
                            print(f"[patrol] 💬 {preview(m.content, 600)}", file=sys.stderr)
                elif isinstance(m, ToolMessage):
                    if verbose:
                        print(f"[patrol]   ↳ {preview(m.content, 220)}", file=sys.stderr)
    except GraphRecursionError:
        # Exploration budget exhausted. Not a failure: the reporting pass runs over
        # whatever was observed, exactly as it does for a naturally-completed patrol.
        budget_cut = True
        print(f"[patrol] WARNING: exploration budget ({max_steps} turns) exhausted; "
              "reporting from observations so far.")
        # If the run died between a tool call and its execution, close those calls so
        # the history is valid to feed into the reporting pass.
        last = messages[-1] if messages else None
        if isinstance(last, AIMessage) and last.tool_calls:
            for tc in last.tool_calls:
                messages.append(ToolMessage(
                    content="ERROR: step budget exhausted; command not executed.",
                    tool_call_id=tc["id"], name=tc["name"]))

    if not messages:
        print("[patrol] WARNING: patrol made no observations; skipping wiki write.")
        return

    report = await shape_report(shape_model, messages, budget_cut)
    # Rule 33 (#66, ADR 0024): a total failure (`shape_report` returned None —
    # no attempt parsed, a different failure family from rule 30's
    # still-empty outcome: there is no observed report at all) no longer stops
    # at the warning. Ordering: the rule-7 warning (stdout, its existing
    # channel) first, then one FAILURE notification through the standard
    # notify path — a blind patrol is always wolf-worthy — then the rule-31
    # minimal-write fallback run DIRECTLY (the wiki-repair pass is skipped on
    # this leg: it exists to merge observed findings into the old wiki, and a
    # total failure has none to merge). Everything else is rule 31 verbatim:
    # the old wiki runs through the accepted-write tail, the seed takes that
    # ride on a file-absent fresh deployment, a legacy no-frontmatter wiki
    # writes nothing. Honest staleness, never frozen staleness.
    total_failure = report is None
    minimal_write = False
    if total_failure:
        print("[patrol] WARNING: reporting pass produced no report; skipping wiki write.")
        notify(["patrol produced no report — both reporting-pass attempts "
                "failed (see pod logs)"], notify_type="FAILURE")
        findings, notifications, new_wiki = [], [], ""
        still_empty = False
        if _split_frontmatter(wiki) is not None:
            new_wiki = wiki
            minimal_write = True
    else:
        findings, notifications, new_wiki = report_fields(report)

    # Rule 7b (#51): an empty updated_wiki on arrival is a twice-failed
    # reporting attempt — shape_report already retried it once with the named
    # corrective. The report is not discarded (a wiki omission must never cost
    # the patrol's observations): its findings and notifications deliver below,
    # and this one stderr warning makes the no-op visible and countable, never
    # silent.
    still_empty = not new_wiki and not total_failure
    if still_empty:
        print("[patrol] WARNING: reporting pass returned an empty updated_wiki; "
              "keeping the previous wiki, writing nothing", file=sys.stderr)

    # Wiki gate (#5, rules 15-19): the model's wiki is validated before it may
    # replace memory. Invalid -> exactly one repair pass; a repair whose wiki
    # validates replaces the report wholesale, otherwise the old wiki stands
    # and the FIRST report's findings/notifications still deliver (rule 19: a
    # wiki-format failure never costs the patrol's observations). An empty
    # updated_wiki skips validation and repair entirely (rule 4 unchanged) — by
    # the time it reaches this gate it is a twice-failed reporting attempt,
    # already retried and warned about above (rule 7b, #51): a classified
    # failure writes nothing.
    if new_wiki:
        errors = validate_wiki(new_wiki)
        if errors:
            print(f"[patrol] WARNING: updated wiki failed skeleton validation "
                  f"({len(errors)} error(s)); running one repair pass", file=sys.stderr)
            # Rule 18 (#40): when the rejected wiki is over the soft budget,
            # its budget warning joins the error list carried to the repair
            # pass — the model is asked to shorten while it fixes structure.
            # Acceptance stays structural validity alone (rule 29: length
            # never decides — an over-budget repair still writes, with the
            # write-path warning below).
            repaired = await shape_report(shape_model, messages, budget_cut,
                                          repair=(new_wiki,
                                                  errors + wiki_budget_warnings(new_wiki)))
            final = None
            if repaired is not None:
                r_findings, r_notifications, r_wiki = report_fields(repaired)
                if r_wiki and not validate_wiki(r_wiki):
                    final = (r_findings, r_notifications, r_wiki)
            if final is not None:
                findings, notifications, new_wiki = final
            else:
                print("[patrol] WARNING: wiki update rejected — the repaired wiki "
                      "still fails skeleton validation; keeping the previous wiki, "
                      "writing nothing", file=sys.stderr)
                new_wiki = ""

    # Still-empty safety net (wiki-memory.md rules 30-32, #60; ADR 0022): only
    # the reporting-pass still-empty outcome triggers it — the rule-18 repair
    # pass's own wiki-less leg (still_empty False above) keeps its
    # keep-old-wiki outcome unchanged.
    if still_empty:
        # Rule 30: one wiki-repair pass — the old on-disk wiki (the seed when
        # the file is absent) plus the observed findings/notifications, asking
        # for a minimal merge. Its own findings/notifications are NEVER
        # delivered (it never saw the cluster); only its wiki is used, gated
        # by validate_wiki with no further repair.
        print("[patrol] wiki-repair pass: reporting pass could not emit the wiki; "
              "asking for a minimal merge of the findings into the old wiki",
              file=sys.stderr)
        repaired = await shape_report(shape_model, messages, budget_cut,
                                      wiki_repair=(wiki, findings, notifications))
        if repaired is not None:
            r_wiki = report_fields(repaired)[2]
            if r_wiki and not validate_wiki(r_wiki):
                new_wiki = r_wiki
        if not new_wiki:
            # Rule 31: the minimal-write fallback — the accepted-write tail on
            # the old on-disk wiki verbatim (the seed when the file is absent;
            # rule 14a's deliberate exception). The stamp makes the clock
            # honest; the content stays stale. A legacy wiki with no
            # frontmatter block has no clock to advance: nothing written,
            # nothing committed, the rule-7b warning alone.
            if _split_frontmatter(wiki) is not None:
                new_wiki = wiki
                minimal_write = True

    # Delivery (rule 16): findings and notifications are delivered after the
    # validation/repair decision resolves and before the physical write, so
    # exactly one report — the final one — is delivered. Rule 33 (#66): the
    # total-failure leg delivers nothing here — its one FAILURE notification
    # already fired above, and there is no report to print findings from.
    if not total_failure:
        print(f"\n[patrol] findings ({len(findings)}):")
        for f in findings:
            print(f"  [{f.get('severity', '?'):>4}] {f.get('summary', '')}")
            if verbose and f.get("evidence"):
                print(f"         ↳ {f['evidence']}")

        notify([m for m in notifications if m],
               notify_type=report_notify_type(findings))

    if new_wiki:
        # Rule 22 (#7): the expiry pass — pure and deterministic, after the
        # delivery decision resolves and immediately before the stamp. P is
        # the counter the stamp is about to write (the on-disk wiki's
        # patrol_count + 1, the same basis rule 17 uses); the old on-disk
        # wiki is the clock's carry source. One console line per flip (rule
        # 21): expiry is wiki housekeeping, never a finding or notification.
        patrol_count = frontmatter_patrol_count(wiki) + 1
        new_wiki, flips = expire_stale_entries(new_wiki, wiki,
                                               patrol_count=patrol_count,
                                               stale_after=STALE_AFTER)
        for entry_id, age in flips:
            print(f"[patrol] wiki: entry '{entry_id}' auto-expired "
                  f"(unverified for {age} patrols, stale_after {STALE_AFTER})")
        # Rule 28 (#40): the compaction pass — pure and deterministic, after
        # expiry (entries flipped this patrol land at the section's end and
        # are among the newest — they survive the cut) and before the stamp.
        # Every watching entry plus the last WIKI_ANOMALY_KEEP resolved/expired
        # entries stay; earlier closed entries are pruned from the working
        # set — git history is the archive. One console line per prune:
        # pruning is wiki housekeeping, never a finding or notification.
        new_wiki, pruned = compact_anomaly_log(new_wiki, WIKI_ANOMALY_KEEP)
        for entry_id in pruned:
            print(f"[patrol] wiki: entry '{entry_id}' pruned from the Anomaly log "
                  f"(beyond the keep budget {WIKI_ANOMALY_KEEP}; "
                  "git history retains it)")
        # Rule 17: stamp the process-owned frontmatter fields — patrol_count
        # from the ON-DISK wiki (never the model's guess), last_patrol from this
        # patrol's start, cluster_id from config. Every accepted write advances
        # patrol_count, so every accepted write changes the file and commits.
        stamped = stamp_wiki(new_wiki,
                             patrol_count=patrol_count,
                             last_patrol=ts,
                             cluster_id=CLUSTER_ID)
        # Rule 29 (#40): the soft-budget warning — on the final wiki (post
        # expire → compact → stamp), to stderr (rule 16's channel for wiki
        # problems). The budget never gates: the write, commit, and push
        # proceed regardless.
        for warning in wiki_budget_warnings(stamped):
            print(f"[patrol] WARNING: {warning}", file=sys.stderr)
        write_wiki_atomic(wiki_path, stamped)
        if minimal_write:
            # Rule 32 (#60): one console line naming the fallback outcome —
            # wiki housekeeping, never a finding or a notification.
            print("[patrol] wiki: minimal write — memory content unchanged, "
                  "patrol clock advanced")
        print(f"[patrol] wiki updated → {wiki_path}")
        if commit_wiki(wiki_path, findings, CLUSTER_ID) and WIKI_REMOTE:
            push_wiki(wiki_path.parent, WIKI_REMOTE)
    print(f"[patrol] tokens: in={tokens_in} out={tokens_out}")
    print(f"[patrol] === patrol done ===")


# --------------------------------------------------------------------------- main
def run_wiki_stats(wiki_path: Path) -> None:
    """Rules 23-24 (#8, #40 amendment): the offline stats mode — print the
    wiki's health numbers (the four of #8 plus #40's size line) and exit 0.

    Fully offline: no PATROL_* env, no cluster CLI, no repo initialization —
    reading stats must not mutate anything (the wiki git region is only
    read). A missing wiki file is a config error (stderr names the path,
    exit 1). A wiki failing skeleton validation still gets its stats — the
    parser is defensive — with one stderr warning stating that and the error
    count. Every degradation (no repo, unusable git, too little history)
    prints churn n/a and exits 0; the lopsided warning and churn flags are
    printed hints on the human surface, never gates.
    """
    wiki_path = Path(wiki_path)
    if not wiki_path.exists():
        die(f"wiki file not found: {wiki_path} (--wiki-stats reads the wiki at "
            "--wiki; run a patrol first or point --wiki at an existing wiki)")
    content = wiki_path.read_text()
    errors = validate_wiki(content)
    if errors:
        print(f"[patrol] WARNING: the wiki at {wiki_path} fails skeleton validation "
              f"({len(errors)} error(s)); stats are parsed defensively from it "
              "as-is", file=sys.stderr)
    stats = wiki_stats(content)
    churn = wiki_churn(wiki_history(wiki_path, WIKI_CHURN_WINDOW))

    # Rule 24: the printed block — exact keywords pinned, spacing and
    # decoration free, percentages floored integers.
    p = stats["patrol_count"]
    print(f"=== wiki stats: cluster {stats['cluster_id']} · patrol_count {p} ===")
    if stats["watch_total"]:
        share = int(100 * stats["watch_verified"] / stats["watch_total"])
        print(f"verification: {stats['watch_verified']}/{stats['watch_total']} "
              f"watch-list entries verified by patrol {p} ({share}%)")
    else:
        print("verification: n/a (empty watch-list)")
    status = stats["status_counts"]
    tracked = sum(status.values())
    print(f"resolution: watching {status['watching']} · resolved {status['resolved']}"
          f" · expired {status['expired']} ({tracked} tracked)")
    spread = stats["confidence_counts"]
    print(f"confidence: high {spread['high']} · medium {spread['medium']}"
          f" · low {spread['low']}")
    if churn is None:
        print("churn: n/a (no wiki history)")
    elif churn:
        print(f"churn: {', '.join(churn)}")
    else:
        print("churn: none")
    if stats["lopsided"] is not None:
        level = stats["lopsided"]
        graded = sum(spread.values())
        share = int(100 * spread[level] / graded)
        print(f"WARNING: lopsided confidence: {spread[level]}/{graded} {level} "
              f"({share}%) — a grade everyone gets is not a grade")
    # Rule 24 (#40): the size line — the working-set drift measure (the
    # character count and its floored share of the soft budget) — and, when
    # over budget, the rule-29 warning text prefixed WARNING:.
    size = stats["char_count"]
    share = int(100 * size / WIKI_CHAR_BUDGET)
    print(f"size: {size} chars ({share}% of budget)")
    if stats["over_budget"]:
        for warning in wiki_budget_warnings(content):
            print(f"WARNING: {warning}")


def main() -> None:
    ap = argparse.ArgumentParser(description="Cluster Sentinel hacky PoC (deepagents engine)")
    ap.add_argument("--once", action="store_true", help="run a single patrol (default)")
    ap.add_argument("--loop", action="store_true", help="patrol repeatedly")
    ap.add_argument("--interval", type=int, default=int(os.environ.get("PATROL_INTERVAL", "21600")),
                    help="seconds between patrols in --loop mode (default 21600, six hours)")
    ap.add_argument("--dry-cluster", action="store_true",
                    help="use canned fixtures instead of a real cluster")
    ap.add_argument("--wiki", default=str(Path(__file__).parent / "wiki" / "cluster-wiki.md"),
                    help="path to the wiki memory file (default wiki/cluster-wiki.md; "
                         "its parent directory is the wiki git repo)")
    ap.add_argument("--max-steps", type=int, default=MAX_STEPS,
                    help="max LLM turns per patrol")
    ap.add_argument("-q", "--quiet", action="store_true",
                    help="suppress the per-turn output stream (reasoning previews, tool "
                         "calls, results — on by default; findings and notifications "
                         "always print)")
    ap.add_argument("--wiki-stats", action="store_true",
                    help="print the wiki's health numbers (verification rate, "
                         "resolution ratio, confidence spread, churn, size) and exit — "
                         "offline: no PATROL_* env, no cluster, no repo initialization")
    args = ap.parse_args()

    # Rule 23 (#8): the offline stats mode branches right here — before the env
    # check, kubectl resolution, and repo initialization — and takes precedence
    # over --once (the default mode). Stats print and exit; they cannot schedule.
    if args.wiki_stats and args.loop:
        ap.error("--wiki-stats and --loop cannot be combined: --wiki-stats prints "
                 "the wiki health numbers and exits, it cannot schedule patrols")
    if args.wiki_stats:
        run_wiki_stats(Path(args.wiki))
        return

    if not API_KEY or not BASE_URL or not MODEL:
        die("set PATROL_API_KEY, PATROL_BASE_URL, and PATROL_MODEL in the environment "
            "(any OpenAI-compatible chat-completions endpoint).")

    kubectl = resolve_kubectl()
    wiki_path = Path(args.wiki)
    # Git-backed wiki (#3): ensure the wiki directory is a repo before the first
    # patrol (wiki-memory.md rule 4a). If git is unusable this warns and the
    # process runs file-only (rule 4b) — patrol behavior never depends on git.
    if ensure_wiki_repo(wiki_path.parent):
        print(f"[patrol] wiki repo: {wiki_path.parent}")

    # #27: the per-turn output stream is on by default — verbose is derived
    # from the --quiet opt-out, never a standalone opt-in flag.
    verbose = not args.quiet

    async def patrol_once() -> None:
        await run_patrol(kubectl=kubectl, wiki_path=wiki_path, dry=args.dry_cluster,
                         verbose=verbose, max_steps=args.max_steps)

    def one() -> None:
        asyncio.run(patrol_once())

    if args.loop:
        print(f"[patrol] loop mode: every {args.interval}s (ctrl-c to stop)")

        # #50: every patrol of the process runs on one process-lifetime event
        # loop — a fresh asyncio.run per patrol stranding loop-bound engine
        # state (cached async clients, keep-alive pools) on a closed loop
        # (ADR 0020). The inter-patrol wait is a deliberate blocking
        # time.sleep: the loop has no concurrent work while idle, and a
        # blocking sleep is trivially ctrl-c-interruptible.
        async def loop() -> None:
            while True:
                try:
                    await patrol_once()
                except Exception as e:  # noqa: BLE001
                    print(f"[patrol] patrol failed ({e}); continuing loop",
                          file=sys.stderr)
                time.sleep(args.interval)

        try:
            asyncio.run(loop())
        except KeyboardInterrupt:
            print("\n[patrol] stopped.")
    else:
        one()


if __name__ == "__main__":
    main()
