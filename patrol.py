#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = ["requests>=2.31"]
# ///
"""
Cluster Sentinel — hacky PoC.

A proactive, non-deterministic SRE "patrol": on each run an LLM agentically explores
the live cluster read-only (it decides what to look at), validates reality against a
persistent markdown "wiki" (its memory), rewrites the wiki, and notifies a human about
anything that looks wrong.

Read-only by design: the single tool exposed to the model is a whitelisted `oc/kubectl`
that only permits non-mutating verbs. Nothing here ever changes the cluster.

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
import json
import os
import re
import shlex
import subprocess
import sys
import time
from pathlib import Path

import requests

# --------------------------------------------------------------------------- config
BASE_URL   = os.environ.get("PATROL_BASE_URL", "")   # e.g. https://api.example.com/v1
MODEL      = os.environ.get("PATROL_MODEL", "")      # model id on that endpoint
API_KEY    = os.environ.get("PATROL_API_KEY", "")
KUBECTL    = os.environ.get("KUBECTL", "")  # resolved later
SLACK_URL  = os.environ.get("SLACK_WEBHOOK_URL", "")
MAX_STEPS  = int(os.environ.get("PATROL_MAX_STEPS", "15"))
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

When you are DONE exploring, reply with NO tool calls and ONLY a single JSON object (optionally in a
```json code fence) matching exactly:
{
  "findings": [{"severity": "info|warn|crit", "summary": "...", "evidence": "short, cite what you saw"}],
  "notifications": ["human-readable message to send", "..."],
  "updated_wiki": "the FULL new markdown content of the cluster wiki"
}
Only put warn/crit items a human should see into "notifications". Keep "updated_wiki" well structured
with sections: Topology, Baselines, Watch-list, Open questions, Anomaly log. Tag entries with
(source, timestamp, confidence) inline and expire stale ones.
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
    return f"(dry-cluster) no fixture for: {cmd}\n(treat as empty / not present)"


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


TOOLS_SPEC = [{
    "type": "function",
    "function": {
        "name": "kubectl_readonly",
        "description": ("Run a READ-ONLY oc/kubectl command and return its output. "
                        "Only non-mutating verbs are permitted (get, describe, logs, top, "
                        "version, api-resources, explain, status, cluster-info). "
                        "Pass everything after `oc` as a single string, e.g. "
                        "'get pods -A' or 'describe node worker-1'."),
        "parameters": {
            "type": "object",
            "properties": {
                "command": {"type": "string",
                            "description": "args after oc/kubectl, e.g. 'get clusteroperators'"}
            },
            "required": ["command"],
        },
    },
}]


def chat(messages: list[dict], *, with_tools: bool) -> tuple[dict, dict]:
    body = {
        "model": MODEL,
        "messages": messages,
        "temperature": 0.3,
        "max_tokens": 16000,
    }
    if with_tools:
        body["tools"] = TOOLS_SPEC
        body["tool_choice"] = "auto"
    r = requests.post(
        f"{BASE_URL}/chat/completions",
        headers={"Authorization": f"Bearer {API_KEY}", "Content-Type": "application/json"},
        json=body, timeout=300,
    )
    if r.status_code != 200:
        die(f"LLM call failed: HTTP {r.status_code}: {r.text[:500]}")
    data = r.json()
    return data["choices"][0]["message"], data.get("usage") or {}


def preview(s: str, n: int = 200) -> str:
    """One-line, length-capped preview for live output."""
    s = " ".join((s or "").split())
    return s if len(s) <= n else s[:n] + "…"


def extract_json(text: str) -> dict | None:
    if not text:
        return None
    m = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL)
    blob = m.group(1) if m else None
    if blob is None:
        s, e = text.find("{"), text.rfind("}")
        blob = text[s:e + 1] if (s != -1 and e > s) else None
    if blob is None:
        return None
    try:
        return json.loads(blob)
    except json.JSONDecodeError:
        return None


def notify(messages: list[str]) -> None:
    for msg in messages:
        print(f"\n[patrol] 🔔 NOTIFY: {msg}")
        if SLACK_URL:
            try:
                requests.post(SLACK_URL, json={"text": f":rotating_light: Cluster patrol: {msg}"},
                              timeout=15)
            except Exception as e:  # noqa: BLE001
                print(f"[patrol]   (slack post failed: {e})", file=sys.stderr)


# --------------------------------------------------------------------------- one patrol
def run_patrol(*, kubectl: str, wiki_path: Path, dry: bool, verbose: bool, max_steps: int) -> None:
    ts = time.strftime("%Y-%m-%dT%H:%M:%S%z")
    print(f"\n[patrol] === patrol start {ts} (model={MODEL}, dry={dry}) ===")

    wiki = wiki_path.read_text() if wiki_path.exists() else WIKI_SEED

    # best-effort dedup context: what's already being handled?
    inflight = kubectl_readonly("get agenticruns -A", kubectl=kubectl, dry=dry, verbose=verbose)

    user = (
        f"Current time: {ts}\n\n"
        f"=== IN-FLIGHT AGENTIC RUNS / WORK ALREADY IN PROGRESS ===\n{inflight}\n\n"
        f"=== CLUSTER WIKI (your prior notes — may be stale, verify against reality) ===\n{wiki}\n\n"
        f"Begin your patrol. Explore with kubectl_readonly, then return the final JSON."
    )
    messages = [{"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": user}]

    final_text = ""
    for step in range(1, max_steps + 1):
        msg, usage = chat(messages, with_tools=True)
        messages.append(msg)
        tool_calls = msg.get("tool_calls") or []
        if verbose:
            tin, tout = usage.get("prompt_tokens", "?"), usage.get("completion_tokens", "?")
            rc = msg.get("reasoning_content") or ""
            print(f"[patrol] ── turn {step} (tokens in={tin} out={tout}) ──", file=sys.stderr)
            if rc:
                print(f"[patrol] 💭 {preview(rc, 400)}", file=sys.stderr)
            c = msg.get("content") or ""
            if c and not tool_calls:
                print(f"[patrol] 💬 {preview(c, 600)}", file=sys.stderr)
        if not tool_calls:
            final_text = msg.get("content") or ""
            if verbose:
                print(f"[patrol]   model finished at step {step}", file=sys.stderr)
            break
        for tc in tool_calls:
            try:
                args = json.loads(tc["function"]["arguments"] or "{}")
            except json.JSONDecodeError:
                args = {}
            result = kubectl_readonly(args.get("command", ""),
                                      kubectl=kubectl, dry=dry, verbose=verbose)
            if verbose:
                print(f"[patrol]     ↳ {preview(result, 220)}", file=sys.stderr)
            messages.append({"role": "tool", "tool_call_id": tc["id"], "content": result})
    else:
        # exhausted steps — force a final answer
        messages.append({"role": "user",
                         "content": "Step budget reached. Stop exploring and return ONLY the final JSON now."})
        m, _ = chat(messages, with_tools=False)
        final_text = m.get("content") or ""

    result = extract_json(final_text)
    if result is None:
        # one retry asking for strict JSON
        messages.append({"role": "user",
                         "content": "Your last reply was not valid JSON. Reply with ONLY the JSON object."})
        result = extract_json(chat(messages, with_tools=False)[0].get("content") or "")
    if result is None:
        print("[patrol] WARNING: could not parse final JSON; skipping wiki write.")
        print("---- raw model output ----\n" + final_text[:2000])
        return

    findings = result.get("findings", [])
    print(f"\n[patrol] findings ({len(findings)}):")
    for f in findings:
        print(f"  [{f.get('severity','?'):>4}] {f.get('summary','')}")
        if verbose and f.get("evidence"):
            print(f"         ↳ {f['evidence']}")

    notify([m for m in result.get("notifications", []) if m])

    new_wiki = result.get("updated_wiki")
    if new_wiki:
        wiki_path.write_text(new_wiki)
        print(f"[patrol] wiki updated → {wiki_path}")
    print(f"[patrol] === patrol done ===")


# --------------------------------------------------------------------------- main
def main() -> None:
    ap = argparse.ArgumentParser(description="Cluster Sentinel hacky PoC")
    ap.add_argument("--once", action="store_true", help="run a single patrol (default)")
    ap.add_argument("--loop", action="store_true", help="patrol repeatedly")
    ap.add_argument("--interval", type=int, default=int(os.environ.get("PATROL_INTERVAL", "900")),
                    help="seconds between patrols in --loop mode (default 900)")
    ap.add_argument("--dry-cluster", action="store_true",
                    help="use canned fixtures instead of a real cluster")
    ap.add_argument("--wiki", default=str(Path(__file__).parent / "cluster-wiki.md"),
                    help="path to the wiki memory file")
    ap.add_argument("--max-steps", type=int, default=MAX_STEPS)
    ap.add_argument("-v", "--verbose", action="store_true")
    args = ap.parse_args()

    if not API_KEY or not BASE_URL or not MODEL:
        die("set PATROL_API_KEY, PATROL_BASE_URL, and PATROL_MODEL in the environment "
            "(any OpenAI-compatible chat-completions endpoint).")

    kubectl = resolve_kubectl()
    wiki_path = Path(args.wiki)

    def one():
        run_patrol(kubectl=kubectl, wiki_path=wiki_path, dry=args.dry_cluster,
                   verbose=args.verbose, max_steps=args.max_steps)

    if args.loop:
        print(f"[patrol] loop mode: every {args.interval}s (ctrl-c to stop)")
        try:
            while True:
                one()
                time.sleep(args.interval)
        except KeyboardInterrupt:
            print("\n[patrol] stopped.")
    else:
        one()


if __name__ == "__main__":
    main()
