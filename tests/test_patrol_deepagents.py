"""Fake-model e2e — the deepagents engine contract, end-to-end (#4).

Drives complete patrols through the REAL engine stack — ChatOpenAI + openai SDK +
create_deep_agent + guard + shape pass + wiki git tail — with only the HTTP
endpoint scripted: an in-process OpenAI-compatible fake over httpx.MockTransport
serves canned chat completions and records every request body, so the tests
assert on the wire behavior the model actually sees. The cluster is answered by
--dry-cluster fixtures; the model seam is `make_model` (monkeypatched to return
a ChatOpenAI pointed at the fake endpoint). No network, no PATROL_* env, no
cluster; the wiki, its repo, and any remote are test-local temporaries.

Covers (safety-model.md rules 6b-6c, wiki-memory.md rules 4f and 15-19):

- tool surface exactly [read_file, kubectl_readonly]; execute/task rejected
  as invalid tools listing that surface
- guard refusals (mutation verb, secret read) reaching the model as REFUSED
  tool results it can recover from
- natural termination AND budget cut both flowing through the same shape pass
  (reporting request binds only PatrolReport, forced tool choice, report
  contract present, completion vs cut-off prefix); budget-cut conversations
  mark never-executed commands
- wiki wiring deferred from #3: repo ensured at startup via main(), one commit
  per accepted wiki write (a byte-identical model rewrite still commits under
  #5 — the stamp advances patrol_count), push only when a remote is
  configured, failed reporting pass leaves the wiki untouched
- wiki skeleton wiring of #5: an invalid updated_wiki triggers exactly one
  repair pass carrying the validation errors and the rejected wiki, the
  repaired wiki is written with process-stamped frontmatter (patrol_count
  derived from the on-disk wiki, legacy wikis counting 0), and a twice-invalid
  wiki leaves the old wiki byte-for-byte intact with the patrol's findings
  still delivered
- recheck wiring of #6 (rule 12a): a Watch-list entry missing its recheck
  command fails validation and gets the same one-repair-pass treatment, the
  recheck error riding in the repair prompt
- stale-entry expiry wiring of #7 (rules 20-22): the expiry pass runs on
  accepted writes, between delivery and the stamp — a watch-list entry
  carried unchanged past PATROL_WIKI_STALE_AFTER flips to expired and lands
  at the end of the Anomaly log with its recheck and an auto-expired
  annotation (clock carried), a re-verified entry's clock resets, and the
  flip logs a console line
- working-set budget wiring of #40 (rules 28-29): the compaction pass runs
  after expiry and before the stamp — a resolved/expired Anomaly log over
  WIKI_ANOMALY_KEEP is written pruned to the last keep closed entries with
  one console line per prune, an over-budget wiki still writes and commits
  with one stderr warning (the budget never gates), and a repair pass
  carries the rejected wiki's budget warning alongside the structural
  errors
- one-event-loop loop mode of #50 (system-overview rule 5): two patrols
  through main() --loop on a single event loop, both writing, clean
  ctrl-c exit; a failed patrol is logged and the loop continues on the
  same loop
- request-bound wiring of #49 (reporting-notifications.md rule 8a):
  run_patrol builds the agent's model at the exploration bound and the
  shape pass's at the reporting bound, both through the make_model seam
- token-bound wiring of #63 (reporting-notifications.md rule 8b): the
  same two make_model calls carry the per-call-shape max_tokens too —
  the agent's at the exploration bound, the shape pass's at the
  reporting bound
- empty-wiki classification wiring of #51 (reporting-notifications.md
  rule 7b): a wiki-less report is retried once with a corrective message
  naming it; a good retry recovers; a still-empty retry delivers
  findings, writes nothing, and warns on stderr
- still-empty safety net wiring of #60 (wiki-memory.md rules 30-32): the
  wiki-repair pass's prompt carries the old wiki verbatim plus the
  observed findings and its own findings are never delivered; a valid
  repaired wiki writes through the standard tail; a wiki-repair failure
  minimal-writes the old wiki with the clock advanced; a legacy
  no-frontmatter wiki writes nothing at all

The `--raw` guard refusal is not e2e-covered: it is unit-checked in
test_kubectl_guard.py (kubectl-guard.md rule 6a, #11).

Run: uv run --group dev pytest tests/ -q
"""
import asyncio
import json
import re
import subprocess
import sys
from datetime import datetime
from pathlib import Path

import httpx
import yaml
from langchain_openai import ChatOpenAI

import patrol


# --------------------------------------------------------------- scripted endpoint

def _assistant_tool_call(name, args, call_id):
    """An OpenAI-protocol assistant message carrying one tool call."""
    return {"role": "assistant", "content": None,
            "tool_calls": [{"id": call_id, "type": "function",
                            "function": {"name": name,
                                         "arguments": json.dumps(args)}}]}


def _assistant_text(content):
    return {"role": "assistant", "content": content}


class ScriptedEndpoint:
    """In-process OpenAI-compatible endpoint (httpx.MockTransport).

    Agent-loop requests are answered from a scripted list of assistant turns;
    when the list runs out, `on_empty` decides ("summary" = plain final text,
    "toolcall" = keep calling kubectl_readonly forever, driving the budget cut).
    A request that forces tool_choice on PatrolReport is the shape pass: it is
    answered with the scripted report tool call ("ok") or with unusable plain
    text ("fail" — exercises the retry-then-give-up contract). `reports` (a
    list) serves sequential shape requests in order — report first, then the
    #5 repair pass — so a patrol can be scripted to emit an invalid wiki and
    then a corrected one. Every request body is recorded in `requests`.
    """

    def __init__(self, turns=(), report=None, on_empty="summary", report_mode="ok",
                 reports=None):
        self.turns = list(turns)
        self.report = report
        self.on_empty = on_empty
        self.report_mode = report_mode
        self.reports = list(reports) if reports is not None else None
        self._shape_seen = 0
        self.requests: list[dict] = []

    # -- wire plumbing -----------------------------------------------------
    def handler(self, request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        self.requests.append(body)
        choice = body.get("tool_choice")
        if isinstance(choice, dict) and choice.get("function", {}).get("name") == "PatrolReport":
            self._shape_seen += 1
            if self.report_mode == "fail":
                msg = _assistant_text("I would rather not produce a report.")
            else:
                if self.reports is not None:
                    served = self.reports[min(self._shape_seen - 1, len(self.reports) - 1)]
                else:
                    served = self.report
                msg = _assistant_tool_call("PatrolReport", served, "call_report")
            return self._completion(msg, "tool_calls" if self.report_mode != "fail" else "stop",
                                    stream=bool(body.get("stream")))
        if self.turns:
            msg = self.turns.pop(0)
        elif self.on_empty == "toolcall":
            msg = _assistant_tool_call("kubectl_readonly", {"command": "get nodes"}, "call_loop")
        else:
            msg = _assistant_text("Patrol complete. Stopping of my own accord.")
        finish = "tool_calls" if msg.get("tool_calls") else "stop"
        return self._completion(msg, finish, stream=bool(body.get("stream")))

    @staticmethod
    def _completion(message, finish, stream):
        if stream:  # SSE chunks, in case the stack switches to streamed calls
            chunks = []
            if message.get("content"):
                chunks.append({"delta": {"role": "assistant", "content": message["content"]}})
            for tc in message.get("tool_calls") or []:
                chunks.append({"delta": {"tool_calls": [dict(tc)]}})
            chunks.append({"delta": {}, "finish_reason": finish})
            events = []
            for c in chunks:
                chunk = {"id": "chatcmpl-fake", "object": "chat.completion.chunk",
                         "created": 1700000000, "model": "fake-model",
                         "choices": [dict(c, index=0)]}
                events.append("data: " + json.dumps(chunk) + "\n\n")
            return httpx.Response(200, content="".join(events).encode() + b"data: ",
                                  headers={"content-type": "text/event-stream"})
        return httpx.Response(200, json={
            "id": "chatcmpl-fake", "object": "chat.completion",
            "created": 1700000000, "model": "fake-model",
            "choices": [{"index": 0, "message": message, "finish_reason": finish}],
            "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
        })

    def model(self, timeout: float | None = None,
             max_tokens: int | None = None) -> ChatOpenAI:
        """A real ChatOpenAI wired to this endpoint — the make_model seam.
        Accepts (and ignores) the per-call-shape bound kwargs of #49/#63
        (timeout, max_tokens): both are client-side config, invisible on
        the wire, so the fake endpoint never enforces them — the
        make_model call itself is the assertion
        surface."""
        transport = httpx.MockTransport(self.handler)
        return ChatOpenAI(
            model="fake-model", base_url="http://fake.test/v1", api_key="test-key",
            temperature=0.3, max_tokens=1000,
            http_client=httpx.Client(transport=transport),
            http_async_client=httpx.AsyncClient(transport=transport),
        )

    # -- recorded-request views ---------------------------------------------
    @property
    def agent_requests(self):
        """Chat requests from the agent loop (kubectl_readonly offered)."""
        return [r for r in self.requests
                if any(t.get("function", {}).get("name") == "kubectl_readonly"
                       for t in r.get("tools") or [])]

    @property
    def shape_requests(self):
        """Chat requests from the shape pass (PatrolReport forced)."""
        return [r for r in self.requests
                if isinstance(r.get("tool_choice"), dict)
                and r["tool_choice"].get("function", {}).get("name") == "PatrolReport"]

    @staticmethod
    def tool_results(request) -> str:
        """Concatenated tool-result messages a request carries (what the model saw)."""
        return "\n".join(str(m.get("content")) for m in request.get("messages", [])
                         if m.get("role") == "tool")

    @staticmethod
    def last_user_prompt(request) -> str:
        for m in reversed(request.get("messages", [])):
            if m.get("role") == "user":
                return str(m.get("content"))
        return ""

    @staticmethod
    def conversation_text(request) -> str:
        """All message text a request carries (user + assistant + tool)."""
        return "\n".join(str(m.get("content")) for m in request.get("messages", []))


# --------------------------------------------------------------- shared fixtures

def wiki_text(marker="e2e wiki marker", patrol_count=9):
    """A skeleton-valid updated_wiki (wiki-memory.md rules 10-12a: the
    Watch-list entry carries its recheck command, rule 12a). The
    frontmatter values are deliberately the model's own guesses (patrol_count
    9, a bogus cluster id) so tests can prove the process stamp overrides
    them on write."""
    return (
        "---\n"
        "wiki_version: 1\n"
        "cluster_id: model-guess\n"
        f"patrol_count: {patrol_count}\n"
        'last_patrol: "2026-10-16T00:00:00+00:00"\n'
        "---\n"
        "# Cluster Wiki\n"
        "\n"
        "## Topology\n"
        f"- {marker} (kubectl get nodes, 2026-10-16, high)\n"
        "\n"
        "## Baselines\n"
        "_(none)_\n"
        "\n"
        "## Watch-list\n"
        "- id: W-1 · status: watching · first_seen: 2026-10-15 · last_verified: 2026-10-16 · source: kubectl get nodes · confidence: high · recheck: get nodes\n"
        "  worker-1 has been NotReady for 40 days.\n"
        "\n"
        "## Open questions\n"
        "- (none yet)\n"
        "\n"
        "## Anomaly log\n"
        "- id: A-1 · status: watching · first_seen: 2026-10-15 · last_verified: 2026-10-16 · source: kubectl get pods -A · confidence: medium\n"
        "  demo-app crashlooping.\n"
    )


def frontmatter(text):
    """The parsed YAML frontmatter of a wiki document."""
    lines = text.split("\n")
    assert lines[0] == "---", "not a frontmatter document"
    return yaml.safe_load("\n".join(lines[1:lines.index("---", 1)]))


def expiry_wiki_text(watch_body, anomaly_body="_(empty)_", marker="e2e wiki marker", patrol_count=9):
    """A skeleton-valid updated_wiki (rules 10-12a) whose Watch-list body is
    supplied — for #7 expiry scenarios (entries with controlled dates) — and
    whose Anomaly-log body may be supplied too (for #40 budget scenarios,
    entries with controlled counts). The frontmatter values are the model's
    own guesses; the process stamp and the expiry pass own the truth."""
    return (
        "---\n"
        "wiki_version: 1\n"
        "cluster_id: model-guess\n"
        f"patrol_count: {patrol_count}\n"
        'last_patrol: "2026-10-16T00:00:00+00:00"\n'
        "---\n"
        "# Cluster Wiki\n"
        "\n"
        "## Topology\n"
        f"- {marker} (kubectl get nodes, 2026-10-16, high)\n"
        "\n"
        "## Baselines\n"
        "_(none)_\n"
        "\n"
        "## Watch-list\n"
        f"{watch_body}\n"
        "\n"
        "## Open questions\n"
        "- (none yet)\n"
        "\n"
        "## Anomaly log\n"
        f"{anomaly_body}\n"
    )


WATCH_V1 = (
    "- id: W-1 · status: watching · first_seen: 2026-10-15 · last_verified: 2026-10-16"
    " · source: kubectl get nodes · confidence: high · recheck: get nodes\n"
    "  worker-1 has been NotReady for 40 days.\n"
    "- id: W-2 · status: watching · first_seen: 2026-10-15 · last_verified: 2026-10-16"
    " · source: kubectl get pods -A · confidence: medium · recheck: get pods -A\n"
    "  demo-app crashlooping."
)


REPORT = {
    "findings": [
        {"severity": "warn", "summary": "worker-1 NotReady for 40d",
         "evidence": "get nodes: worker-1 NotReady 40d"},
        {"severity": "info", "summary": "demo-app crashlooping",
         "evidence": "get pods -A: CrashLoopBackOff 128 restarts"},
    ],
    "notifications": ["worker-1 has been NotReady for 40 days"],
    "updated_wiki": wiki_text(),
}

# A patrol that explores, trips both guard refusals and both absent-tool calls,
# then writes its plain-text final summary (natural termination).
NATURAL_TURNS = [
    _assistant_tool_call("kubectl_readonly", {"command": "get nodes"}, "call_1"),
    # guard: mutating verb must be REFUSED by the tool layer
    _assistant_tool_call("kubectl_readonly",
                         {"command": "delete pod demo-6b5c-xyz -n demo-app"}, "call_2"),
    # guard: secret value read must be REFUSED
    _assistant_tool_call("kubectl_readonly",
                         {"command": "get secret demo-secret -n demo-app -o yaml"}, "call_3"),
    # absent tools: shell and subagent must not exist in the tool surface
    _assistant_tool_call("execute", {"command": "ls /"}, "call_4"),
    _assistant_tool_call("task", {"description": "go wild"}, "call_5"),
    # natural termination: plain-text final summary ends the agent loop
    _assistant_text("PATROL FINAL SUMMARY: worker-1 NotReady (warn); "
                    "demo-app crashlooping (info). Wiki update ready."),
]


def git(*args, cwd):
    """Run git in cwd, raising on failure — tests assert on real git state."""
    return subprocess.run(["git", *args], cwd=cwd, capture_output=True,
                          text=True, check=True)


def commit_count(cwd) -> int:
    """Commits in the repo (0 for a freshly initted one)."""
    return int(git("rev-list", "--count", "--all", cwd=cwd).stdout.strip() or 0)


def run_patrol_cli(monkeypatch, endpoint, wiki, *extra_args, wiki_remote="",
                   model_factory=None):
    """Run one full patrol through main() — argparse, env check, startup repo
    ensure, the engine loop, the shape pass, and the wiki git tail — with the
    model pointed at the scripted endpoint. Module-level config read from env
    at import (BASE_URL/MODEL/API_KEY/CLUSTER_ID/SLACK_URL/WIKI_REMOTE) is
    pinned explicitly so an ambient PATROL_*/SLACK_* environment cannot leak
    network calls into the e2e (rule 6c). `model_factory` overrides the
    make_model patch (e.g. a recording wrapper asserting on the #49 bound)."""
    monkeypatch.setattr(patrol, "make_model", model_factory or endpoint.model)
    monkeypatch.setattr(patrol, "BASE_URL", "http://fake.test/v1")
    monkeypatch.setattr(patrol, "MODEL", "fake-model")
    monkeypatch.setattr(patrol, "API_KEY", "test-key")
    monkeypatch.setattr(patrol, "CLUSTER_ID", "e2e-cluster")
    monkeypatch.setattr(patrol, "SLACK_URL", "")
    monkeypatch.setattr(patrol, "WIKI_REMOTE", wiki_remote)
    monkeypatch.setattr(sys, "argv",
                        ["patrol.py", "--once", "--dry-cluster", "--wiki", str(wiki),
                         *extra_args])
    patrol.main()


# --------------------------------------------------------------- engine contract

def test_natural_termination_full_patrol(tmp_path, monkeypatch, capsys):
    """Explore -> refusals -> invalid tools -> plain summary -> shape pass ->
    findings/notify -> wiki written and committed. The wire tool surface is
    exactly [read_file, kubectl_readonly] on every agent turn."""
    wiki = tmp_path / "wiki" / "cluster-wiki.md"
    endpoint = ScriptedEndpoint(turns=NATURAL_TURNS, report=REPORT)
    # explicit budget: an ambient PATROL_MAX_STEPS must not flip this scenario
    # into a budget cut (rule 6c determinism)
    run_patrol_cli(monkeypatch, endpoint, wiki, "--max-steps", "8")

    # wiki written with the report's updated_wiki and committed once
    assert "e2e wiki marker" in wiki.read_text()
    log = git("log", "-1", "--format=%ae|%s", cwd=wiki.parent).stdout.strip()
    assert log == "patrol@e2e-cluster|patrol 1: 2 findings, 1 warn"

    # #5: written in the skeleton, with process-stamped frontmatter — the
    # model's guesses (patrol_count 9, cluster_id model-guess) were overridden
    fm = frontmatter(wiki.read_text())
    assert fm["wiki_version"] == 1
    assert fm["cluster_id"] == "e2e-cluster"
    assert fm["patrol_count"] == 1
    datetime.fromisoformat(fm["last_patrol"])  # the patrol-start timestamp

    # every agent-turn request offers exactly the two allowed tools
    assert endpoint.agent_requests, "no agent requests recorded"
    for r in endpoint.agent_requests:
        offered = sorted(t["function"]["name"] for t in r["tools"])
        assert offered == ["kubectl_readonly", "read_file"], (
            f"tool surface must be exactly [read_file, kubectl_readonly], got {offered}")
    # and there is no third kind of request: everything recorded is either an
    # agent turn or the shape pass (no hidden tool-bearing call escapes the
    # surface check above)
    assert (len(endpoint.agent_requests) + len(endpoint.shape_requests)
            == len(endpoint.requests)), (
        "every model request must be an agent turn or a shape-pass request")

    # guard refusals reached the model as tool results it recovers from
    seen = "\n".join(endpoint.tool_results(r) for r in endpoint.agent_requests)
    assert "REFUSED" in seen and "not a read-only verb" in seen, (
        "the mutation-verb refusal must reach the model")
    assert "REFUSED" in seen and "Secret values" in seen, (
        "the secret-value refusal must reach the model")

    # execute/task were rejected as invalid tools listing the real surface
    assert "execute is not a valid tool" in seen
    assert "task is not a valid tool" in seen
    assert "read_file" in seen and "kubectl_readonly" in seen

    # the shape pass: one request, PatrolReport only, forced choice, contract
    # text, completion prefix, and the patrol's plain summary in the history
    shapes = endpoint.shape_requests
    assert len(shapes) == 1, f"expected exactly one shape-pass request, got {len(shapes)}"
    s = shapes[0]
    assert [t["function"]["name"] for t in s["tools"]] == ["PatrolReport"]
    assert s["tool_choice"] == {"type": "function", "function": {"name": "PatrolReport"}}
    prompt = endpoint.last_user_prompt(s)
    assert "Produce the final PatrolReport" in prompt          # report contract
    assert "completed and wrote its final summary" in prompt   # completion prefix
    assert "PATROL FINAL SUMMARY" in endpoint.conversation_text(s)  # summary fed to the pass

    # delivery: findings + notification printed
    out = capsys.readouterr().out
    assert "findings (2):" in out
    assert "[warn] worker-1 NotReady for 40d" in out
    assert "🔔 NOTIFY: worker-1 has been NotReady for 40 days" in out


def test_budget_cut_flows_through_same_shape_pass(tmp_path, monkeypatch, capsys):
    """A model that never stops exploring is cut by the step budget; the SAME
    shape pass produces the report (cut-off prefix), never-executed commands
    are marked, and the wiki is still written and committed."""
    wiki = tmp_path / "wiki" / "cluster-wiki.md"
    endpoint = ScriptedEndpoint(report=REPORT, on_empty="toolcall")
    run_patrol_cli(monkeypatch, endpoint, wiki, "--max-steps", "2")

    # the cut happened and was reported
    out = capsys.readouterr().out
    assert "exploration budget" in out and "exhausted" in out

    # same shape pass, cut-off prefix, dangling tool calls marked not-executed
    shapes = endpoint.shape_requests
    assert len(shapes) == 1
    s = shapes[0]
    assert [t["function"]["name"] for t in s["tools"]] == ["PatrolReport"]
    prompt = endpoint.last_user_prompt(s)
    assert "Produce the final PatrolReport" in prompt
    assert "CUT OFF" in prompt and "not executed" in prompt
    assert "step budget exhausted; command not executed." in endpoint.tool_results(s)

    # the report still landed: wiki written, committed once, stamped
    assert "e2e wiki marker" in wiki.read_text()
    assert commit_count(wiki.parent) == 1
    fm = frontmatter(wiki.read_text())
    assert fm["patrol_count"] == 1 and fm["cluster_id"] == "e2e-cluster"

    # and the model genuinely never wrote a final summary: no assistant text
    # in the conversation the shape pass received (it was cut mid-tool-loop)
    assert "FINAL SUMMARY" not in endpoint.conversation_text(s)


def test_failed_reporting_pass_leaves_wiki_untouched(tmp_path, monkeypatch, capsys):
    """Shape pass fails twice (retry included) -> no report -> warning, and the
    wiki is neither written nor committed."""
    wiki = tmp_path / "wiki" / "cluster-wiki.md"
    endpoint = ScriptedEndpoint(turns=NATURAL_TURNS[:1], report=REPORT,
                                report_mode="fail")
    run_patrol_cli(monkeypatch, endpoint, wiki, "--max-steps", "8")

    out = capsys.readouterr().out
    assert "reporting pass produced no report" in out
    # the retry happened: two shape requests, both answered unusably
    assert len(endpoint.shape_requests) == 2
    # wiki untouched: no file, and the startup-ensured repo has no commits
    assert not wiki.exists()
    assert commit_count(wiki.parent) == 0


# --------------------------------------------------------------- wiki wiring (#3)

def test_wiki_git_wiring_end_to_end(tmp_path, monkeypatch, capsys):
    """The wiring deferred from #3's unit tests, driven through main(), with
    the #5 skeleton semantics: startup repo ensure, one commit per accepted
    wiki write (the stamp advances patrol_count, so even a byte-identical
    model rewrite writes and commits), push only when a remote is configured."""
    wiki = tmp_path / "wiki" / "cluster-wiki.md"      # directory does not exist yet
    remote_a = tmp_path / "remote-a.git"
    remote_b = tmp_path / "remote-b.git"
    for r in (remote_a, remote_b):
        git("init", "--bare", "-b", "main", str(r), cwd=tmp_path)

    def report_with(wiki_body):
        return dict(REPORT, updated_wiki=wiki_text(wiki_body))

    # patrol 1: wiki v1 -> written (stamped), committed, pushed to remote_a
    run_patrol_cli(monkeypatch, ScriptedEndpoint(turns=NATURAL_TURNS[:1],
                                                 report=report_with("v1")), wiki,
                   "--max-steps", "8", wiki_remote=str(remote_a))
    assert (wiki.parent / ".git").is_dir(), "main() must ensure the wiki repo at startup"
    head1 = git("rev-parse", "HEAD", cwd=wiki.parent).stdout.strip()
    assert commit_count(wiki.parent) == 1
    assert frontmatter(wiki.read_text())["patrol_count"] == 1
    assert git("rev-parse", "--verify", "refs/heads/main",
               cwd=remote_a).stdout.strip() == head1, "commit must be pushed to the remote"

    capsys.readouterr()  # scope the no-push assertion below to patrol 2 alone

    # patrol 2: byte-identical MODEL rewrite -> the stamp advances
    # patrol_count (1 -> 2), so the file changes and it commits; no remote
    # configured -> no push happens
    run_patrol_cli(monkeypatch, ScriptedEndpoint(turns=NATURAL_TURNS[:1],
                                                 report=report_with("v1")), wiki,
                   "--max-steps", "8")
    assert commit_count(wiki.parent) == 2
    assert frontmatter(wiki.read_text())["patrol_count"] == 2
    out = capsys.readouterr().out
    assert "wiki push" not in out, "no push may be attempted without PATROL_WIKI_REMOTE"

    # patrol 3: changed wiki + a different remote -> commit lands, pushed there
    run_patrol_cli(monkeypatch, ScriptedEndpoint(turns=NATURAL_TURNS[:1],
                                                 report=report_with("v2")), wiki,
                   "--max-steps", "8", wiki_remote=str(remote_b))
    assert commit_count(wiki.parent) == 3
    head3 = git("rev-parse", "HEAD", cwd=wiki.parent).stdout.strip()
    assert git("rev-parse", "--verify", "refs/heads/main",
               cwd=remote_b).stdout.strip() == head3


# --------------------------------------------------------------- wiki skeleton (#5)

def test_invalid_wiki_triggers_one_repair_pass(tmp_path, monkeypatch, capsys):
    """A report whose updated_wiki fails skeleton validation gets exactly one
    repair pass — a second shape request carrying the validation errors and
    the rejected wiki — and the repaired report is the final one: its wiki is
    written (stamped) and its findings/notifications are delivered, not the
    first report's."""
    wiki = tmp_path / "wiki" / "cluster-wiki.md"
    rejected = wiki_text("REJECTED-WIKI-MARKER").replace(
        "patrol_count: 9\n", "patrol_count: 9\nbogus_key: 1\n")
    repaired = dict(REPORT,
                    updated_wiki=wiki_text("repaired marker"),
                    findings=[{"severity": "warn", "summary": "repaired finding",
                               "evidence": "e2e"}],
                    notifications=["repaired notification"])
    endpoint = ScriptedEndpoint(turns=NATURAL_TURNS[:1],
                                reports=[dict(REPORT, updated_wiki=rejected),
                                         repaired])
    run_patrol_cli(monkeypatch, endpoint, wiki, "--max-steps", "8")

    # exactly two shape requests: the report, then the single repair pass
    shapes = endpoint.shape_requests
    assert len(shapes) == 2, f"expected report + repair, got {len(shapes)}"
    repair_text = endpoint.conversation_text(shapes[1])
    assert "Produce the final PatrolReport" in repair_text  # a shape-pass call
    assert "bogus_key" in repair_text                       # the validation errors
    assert "REJECTED-WIKI-MARKER" in repair_text            # the rejected wiki

    # the repaired report won: its wiki on disk (stamped), its findings delivered
    assert "repaired marker" in wiki.read_text()
    fm = frontmatter(wiki.read_text())
    assert fm["patrol_count"] == 1 and fm["cluster_id"] == "e2e-cluster"
    out = capsys.readouterr().out
    assert "repaired finding" in out
    assert "🔔 NOTIFY: repaired notification" in out
    assert "[warn] worker-1 NotReady for 40d" not in out  # wholesale replacement
    assert "🔔 NOTIFY: worker-1 has been NotReady" not in out
    assert commit_count(wiki.parent) == 1


def test_missing_recheck_triggers_one_repair_pass(tmp_path, monkeypatch, capsys):
    """#6 rule 12a: a Watch-list entry without a recheck: command is a
    validation failure — it gets the same one repair pass as any skeleton
    error, with the recheck error in the repair prompt, and the repaired
    (recheck-carrying) wiki is the one written and committed."""
    wiki = tmp_path / "wiki" / "cluster-wiki.md"
    rejected = wiki_text("REJECTED-WIKI-MARKER").replace(
        " · recheck: get nodes", "")          # W-1 loses its recheck pair
    endpoint = ScriptedEndpoint(turns=NATURAL_TURNS[:1],
                                reports=[dict(REPORT, updated_wiki=rejected),
                                         dict(REPORT,
                                              updated_wiki=wiki_text("repaired marker"))])
    run_patrol_cli(monkeypatch, endpoint, wiki, "--max-steps", "8")

    # exactly two shape requests: the report, then the single repair pass
    shapes = endpoint.shape_requests
    assert len(shapes) == 2, f"expected report + repair, got {len(shapes)}"
    repair_text = endpoint.conversation_text(shapes[1])
    assert "recheck" in repair_text            # the recheck validation error
    assert "REJECTED-WIKI-MARKER" in repair_text  # the rejected wiki

    # the repaired report won: its wiki (with the recheck) is on disk
    assert "repaired marker" in wiki.read_text()
    assert " · recheck: get nodes" in wiki.read_text()
    assert commit_count(wiki.parent) == 1


def test_twice_invalid_wiki_keeps_old_wiki(tmp_path, monkeypatch, capsys):
    """Both the report's wiki and the repair's wiki fail validation -> the old
    wiki stands byte-for-byte, nothing is committed, a warning is logged, and
    the patrol's findings and notifications still deliver (rule 19)."""
    wiki_dir = tmp_path / "wiki"
    wiki_dir.mkdir()
    wiki = wiki_dir / "cluster-wiki.md"
    old_wiki = wiki_text("old world", patrol_count=5)
    wiki.write_text(old_wiki)

    rejected = wiki_text("REJECTED-WIKI-MARKER").replace(
        "patrol_count: 9\n", "patrol_count: 9\nbogus_key: 1\n")
    endpoint = ScriptedEndpoint(turns=NATURAL_TURNS[:1],
                                reports=[dict(REPORT, updated_wiki=rejected),
                                         dict(REPORT, updated_wiki=rejected)])
    run_patrol_cli(monkeypatch, endpoint, wiki, "--max-steps", "8")

    assert len(endpoint.shape_requests) == 2   # report + the one repair pass
    assert wiki.read_text() == old_wiki        # byte-for-byte
    assert commit_count(wiki_dir) == 0         # startup-ensured repo, no commits

    # the patrol's observations survived the wiki rejection
    captured = capsys.readouterr()
    assert "[warn] worker-1 NotReady for 40d" in captured.out
    assert "🔔 NOTIFY: worker-1 has been NotReady for 40 days" in captured.out
    # and the rejection was warned about, not silent
    assert re.search(r"(?i)wiki", captured.err)
    assert re.search(r"(?i)valid|reject|keep", captured.err)


def test_stamp_derives_patrol_count_from_disk(tmp_path, monkeypatch):
    """Rule 17: patrol_count = the on-disk wiki's value + 1 (never the model's
    guess), and a legacy pre-#5 wiki without frontmatter counts as 0 — the
    first skeleton write stamps 1."""
    wiki_dir = tmp_path / "wiki"
    wiki_dir.mkdir()
    wiki = wiki_dir / "cluster-wiki.md"

    # on-disk wiki at patrol_count 5 -> next write stamps 6
    wiki.write_text(wiki_text("v0", patrol_count=5))
    run_patrol_cli(monkeypatch, ScriptedEndpoint(
        turns=NATURAL_TURNS[:1],
        report=dict(REPORT, updated_wiki=wiki_text("v1", patrol_count=99))), wiki,
        "--max-steps", "8")
    assert frontmatter(wiki.read_text())["patrol_count"] == 6

    # legacy pre-#5 wiki (no frontmatter at all) counts as 0 -> stamps 1
    wiki.write_text("# Cluster Wiki\n\n## Topology\n- legacy prose-only wiki\n")
    run_patrol_cli(monkeypatch, ScriptedEndpoint(
        turns=NATURAL_TURNS[:1],
        report=dict(REPORT, updated_wiki=wiki_text("v2", patrol_count=99))), wiki,
        "--max-steps", "8")
    assert frontmatter(wiki.read_text())["patrol_count"] == 1
    assert "v2" in wiki.read_text()


# --------------------------------------------------------------- stale-entry expiry (#7)

def test_stale_entry_expires_and_reverified_entry_survives(tmp_path, monkeypatch, capsys):
    """#7 rules 20-22 wired through main(): patrol 1 writes two fresh entries
    (clocks stamped 1). Patrol 2 re-verifies W-1 (last_verified advances ->
    clock resets to 2) and carries W-2 unchanged (clock carried 1;
    STALE_AFTER=1 -> age 1 flips): W-2 lands expired at the end of the Anomaly
    log with its recheck and the auto-expired annotation, W-1 stays watching,
    the flip logs a console line, and both writes commit."""
    wiki = tmp_path / "wiki" / "cluster-wiki.md"
    monkeypatch.setattr(patrol, "STALE_AFTER", 1)

    def report_with(watch_body):
        return dict(REPORT, updated_wiki=expiry_wiki_text(watch_body))

    # patrol 1: two fresh entries -> written with clocks stamped at 1
    run_patrol_cli(monkeypatch, ScriptedEndpoint(turns=NATURAL_TURNS[:1],
                                                 report=report_with(WATCH_V1)), wiki,
                   "--max-steps", "8")
    assert frontmatter(wiki.read_text())["patrol_count"] == 1
    assert wiki.read_text().count("verified_patrol: 1") == 2

    capsys.readouterr()  # scope the patrol-2 assertions to patrol 2

    # patrol 2: W-1 re-verified (date advanced), W-2 copied unchanged
    watch_v2 = WATCH_V1.replace(
        "- id: W-1 · status: watching · first_seen: 2026-10-15 · last_verified: 2026-10-16",
        "- id: W-1 · status: watching · first_seen: 2026-10-15 · last_verified: 2026-10-17")
    run_patrol_cli(monkeypatch, ScriptedEndpoint(turns=NATURAL_TURNS[:1],
                                                 report=report_with(watch_v2)), wiki,
                   "--max-steps", "8")

    final = wiki.read_text()
    assert frontmatter(final)["patrol_count"] == 2
    assert patrol.validate_wiki(final) == []

    wl = final.split("## Watch-list", 1)[1].split("## Open questions", 1)[0]
    al = final.split("## Anomaly log", 1)[1]
    # W-2 flipped out of the Watch-list; W-1 survives with a reset clock
    assert "id: W-2" not in wl
    assert "id: W-1" in wl and "status: watching" in wl
    assert "verified_patrol: 2" in wl
    # W-2 expired into the Anomaly log: carried clock, recheck kept, annotation
    assert "id: W-2 · status: expired" in al
    assert "recheck: get pods -A" in al
    assert "verified_patrol: 1" in al
    assert "auto-expired at patrol 2: unverified for 1 patrols" in al
    assert "_(empty)_" in al  # the placeholder line is untouched prose

    assert commit_count(wiki.parent) == 2  # one commit per accepted write
    # expiry is wiki housekeeping, never a finding or notification (rule 21):
    # the findings block still reports the report's own two findings, and no
    # notification mentions the expired entry
    out = capsys.readouterr().out
    assert "findings (2):" in out
    assert not any("NOTIFY" in l and "W-2" in l for l in out.splitlines())
    # one console line per flip, naming the id and its unverified-patrol count
    flip_lines = [l for l in out.splitlines() if "auto-expired" in l]
    assert len(flip_lines) == 1
    assert "W-2" in flip_lines[0] and "unverified for 1" in flip_lines[0]


# --------------------------------------------------------------- working-set budget (#40)

def test_anomaly_log_compaction_wiring(tmp_path, monkeypatch, capsys):
    """#40 rule 28 wired through main(): a report whose Anomaly log carries
    more resolved/expired entries than WIKI_ANOMALY_KEEP is written pruned —
    the last `keep` closed entries survive, earlier ones vanish from the
    written wiki (git history retains them), one console line per prune
    names the id and the budget, and the written wiki still validates."""
    wiki = tmp_path / "wiki" / "cluster-wiki.md"
    monkeypatch.setattr(patrol, "WIKI_ANOMALY_KEEP", 2)
    anomaly_body = "\n".join(
        f"- id: A-{i} · status: expired · first_seen: 2026-10-01"
        f" · last_verified: 2026-10-16 · source: kubectl get pods -A"
        f" · confidence: low\n  history entry {i}." for i in range(1, 5))
    report = dict(REPORT, updated_wiki=expiry_wiki_text(WATCH_V1, anomaly_body))
    run_patrol_cli(monkeypatch, ScriptedEndpoint(turns=NATURAL_TURNS[:1],
                                                 report=report), wiki,
                   "--max-steps", "8")

    final = wiki.read_text()
    assert patrol.validate_wiki(final) == []
    al = final.split("## Anomaly log", 1)[1]
    assert "id: A-3" in al and "id: A-4" in al         # the last keep=2 survive
    assert "id: A-1" not in al and "id: A-2" not in al  # pruned from the working set
    assert commit_count(wiki.parent) == 1               # the accepted (pruned) write committed
    out = capsys.readouterr().out
    prune_lines = [l for l in out.splitlines() if "pruned" in l]
    assert len(prune_lines) == 2, out
    assert "A-1" in prune_lines[0] and "A-2" in prune_lines[1]
    assert "keep budget 2" in prune_lines[0]
    # pruning is wiki housekeeping, never a finding or notification (rule 28)
    assert "findings (2):" in out
    assert not any("NOTIFY" in l and "A-1" in l for l in out.splitlines())


def test_pruned_entries_recoverable_from_git_history(tmp_path, monkeypatch, capsys):
    """#40 rule 28's archive half, two patrols: patrol 1 (keep 4) writes four
    closed Anomaly-log entries; patrol 2 (keep 2) prunes the two oldest from
    the working set — and they stay readable in the wiki repo's previous
    commit. The stance, made exact: git history is the archive; the wiki is
    the working set."""
    wiki = tmp_path / "wiki" / "cluster-wiki.md"
    monkeypatch.setattr(patrol, "STALE_AFTER", 5)  # determinism: no flips here
    anomaly_body = "\n".join(
        f"- id: A-{i} · status: expired · first_seen: 2026-10-01"
        f" · last_verified: 2026-10-16 · source: kubectl get pods -A"
        f" · confidence: low\n  history entry {i}." for i in range(1, 5))
    report = dict(REPORT, updated_wiki=expiry_wiki_text(WATCH_V1, anomaly_body))

    monkeypatch.setattr(patrol, "WIKI_ANOMALY_KEEP", 4)   # patrol 1: all fit
    run_patrol_cli(monkeypatch, ScriptedEndpoint(turns=NATURAL_TURNS[:1],
                                                 report=report), wiki,
                   "--max-steps", "8")
    assert "id: A-1" in wiki.read_text()

    monkeypatch.setattr(patrol, "WIKI_ANOMALY_KEEP", 2)   # patrol 2: budget cut
    run_patrol_cli(monkeypatch, ScriptedEndpoint(turns=NATURAL_TURNS[:1],
                                                 report=report), wiki,
                   "--max-steps", "8")
    final = wiki.read_text()
    assert "id: A-1" not in final and "id: A-2" not in final  # pruned on write
    assert "id: A-3" in final and "id: A-4" in final
    assert commit_count(wiki.parent) == 2

    # the pruned entries stay recoverable from the wiki repo's history
    history = patrol.wiki_history(wiki, 2)
    assert len(history) == 2
    assert "id: A-1" in history[0] and "id: A-2" in history[0]
    assert "id: A-1" not in history[1]


def test_over_budget_wiki_writes_with_stderr_warning(tmp_path, monkeypatch, capsys):
    """#40 rule 29 wired: the budget is soft — an over-budget wiki is
    accepted, written, committed, and stamped like any other; the overshoot
    surfaces as one stderr warning naming the budget, and the patrol's
    findings still deliver."""
    wiki = tmp_path / "wiki" / "cluster-wiki.md"
    monkeypatch.setattr(patrol, "WIKI_CHAR_BUDGET", 500)
    big = wiki_text("big but valid")
    assert len(big) > 500 and patrol.validate_wiki(big) == []
    run_patrol_cli(monkeypatch, ScriptedEndpoint(
        turns=NATURAL_TURNS[:1], report=dict(REPORT, updated_wiki=big)),
        wiki, "--max-steps", "8")

    assert "big but valid" in wiki.read_text()   # written anyway (soft)
    assert commit_count(wiki.parent) == 1        # committed anyway
    captured = capsys.readouterr()
    assert "findings (2):" in captured.out       # the patrol delivered
    assert "over" in captured.err and "length budget" in captured.err
    assert "500" in captured.err                 # the budget is named


def test_repair_prompt_carries_the_budget_warning(tmp_path, monkeypatch, capsys):
    """#40 rule 29's repair half: when a repair pass runs, the rejected
    wiki's budget warning rides the error list (the repair prompt carries
    the structural errors AND the over-budget warning), but acceptance
    stays structural — the repaired, still over-budget wiki is written."""
    wiki = tmp_path / "wiki" / "cluster-wiki.md"
    monkeypatch.setattr(patrol, "WIKI_CHAR_BUDGET", 500)
    rejected = wiki_text("REJECTED-WIKI-MARKER").replace(
        "patrol_count: 9\n", "patrol_count: 9\nbogus_key: 1\n")
    assert len(rejected) > 500
    endpoint = ScriptedEndpoint(
        turns=NATURAL_TURNS[:1],
        reports=[dict(REPORT, updated_wiki=rejected),
                 dict(REPORT, updated_wiki=wiki_text("repaired marker"))])
    run_patrol_cli(monkeypatch, endpoint, wiki, "--max-steps", "8")

    shapes = endpoint.shape_requests
    assert len(shapes) == 2, f"expected report + repair, got {len(shapes)}"
    repair_text = endpoint.conversation_text(shapes[1])
    assert "bogus_key" in repair_text        # the structural errors
    assert "length budget" in repair_text     # the budget warning rides along
    assert "summarize" in repair_text
    # acceptance stayed structural: the over-budget repaired wiki was written
    assert "repaired marker" in wiki.read_text()
    assert commit_count(wiki.parent) == 1


def test_wiki_flag_default_is_the_wiki_directory():
    """The --wiki default points at wiki/cluster-wiki.md beside patrol.py (the
    wiki directory is the repo root) — wiki-memory.md rule 1. Argparse wraps the
    long default across lines in --help, so the default is pinned on the
    argparse wiring (same pattern as test_cli_defaults.py)."""
    source = Path(patrol.__file__).read_text()
    m = re.search(r'"--wiki",\s*default=str\(Path\(__file__\)\.parent\s*/\s*"wiki"\s*/\s*"cluster-wiki\.md"\)',
                  source)
    assert m, "--wiki must default to wiki/cluster-wiki.md beside patrol.py"


# ------------------------------------------------- one event loop per process (#50)

def test_loop_mode_runs_patrols_on_one_event_loop(tmp_path, monkeypatch, capsys):
    """#50 (system-overview rule 5) wiring: loop mode runs every patrol of
    the process on one event loop — a fresh asyncio.run per patrol strands
    loop-bound engine state on a closed loop (real-cluster validation caught
    a one-time 'Event loop is closed' failure on the second patrol). Two
    patrols run through main() --loop; the second completion raises
    KeyboardInterrupt exactly where ctrl-c would."""
    endpoint = ScriptedEndpoint(report=REPORT)
    wiki = tmp_path / "wiki" / "cluster-wiki.md"
    loops = []
    real_run = patrol.run_patrol

    async def recording_run(**kw):
        loops.append(asyncio.get_running_loop())
        await real_run(**kw)
        if len(loops) == 2:
            raise KeyboardInterrupt  # ctrl-c after the second patrol

    monkeypatch.setattr(patrol, "run_patrol", recording_run)
    run_patrol_cli(monkeypatch, endpoint, wiki, "--loop", "--interval", "0")

    assert len(loops) == 2
    assert loops[0] is loops[1]                   # one loop for all patrols
    assert commit_count(wiki.parent) == 2         # both patrols wrote + committed
    assert frontmatter(wiki.read_text())["patrol_count"] == 2
    captured = capsys.readouterr()
    assert "[patrol] stopped." in captured.out    # clean ctrl-c exit
    assert "Event loop is closed" not in captured.err


def test_loop_mode_continues_after_a_failed_patrol(tmp_path, monkeypatch, capsys):
    """#50 (system-overview rule 5) wiring: the one-loop restructure keeps
    loop mode's catch-and-continue — a patrol exception is logged and the
    loop goes on, on the same event loop (the per-patrol try/except must
    sit inside the loop body, not around it)."""
    endpoint = ScriptedEndpoint(report=REPORT)
    wiki = tmp_path / "wiki" / "cluster-wiki.md"
    loops = []
    real_run = patrol.run_patrol

    async def recording_run(**kw):
        loops.append(asyncio.get_running_loop())
        if len(loops) == 1:
            raise RuntimeError("boom")            # first patrol fails
        await real_run(**kw)
        if len(loops) == 2:
            raise KeyboardInterrupt                # ctrl-c after the second

    monkeypatch.setattr(patrol, "run_patrol", recording_run)
    run_patrol_cli(monkeypatch, endpoint, wiki, "--loop", "--interval", "0")

    assert len(loops) == 2                         # the loop continued
    assert loops[0] is loops[1]                    # ...on the same event loop
    captured = capsys.readouterr()
    assert "patrol failed (boom); continuing loop" in captured.err
    assert "[patrol] stopped." in captured.out    # still a clean ctrl-c exit
    assert commit_count(wiki.parent) == 1          # only patrol 2 wrote

# ---------------------------------------------------- request bounds (#49)

def test_shape_pass_model_carries_the_reporting_bound(tmp_path, monkeypatch, capsys):
    """#49 rule 8a wiring: run_patrol builds two model instances through the
    make_model seam — the agent's at the exploration bound, the shape pass's
    at the reporting bound."""
    endpoint = ScriptedEndpoint(report=REPORT)
    wiki = tmp_path / "wiki" / "cluster-wiki.md"
    bounds = []
    real_model = endpoint.model

    def recording_model(**kwargs):
        bounds.append(kwargs.get("timeout"))
        return real_model()

    run_patrol_cli(monkeypatch, endpoint, wiki, model_factory=recording_model)
    # order pins the assignment: the agent's model (built first) carries the
    # exploration bound, the shape pass's the reporting bound — a swap fails
    assert bounds == [patrol.REQUEST_TIMEOUT, patrol.REPORT_TIMEOUT]


def test_repair_reuses_the_shape_pass_model(tmp_path, monkeypatch, capsys):
    """#49 (agent-engine.md): the repair call reuses the shape pass's model
    instance — a repair scenario still makes exactly two make_model calls
    (agent + shape), never a third for the repair."""
    rejected = wiki_text("REJECTED-WIKI-MARKER").replace(
        "patrol_count: 9\n", "patrol_count: 9\nbogus_key: 1\n")
    endpoint = ScriptedEndpoint(
        turns=NATURAL_TURNS[:1],
        reports=[dict(REPORT, updated_wiki=rejected),
                 dict(REPORT, updated_wiki=wiki_text("repaired marker"))])
    wiki = tmp_path / "wiki" / "cluster-wiki.md"
    bounds = []
    real_model = endpoint.model

    def recording_model(**kwargs):
        bounds.append(kwargs.get("timeout"))
        return real_model()

    run_patrol_cli(monkeypatch, endpoint, wiki, "--max-steps", "8",
                   model_factory=recording_model)
    assert len(endpoint.shape_requests) == 2      # the repair pass ran
    assert bounds == [patrol.REQUEST_TIMEOUT, patrol.REPORT_TIMEOUT]
    assert "repaired marker" in wiki.read_text()


def test_shape_pass_model_carries_the_reporting_token_bound(tmp_path, monkeypatch, capsys):
    """#63 rule 8b wiring: the same two make_model calls, both bounds —
    the agent's model carries the exploration timeout AND token bound, the
    shape pass's the reporting pair."""
    endpoint = ScriptedEndpoint(report=REPORT)
    wiki = tmp_path / "wiki" / "cluster-wiki.md"
    calls = []
    real_model = endpoint.model

    def recording_model(**kwargs):
        calls.append((kwargs.get("timeout"), kwargs.get("max_tokens")))
        return real_model(**kwargs)

    run_patrol_cli(monkeypatch, endpoint, wiki, model_factory=recording_model)
    # order pins the assignment: agent first, shape pass second — a swap or
    # a missing token bound fails
    assert calls == [(patrol.REQUEST_TIMEOUT, patrol.REQUEST_MAX_TOKENS),
                     (patrol.REPORT_TIMEOUT, patrol.REPORT_MAX_TOKENS)]


# --------------------------------------------- empty-wiki classification (#51)

# A structurally valid report whose updated_wiki is empty — the #51 failure
# shape observed in real-cluster validation (the field's lenient default
# parses an omission to "").
EMPTY_WIKI_REPORT = {
    "findings": [
        {"severity": "crit", "summary": "control-plane operators degraded",
         "evidence": "get clusteroperators: multiple Degraded=True"},
    ],
    "notifications": ["control-plane operators degraded"],
    "updated_wiki": "",
}


def test_empty_wiki_report_retries_and_recovers(tmp_path, monkeypatch, capsys):
    """#51 rule 7b wiring: a valid-but-wiki-less report is a failed attempt —
    one retry whose corrective message names the empty updated_wiki; the
    retry's report replaces the first wholesale (its findings, its wiki)."""
    endpoint = ScriptedEndpoint(turns=NATURAL_TURNS[:1],
                                reports=[EMPTY_WIKI_REPORT, REPORT])
    wiki = tmp_path / "wiki" / "cluster-wiki.md"
    run_patrol_cli(monkeypatch, endpoint, wiki, "--max-steps", "8")

    shapes = endpoint.shape_requests
    assert len(shapes) == 2, f"expected report + empty-wiki retry, got {len(shapes)}"
    corrective = endpoint.last_user_prompt(shapes[1])
    assert "updated_wiki" in corrective and "empty" in corrective.lower()

    out = capsys.readouterr().out
    assert "worker-1 NotReady" in out            # the retry's findings deliver
    assert "control-plane operators degraded" not in out  # first report replaced
    assert "e2e wiki marker" in wiki.read_text()  # the retry's wiki was written
    assert commit_count(wiki.parent) == 1


def test_still_empty_wiki_warns_and_preserves_findings(tmp_path, monkeypatch, capsys):
    """#51 rule 7b wiring: when the retry also omits the wiki, the report is
    not discarded — findings and notifications deliver, the rule-7b warning
    fires first, and then the #60 safety net takes over: the wiki-repair
    pass runs (with its own retry), and its double failure minimal-writes
    the stamped seed (a fresh deployment — no file on disk)."""
    endpoint = ScriptedEndpoint(turns=NATURAL_TURNS[:1],
                                reports=[EMPTY_WIKI_REPORT, EMPTY_WIKI_REPORT,
                                         EMPTY_WIKI_REPORT, EMPTY_WIKI_REPORT])
    wiki = tmp_path / "wiki" / "cluster-wiki.md"
    run_patrol_cli(monkeypatch, endpoint, wiki, "--max-steps", "8")

    # report + empty-wiki retry + wiki-repair pass + its empty-wiki retry
    assert len(endpoint.shape_requests) == 4
    captured = capsys.readouterr()
    assert "control-plane operators degraded" in captured.out   # findings deliver
    assert "🔔 NOTIFY: control-plane operators degraded" in captured.out
    # rule 7b's verbatim warning still fires first (rule 32) — visible, never silent
    assert ("[patrol] WARNING: reporting pass returned an empty updated_wiki; "
            "keeping the previous wiki, writing nothing") in captured.err
    # rule 31's file-absent clause: the fallback stamps-and-writes the seed
    assert "minimal write" in captured.out
    assert wiki.exists()
    assert frontmatter(wiki.read_text())["patrol_count"] == 1
    assert commit_count(wiki.parent) == 1


def test_repair_that_stays_wiki_less_falls_to_keep_old_wiki(tmp_path, monkeypatch, capsys):
    """#51 rule 7b's repair clause, still-empty leg: a repair that stays
    wiki-less after its named retry falls to rule 16's keep-old-wiki
    outcome — the first report's findings deliver, the old wiki stands,
    and the wiki-rejection warning fires (the first report's wiki was
    non-empty; it failed validation, not emission)."""
    rejected = wiki_text("REJECTED-WIKI-MARKER").replace(
        "patrol_count: 9\n", "patrol_count: 9\nbogus_key: 1\n")
    endpoint = ScriptedEndpoint(turns=NATURAL_TURNS[:1],
                                reports=[dict(REPORT, updated_wiki=rejected),
                                         EMPTY_WIKI_REPORT, EMPTY_WIKI_REPORT])
    wiki = tmp_path / "wiki" / "cluster-wiki.md"
    run_patrol_cli(monkeypatch, endpoint, wiki, "--max-steps", "8")

    shapes = endpoint.shape_requests
    assert len(shapes) == 3  # report + repair + the repair's empty-wiki retry
    captured = capsys.readouterr()
    assert "worker-1 NotReady" in captured.out    # the FIRST report's findings
    assert "control-plane operators degraded" not in captured.out
    assert not wiki.exists()                      # the old wiki stands
    assert "wiki update rejected" in captured.err # rule 16's warning
    assert "empty updated_wiki" not in captured.err  # NOT rule 7b's


# ------------------------------------------- still-empty safety net (#60)

# The wiki-repair pass's report: only its wiki is used — its findings and
# notifications are NEVER delivered (the call never saw the cluster).
WIKI_REPAIR_REPORT = {
    "findings": [
        {"severity": "info", "summary": "wiki-repair-only finding",
         "evidence": "never observed"},
    ],
    "notifications": ["wiki-repair-only notification"],
    "updated_wiki": wiki_text("wiki-repair-marker"),
}


def test_still_empty_runs_the_wiki_repair_pass(tmp_path, monkeypatch, capsys):
    """#60 rule 30 wiring: after the still-empty outcome (two wiki-less
    reports) the tail runs one wiki-repair pass whose prompt carries the
    old wiki verbatim and the observed findings; the observed report's
    findings deliver (the repair's never do), and the repaired wiki
    writes through the standard tail."""
    endpoint = ScriptedEndpoint(turns=NATURAL_TURNS[:1],
                                reports=[EMPTY_WIKI_REPORT, EMPTY_WIKI_REPORT,
                                         WIKI_REPAIR_REPORT])
    wiki = tmp_path / "wiki" / "cluster-wiki.md"
    run_patrol_cli(monkeypatch, endpoint, wiki, "--max-steps", "8")

    shapes = endpoint.shape_requests
    assert len(shapes) == 3, (f"expected report + retry + wiki-repair pass, "
                              f"got {len(shapes)}")
    prompt = endpoint.last_user_prompt(shapes[2])
    # the old wiki (the seed — no file on disk) rides verbatim (rule 30)
    assert "No patrols have run yet." in prompt
    # the observed report's findings ride (rule 30)
    assert "control-plane operators degraded" in prompt
    captured = capsys.readouterr()
    # the OBSERVED findings deliver; the repair's never do (rule 30)
    assert "control-plane operators degraded" in captured.out
    assert "wiki-repair-only finding" not in captured.out
    assert "wiki-repair-only notification" not in captured.out
    # rule 7b's warning still fires first (rule 32)
    assert ("[patrol] WARNING: reporting pass returned an empty updated_wiki; "
            "keeping the previous wiki, writing nothing") in captured.err
    # the repaired wiki writes through the standard tail (expire → compact
    # → stamp → write → commit)
    assert "wiki-repair-marker" in wiki.read_text()
    assert frontmatter(wiki.read_text())["patrol_count"] == 1
    assert commit_count(wiki.parent) == 1


def test_wiki_repair_failure_minimal_writes_the_old_wiki(tmp_path, monkeypatch, capsys):
    """#60 rule 31 wiring: when the wiki-repair pass also comes back
    wiki-less (after its own retry), the minimal-write fallback runs the
    accepted-write tail on the old wiki — body preserved, patrol_count
    advanced, last_patrol refreshed, one commit, one console line."""
    old = wiki_text("on-disk-marker", patrol_count=3)
    wiki = tmp_path / "wiki" / "cluster-wiki.md"
    wiki.parent.mkdir(parents=True)
    wiki.write_text(old)
    endpoint = ScriptedEndpoint(turns=NATURAL_TURNS[:1],
                                reports=[EMPTY_WIKI_REPORT] * 4)
    run_patrol_cli(monkeypatch, endpoint, wiki, "--max-steps", "8")

    # report + empty-wiki retry + wiki-repair pass + its empty-wiki retry
    assert len(endpoint.shape_requests) == 4
    captured = capsys.readouterr()
    assert "control-plane operators degraded" in captured.out  # findings deliver
    assert "minimal write" in captured.out                      # rule 32's line
    written = wiki.read_text()
    assert "on-disk-marker" in written          # the old body stands (rule 31)
    fm = frontmatter(written)
    assert fm["patrol_count"] == 4              # the clock advanced (rule 31)
    assert fm["last_patrol"] != "2026-10-16T00:00:00+00:00"  # refreshed
    assert fm["cluster_id"] == "e2e-cluster"    # stamped (rule 17)
    assert commit_count(wiki.parent) == 1


def test_minimal_write_on_a_legacy_wiki_writes_nothing(tmp_path, monkeypatch, capsys):
    """#60 rule 31's legacy clause: a wiki with no frontmatter block has
    no clock to advance — the fallback is a no-op and the keep-old-wiki
    outcome stands (rule 4), the rule-7b warning alone."""
    legacy = "# Just markdown\n\n## Topology\n- old notes\n"
    wiki = tmp_path / "wiki" / "cluster-wiki.md"
    wiki.parent.mkdir(parents=True)
    wiki.write_text(legacy)
    endpoint = ScriptedEndpoint(turns=NATURAL_TURNS[:1],
                                reports=[EMPTY_WIKI_REPORT] * 4)
    run_patrol_cli(monkeypatch, endpoint, wiki, "--max-steps", "8")

    captured = capsys.readouterr()
    assert "control-plane operators degraded" in captured.out
    assert wiki.read_text() == legacy           # untouched (rule 31)
    assert commit_count(wiki.parent) == 0


def test_minimal_write_on_a_fresh_deployment_writes_the_stamped_seed(
        tmp_path, monkeypatch, capsys):
    """#60 rule 31's file-absent clause + rule 14a's exception: a brand-new
    deployment whose first patrol double-fails minimal-writes the seed —
    stamped (gate-clean content, honest clock), the one case where the
    seed reaches disk."""
    endpoint = ScriptedEndpoint(turns=NATURAL_TURNS[:1],
                                reports=[EMPTY_WIKI_REPORT] * 4)
    wiki = tmp_path / "wiki" / "cluster-wiki.md"
    run_patrol_cli(monkeypatch, endpoint, wiki, "--max-steps", "8")

    assert len(endpoint.shape_requests) == 4
    captured = capsys.readouterr()
    assert "minimal write" in captured.out
    assert wiki.exists(), "the stamped seed must be written (rule 31)"
    written = wiki.read_text()
    assert "No patrols have run yet." in written   # the seed body
    fm = frontmatter(written)
    assert fm["patrol_count"] == 1                  # stamped, honest clock
    assert fm["cluster_id"] == "e2e-cluster"
    assert commit_count(wiki.parent) == 1
