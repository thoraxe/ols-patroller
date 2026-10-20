#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = [
#     "deepagents>=0.7",
#     "langchain-openai>=1.0",
#     "pydantic>=2",
#     "pyyaml>=6",
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
from datetime import date, datetime
from pathlib import Path
from typing import Literal

import requests
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

# --------------------------------------------------------------------------- config
BASE_URL   = os.environ.get("PATROL_BASE_URL", "")   # e.g. https://api.example.com/v1
MODEL      = os.environ.get("PATROL_MODEL", "")      # model id on that endpoint
API_KEY    = os.environ.get("PATROL_API_KEY", "")
KUBECTL    = os.environ.get("KUBECTL", "")  # resolved later
SLACK_URL  = os.environ.get("SLACK_WEBHOOK_URL", "")
MAX_STEPS  = int(os.environ.get("PATROL_MAX_STEPS", "30"))  # LLM turns per patrol
TOOL_TIMEOUT = int(os.environ.get("PATROL_TOOL_TIMEOUT", "60"))
OUT_CAP    = 15_000  # max chars of tool output fed back to the model
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
   how major pieces connect. Prefer a broad sweep first, then drill into anything suspicious.
2. Compare what you see against the WIKI. Note drift, new/removed components, anomalies.
   Watch-list entries carry a `recheck:` command — the read-only invocation that
   re-verifies the entry. Run an entry's recheck FIRST when re-checking it, and
   rewrite a recheck whose verification path has changed (keep it current).
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
- "updated_wiki": the FULL new markdown content of the cluster wiki in the
  skeleton format. It is validated before it may replace the wiki on disk, and
  MUST have exactly this structure:
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
     inline and expire stale entries rather than deleting history."""


async def shape_report(model: ChatOpenAI, messages: list, budget_cut: bool,
                       repair: tuple[str, list[str]] | None = None):
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
    """
    if repair is None:
        prefix = ("You are the REPORTING PASS for the cluster patrol recorded above. "
                  "The patrol completed and wrote its final summary. "
                  if not budget_cut else
                  "You are the REPORTING PASS for the cluster patrol recorded above. "
                  "The patrol was CUT OFF by its step budget mid-exploration; commands "
                  "marked 'not executed' never ran. Report only on what was actually "
                  "observed. ")
    else:
        rejected_wiki, wiki_errors = repair
        prefix = ("You are the REPORTING PASS for the cluster patrol recorded above. "
                  "Your previous updated_wiki FAILED the wiki skeleton validation and "
                  "was REJECTED — it was not written. Produce a corrected PatrolReport "
                  "whose updated_wiki fixes EVERY validation error below:\n"
                  + "\n".join(f"- {e}" for e in wiki_errors)
                  + "\n\nThe rejected wiki was:\n\n" + rejected_wiki + "\n")
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


def _is_iso_datetime(value: object) -> bool:
    """Rule 10: an ISO-8601 datetime string, or a YAML-parsed timestamp."""
    if isinstance(value, (datetime, date)):  # PyYAML typed an unquoted scalar
        return True
    if not isinstance(value, str):
        return False
    try:
        datetime.fromisoformat(value)
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
    if "cluster_id" in fm:
        v = fm["cluster_id"]
        if not isinstance(v, str) or not v:
            errors.append(f"frontmatter: cluster_id must be a non-empty string (got {v!r})")
    if "patrol_count" in fm:
        v = fm["patrol_count"]
        if isinstance(v, bool) or not isinstance(v, int) or v < 1:
            errors.append(f"frontmatter: patrol_count must be a positive integer (got {v!r})")
    if "last_patrol" in fm and not _is_iso_datetime(fm["last_patrol"]):
        errors.append(f"frontmatter: last_patrol must be an ISO-8601 datetime "
                      f"(got {fm['last_patrol']!r})")
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


def _run_git(wiki_dir: Path, *args: str, timeout: float | None = None):
    """Run git in wiki_dir with patrol-owned config only (never the user's).

    Every invocation passes `safe.directory` for the wiki directory so git does
    not depend on the invoking user's config or HOME (non-root, random-UID safe,
    rule 4d). Returns None when git itself is unusable — callers degrade to
    file-only mode instead of failing the patrol (rule 4b).
    """
    cmd = ["git", "-C", str(wiki_dir),
           "-c", f"safe.directory={wiki_dir.resolve()}", *args]
    try:
        return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
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

    # Wiki gate (#5, rules 15-19): the model's wiki is validated before it may
    # replace memory. Invalid -> exactly one repair pass; a repair whose wiki
    # validates replaces the report wholesale, otherwise the old wiki stands
    # and the FIRST report's findings/notifications still deliver (rule 19: a
    # wiki-format failure never costs the patrol's observations). An empty
    # updated_wiki skips validation and repair entirely (rule 4 unchanged).
    if new_wiki:
        errors = validate_wiki(new_wiki)
        if errors:
            print(f"[patrol] WARNING: updated wiki failed skeleton validation "
                  f"({len(errors)} error(s)); running one repair pass", file=sys.stderr)
            repaired = await shape_report(model, messages, budget_cut,
                                          repair=(new_wiki, errors))
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

    # Delivery (rule 16): findings and notifications are delivered after the
    # validation/repair decision resolves and before the physical write, so
    # exactly one report — the final one — is delivered.
    print(f"\n[patrol] findings ({len(findings)}):")
    for f in findings:
        print(f"  [{f.get('severity', '?'):>4}] {f.get('summary', '')}")
        if verbose and f.get("evidence"):
            print(f"         ↳ {f['evidence']}")

    notify([m for m in notifications if m])

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
        # Rule 17: stamp the process-owned frontmatter fields — patrol_count
        # from the ON-DISK wiki (never the model's guess), last_patrol from this
        # patrol's start, cluster_id from config. Every accepted write advances
        # patrol_count, so every accepted write changes the file and commits.
        stamped = stamp_wiki(new_wiki,
                             patrol_count=patrol_count,
                             last_patrol=ts,
                             cluster_id=CLUSTER_ID)
        write_wiki_atomic(wiki_path, stamped)
        print(f"[patrol] wiki updated → {wiki_path}")
        if commit_wiki(wiki_path, findings, CLUSTER_ID) and WIKI_REMOTE:
            push_wiki(wiki_path.parent, WIKI_REMOTE)
    print(f"[patrol] tokens: in={tokens_in} out={tokens_out}")
    print(f"[patrol] === patrol done ===")


# --------------------------------------------------------------------------- main
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
    args = ap.parse_args()

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

    def one(fatal: bool) -> None:
        try:
            asyncio.run(run_patrol(kubectl=kubectl, wiki_path=wiki_path, dry=args.dry_cluster,
                                   verbose=verbose, max_steps=args.max_steps))
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
