"""Fake-model e2e — the deepagents engine contract, end-to-end (#4).

Drives complete patrols through the REAL engine stack — ChatOpenAI + openai SDK +
create_deep_agent + guard + shape pass + wiki git tail — with only the HTTP
endpoint scripted: an in-process OpenAI-compatible fake over httpx.MockTransport
serves canned chat completions and records every request body, so the tests
assert on the wire behavior the model actually sees. The cluster is answered by
--dry-cluster fixtures; the model seam is `make_model` (monkeypatched to return
a ChatOpenAI pointed at the fake endpoint). No network, no PATROL_* env, no
cluster; the wiki, its repo, and any remote are test-local temporaries.

Covers (safety-model.md rules 6b-6c, wiki-memory.md rule 4f):

- tool surface exactly [read_file, kubectl_readonly]; execute/task rejected
  as invalid tools listing that surface
- guard refusals (mutation verb, secret read) reaching the model as REFUSED
  tool results it can recover from
- natural termination AND budget cut both flowing through the same shape pass
  (reporting request binds only PatrolReport, forced tool choice, report
  contract present, completion vs cut-off prefix); budget-cut conversations
  mark never-executed commands
- wiki wiring deferred from #3: repo ensured at startup via main(), one commit
  per wiki-changing patrol, no commit on a byte-identical rewrite, push only
  when a remote is configured, failed reporting pass leaves the wiki untouched

The `--raw` guard refusal is not e2e-covered: it is unit-checked in
test_kubectl_guard.py (kubectl-guard.md rule 6a, #11).

Run: uv run --group dev pytest tests/ -q
"""
import json
import re
import subprocess
import sys
from pathlib import Path

import httpx
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
    text ("fail" — exercises the retry-then-give-up contract). Every request
    body is recorded in `requests`.
    """

    def __init__(self, turns=(), report=None, on_empty="summary", report_mode="ok"):
        self.turns = list(turns)
        self.report = report
        self.on_empty = on_empty
        self.report_mode = report_mode
        self.requests: list[dict] = []

    # -- wire plumbing -----------------------------------------------------
    def handler(self, request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        self.requests.append(body)
        choice = body.get("tool_choice")
        if isinstance(choice, dict) and choice.get("function", {}).get("name") == "PatrolReport":
            if self.report_mode == "fail":
                msg = _assistant_text("I would rather not produce a report.")
            else:
                msg = _assistant_tool_call("PatrolReport", self.report, "call_report")
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

    def model(self) -> ChatOpenAI:
        """A real ChatOpenAI wired to this endpoint — the make_model seam."""
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

REPORT = {
    "findings": [
        {"severity": "warn", "summary": "worker-1 NotReady for 40d",
         "evidence": "get nodes: worker-1 NotReady 40d"},
        {"severity": "info", "summary": "demo-app crashlooping",
         "evidence": "get pods -A: CrashLoopBackOff 128 restarts"},
    ],
    "notifications": ["worker-1 has been NotReady for 40 days"],
    "updated_wiki": "# Cluster Wiki\n\n## Topology\n- e2e wiki marker\n",
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


def git_try(*args, cwd):
    """Run git without raising — for negative assertions (missing refs)."""
    return subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True)


def commit_count(cwd) -> int:
    """Commits in the repo (0 for a freshly initted one)."""
    return int(git("rev-list", "--count", "--all", cwd=cwd).stdout.strip() or 0)


def run_patrol_cli(monkeypatch, endpoint, wiki, *extra_args, wiki_remote=""):
    """Run one full patrol through main() — argparse, env check, startup repo
    ensure, the engine loop, the shape pass, and the wiki git tail — with the
    model pointed at the scripted endpoint. Module-level config read from env
    at import (BASE_URL/MODEL/API_KEY/CLUSTER_ID/SLACK_URL/WIKI_REMOTE) is
    pinned explicitly so an ambient PATROL_*/SLACK_* environment cannot leak
    network calls into the e2e (rule 6c)."""
    monkeypatch.setattr(patrol, "make_model", endpoint.model)
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

    # the report still landed: wiki written, committed once
    assert "e2e wiki marker" in wiki.read_text()
    assert commit_count(wiki.parent) == 1

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
    """The wiring deferred from #3's unit tests, driven through main():
    startup repo ensure, one commit per wiki-changing patrol, no commit on a
    byte-identical rewrite, push only when a remote is configured."""
    wiki = tmp_path / "wiki" / "cluster-wiki.md"      # directory does not exist yet
    remote_a = tmp_path / "remote-a.git"
    remote_b = tmp_path / "remote-b.git"
    for r in (remote_a, remote_b):
        git("init", "--bare", "-b", "main", str(r), cwd=tmp_path)

    def report_with(wiki_body):
        return dict(REPORT, updated_wiki=f"# Cluster Wiki\n\n## Topology\n- {wiki_body}\n")

    # patrol 1: wiki v1 -> written, committed, pushed to the configured remote
    run_patrol_cli(monkeypatch, ScriptedEndpoint(turns=NATURAL_TURNS[:1],
                                                 report=report_with("v1")), wiki,
                   "--max-steps", "8", wiki_remote=str(remote_a))
    assert (wiki.parent / ".git").is_dir(), "main() must ensure the wiki repo at startup"
    head1 = git("rev-parse", "HEAD", cwd=wiki.parent).stdout.strip()
    assert commit_count(wiki.parent) == 1
    assert git("rev-parse", "--verify", "refs/heads/main",
               cwd=remote_a).stdout.strip() == head1, "commit must be pushed to the remote"

    # patrol 2: byte-identical rewrite -> no commit, and (a different remote
    # being configured) no push happens — remote-b stays empty
    run_patrol_cli(monkeypatch, ScriptedEndpoint(turns=NATURAL_TURNS[:1],
                                                 report=report_with("v1")), wiki,
                   "--max-steps", "8", wiki_remote=str(remote_b))
    assert commit_count(wiki.parent) == 1
    assert git_try("rev-parse", "--verify", "refs/heads/main",
                   cwd=remote_b).returncode != 0, "no commit means no push"

    # patrol 3: changed wiki, NO remote configured -> commit lands, no push
    capsys.readouterr()  # scope the no-push assertion to this patrol alone
    run_patrol_cli(monkeypatch, ScriptedEndpoint(turns=NATURAL_TURNS[:1],
                                                 report=report_with("v2")), wiki,
                   "--max-steps", "8")
    assert commit_count(wiki.parent) == 2
    out = capsys.readouterr().out
    assert "wiki push" not in out, "no push may be attempted without PATROL_WIKI_REMOTE"
    assert git_try("rev-parse", "--verify", "refs/heads/main",
                   cwd=remote_b).returncode != 0


def test_wiki_flag_default_is_the_wiki_directory():
    """The --wiki default points at wiki/cluster-wiki.md beside patrol.py (the
    wiki directory is the repo root) — wiki-memory.md rule 1. Argparse wraps the
    long default across lines in --help, so the default is pinned on the
    argparse wiring (same pattern as test_cli_defaults.py)."""
    source = Path(patrol.__file__).read_text()
    m = re.search(r'"--wiki",\s*default=str\(Path\(__file__\)\.parent\s*/\s*"wiki"\s*/\s*"cluster-wiki\.md"\)',
                  source)
    assert m, "--wiki must default to wiki/cluster-wiki.md beside patrol.py"
