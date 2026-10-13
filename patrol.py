#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = [
#     "deepagents>=0.7",
#     "langchain-openai>=1.0",
#     "pydantic>=2",
#     "requests>=2.31",
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
    uv run patrol.py --loop --interval 900    # every 15 min

Point it at any OpenAI-compatible chat-completions endpoint via env (see below).
"""
from __future__ import annotations

import argparse
import asyncio
import os
import shlex
import subprocess
import sys
import time
from pathlib import Path
from typing import Literal

import requests
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

# --------------------------------------------------------------------------- config
BASE_URL   = os.environ.get("PATROL_BASE_URL", "")   # e.g. https://api.example.com/v1
MODEL      = os.environ.get("PATROL_MODEL", "")      # model id on that endpoint
API_KEY    = os.environ.get("PATROL_API_KEY", "")
KUBECTL    = os.environ.get("KUBECTL", "")  # resolved later
SLACK_URL  = os.environ.get("SLACK_WEBHOOK_URL", "")
MAX_STEPS  = int(os.environ.get("PATROL_MAX_STEPS", "30"))  # LLM turns per patrol
TOOL_TIMEOUT = int(os.environ.get("PATROL_TOOL_TIMEOUT", "60"))
OUT_CAP    = 15_000  # max chars of tool output fed back to the model

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
   how major pieces connect. Prefer a broad sweep first, then drill into anything suspicious.
2. Compare what you see against the WIKI. Note drift, new/removed components, anomalies.
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
# Cluster Wiki

_No patrols have run yet. This file is the sentinel's memory and will be rewritten each patrol._

## Topology
_(unknown — first patrol will populate)_

## Baselines
_(unknown)_

## Watch-list
- (empty)

## Open questions
- (none yet)

## Anomaly log
- (none yet)
"""

# ------------------------------------------------------------------- dry-cluster fixtures
# Minimal canned outputs so the agent loop can be demoed without a real cluster.
# Keyed by a substring of the command; first match wins.
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
    ("get pods -A", (
        "NAMESPACE              NAME                         READY   STATUS             RESTARTS   AGE\n"
        "openshift-lightspeed   lightspeed-app-7c9-abc       1/1     Running            0          2d\n"
        "openshift-monitoring   prometheus-k8s-0             6/6     Running            0          40d\n"
        "demo-app               demo-6b5c-xyz                0/1     CrashLoopBackOff   128        3d\n"
        "demo-app               demo-6b5c-pqr                1/1     Running            0          3d\n"
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


def kubectl_readonly(arg_string: str, *, kubectl: str, dry: bool, verbose: bool) -> str:
    """The ONLY tool the model gets. Read-only enforcement is the safety boundary."""
    try:
        args = shlex.split(arg_string)
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
        out = out[:OUT_CAP] + f"\n...[truncated, {len(out)} chars total]"
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


def make_model() -> ChatOpenAI:
    """Any OpenAI-compatible endpoint, same env contract as the raw-requests version."""
    return ChatOpenAI(
        model=MODEL,
        base_url=BASE_URL,
        api_key=API_KEY,
        temperature=0.3,
        max_tokens=16000,
        timeout=300,
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


def notify(messages: list[str]) -> None:
    for msg in messages:
        print(f"\n[patrol] 🔔 NOTIFY: {msg}")
        if SLACK_URL:
            try:
                requests.post(SLACK_URL, json={"text": f":rotating_light: Cluster patrol: {msg}"},
                              timeout=15)
            except Exception as e:  # noqa: BLE001
                print(f"[patrol]   (slack post failed: {e})", file=sys.stderr)


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


REPORT_CONTRACT = """\
Produce the final PatrolReport for the cluster patrol above:
- "findings": everything worth recording. severity: "info" (note / baseline /
  expected state), "warn" (drift, degradation, something suspicious), "crit"
  (broken, needs a human now). Give each finding short "evidence" citing what was
  actually observed.
- "notifications": ONLY the warn/crit findings a human should actually see (don't
  cry wolf), as human-readable messages.
- "updated_wiki": the FULL new markdown content of the cluster wiki, well
  structured with sections: Topology, Baselines, Watch-list, Open questions,
  Anomaly log. Tag entries with (source, timestamp, confidence) inline and expire
  stale ones."""


async def shape_report(model: ChatOpenAI, messages: list, budget_cut: bool):
    """Tool-free structured-output pass over the finished patrol conversation.

    Same pattern as the OLS agentic sandbox's "shape pass": the agent explores
    freely (auto tool choice, natural termination); this pass converts the
    conversation — including the patrol's plain-text final summary, if it wrote
    one — into the typed PatrolReport. It runs identically whether the patrol
    completed naturally or was cut off by the step budget, so there is one
    reporting code path and no salvage special-case.
    """
    prefix = ("You are the REPORTING PASS for the cluster patrol recorded above. "
              "The patrol completed and wrote its final summary. "
              if not budget_cut else
              "You are the REPORTING PASS for the cluster patrol recorded above. "
              "The patrol was CUT OFF by its step budget mid-exploration; commands "
              "marked 'not executed' never ran. Report only on what was actually "
              "observed. ")
    structured = model.with_structured_output(PatrolReport, method="function_calling")
    msgs: list = [*messages, {"role": "user", "content": prefix + REPORT_CONTRACT}]
    for attempt in (1, 2):
        reason = None
        try:
            result = await structured.ainvoke(msgs)
            if result is not None:
                return result
            reason = "empty result"
        except Exception as e:  # noqa: BLE001
            reason = str(e)
        print(f"[patrol] reporting pass attempt {attempt} failed ({reason})",
              file=sys.stderr)
        msgs = [*msgs, {"role": "user",
                        "content": "That was not a valid PatrolReport. Produce it now."}]
    return None


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

    model = make_model()
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

    report = await shape_report(model, messages, budget_cut)
    if report is None:
        print("[patrol] WARNING: reporting pass produced no report; skipping wiki write.")
        return

    findings, notifications, new_wiki = report_fields(report)

    print(f"\n[patrol] findings ({len(findings)}):")
    for f in findings:
        print(f"  [{f.get('severity', '?'):>4}] {f.get('summary', '')}")
        if verbose and f.get("evidence"):
            print(f"         ↳ {f['evidence']}")

    notify([m for m in notifications if m])

    if new_wiki:
        wiki_path.write_text(new_wiki)
        print(f"[patrol] wiki updated → {wiki_path}")
    print(f"[patrol] tokens: in={tokens_in} out={tokens_out}")
    print(f"[patrol] === patrol done ===")


# --------------------------------------------------------------------------- main
def main() -> None:
    ap = argparse.ArgumentParser(description="Cluster Sentinel hacky PoC (deepagents engine)")
    ap.add_argument("--once", action="store_true", help="run a single patrol (default)")
    ap.add_argument("--loop", action="store_true", help="patrol repeatedly")
    ap.add_argument("--interval", type=int, default=int(os.environ.get("PATROL_INTERVAL", "900")),
                    help="seconds between patrols in --loop mode (default 900)")
    ap.add_argument("--dry-cluster", action="store_true",
                    help="use canned fixtures instead of a real cluster")
    ap.add_argument("--wiki", default=str(Path(__file__).parent / "cluster-wiki.md"),
                    help="path to the wiki memory file")
    ap.add_argument("--max-steps", type=int, default=MAX_STEPS,
                    help="max LLM turns per patrol")
    ap.add_argument("-v", "--verbose", action="store_true")
    args = ap.parse_args()

    if not API_KEY or not BASE_URL or not MODEL:
        die("set PATROL_API_KEY, PATROL_BASE_URL, and PATROL_MODEL in the environment "
            "(any OpenAI-compatible chat-completions endpoint).")

    kubectl = resolve_kubectl()
    wiki_path = Path(args.wiki)

    def one(fatal: bool) -> None:
        try:
            asyncio.run(run_patrol(kubectl=kubectl, wiki_path=wiki_path, dry=args.dry_cluster,
                                   verbose=args.verbose, max_steps=args.max_steps))
        except Exception as e:  # noqa: BLE001
            if fatal:
                raise
            print(f"[patrol] patrol failed ({e}); continuing loop", file=sys.stderr)

    if args.loop:
        print(f"[patrol] loop mode: every {args.interval}s (ctrl-c to stop)")
        try:
            while True:
                one(fatal=False)
                time.sleep(args.interval)
        except KeyboardInterrupt:
            print("\n[patrol] stopped.")
    else:
        one(fatal=True)


if __name__ == "__main__":
    main()
