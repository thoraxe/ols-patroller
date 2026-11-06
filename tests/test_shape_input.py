"""Compacted shape-pass input + visible failure timing (#66 spec PR), and
the ceiling-aware retry (#88 spec PR).

Real-cluster soak evidence (v0.2.3, ols-demo-2): the shape pass's input —
the full raw patrol conversation plus the wiki plus the contract — grew
with the wiki until the pass legitimately flirted with the model
gateway's ~900 s request ceiling: one patrol's both attempts 504ed at
~902 s, the next patrol's first attempt too, and `report is None` lost
everything (findings, notifications, wiki write) with only timestamps to
infer the ceiling from. Raising REPORT_TIMEOUT (#49) cannot help while
the *gateway* caps the request.

The contract (reporting-notifications.md rules 6, 7, 7e, 7f; #66):
- every shape-pass invocation carries the COMPACTED conversation (pure
  `compact_messages`, `SHAPE_TOOL_KEEP` 1500 chars): all messages kept in
  order, tool-result bodies over the cap truncated with a marker, all
  non-tool text — the patrol's final summary included — verbatim;
- every attempt-failure log line carries the attempt's elapsed seconds,
  and an exception's reason passes the pure `classify_exception`
  (OpenAI-style 5xx or a timeout marker -> `gateway failure (...)`);
- a total-failure patrol warns, delivers one FAILURE notification
  (pinned message), and minimal-writes the old wiki (the wiki-repair
  pass is skipped — no findings to merge).

#88's follow-up evidence (v0.3.0): 4 of the last 5 patrols produced no
report, both attempts dying as a 504 at ~902 s — the gateway window, not
a token bound. The contract additions (rules 7f amendment, 7g):
- the attempt-failure line also carries the attempt's token counts —
  `in I out O tokens`, `?` when there is no raw response (an exception);
- a **gateway-failure** retry rebuilds its base from the raw conversation
  compacted at the hard-coded `SHAPE_TOOL_KEEP_RETRY` (500 chars), discards
  the attempt-1 message list, and appends one user message: mode prefix,
  the pinned ceiling corrective (verbatim), `REPORT_CONTRACT`, then the
  rule-29a size note last;
- non-gateway failures keep today's append-only retry: the 1500-char base
  plus the existing corrective.

The pure-function matrices and the shape-pass prompt compaction are
driven here; the total-failure tail wiring (notification + minimal
write) is driven in test_patrol_deepagents.py.

Run: uv run --group dev pytest tests/ -q
"""
import asyncio
import re

import patrol
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage


# ------------------------------------------------------------ constants

def test_shape_tool_keep_constant():
    """Rule 7e (#66): the compaction cap is a hard-coded constant (not
    env-configurable — the same convention as the rules-8a/8b bounds)."""
    assert patrol.SHAPE_TOOL_KEEP == 1500


def test_shape_tool_keep_retry_constant():
    """Rule 7g (#88): the gateway-failure retry's cap is a hard-coded
    constant too — a third of the first attempt's tool-result payload."""
    assert patrol.SHAPE_TOOL_KEEP_RETRY == 500


# ------------------------------------------------- compact_messages matrix

def _tool(content):
    return ToolMessage(content=content, tool_call_id="call-1",
                       name="kubectl_readonly")


def _marker(keep, total):
    return (f"[truncated by the patroller: first {keep} of {total} "
            "characters — full text not shown]")


def test_compact_messages_truncates_long_tool_results():
    """Rule 7e: a tool-result body over SHAPE_TOOL_KEEP characters becomes
    its first keep chars plus the marker line — langchain messages and
    plain dicts alike, roles and order preserved."""
    big = "x" * 4000
    msgs = [SystemMessage("system prompt"),
            {"role": "user", "content": "mission"},
            _tool(big),
            {"role": "tool", "content": big},
            AIMessage("FINAL SUMMARY")]
    out = patrol.compact_messages(msgs)
    assert len(out) == 5                       # every message kept, in order
    assert out[0].content == "system prompt"   # non-tool messages verbatim
    assert out[1]["content"] == "mission"
    expected = big[:1500] + "\n" + _marker(1500, 4000)
    assert out[2].content == expected          # langchain ToolMessage
    assert out[3]["content"] == expected       # dict form, same rule
    assert out[4].content == "FINAL SUMMARY"   # the final summary survives
    # pure: the input list is never mutated
    assert msgs[2].content == big
    assert msgs[3]["content"] == big


def test_compact_messages_leaves_short_content_alone():
    """Rule 7e: lossy in exactly one dimension — at-or-under-cap tool
    bodies and every non-tool message pass through unchanged, object
    identity included."""
    tool_ok = _tool("y" * 1500)
    msgs = [HumanMessage("mission"), tool_ok, AIMessage("summary")]
    out = patrol.compact_messages(msgs)
    assert out[0] is msgs[0] and out[1] is tool_ok and out[2] is msgs[2]
    assert out[1].content == "y" * 1500


def test_compact_messages_keep_chars_is_a_parameter():
    """Rule 7e: the cap parametrizes the pure function (tests are not
    pinned to the constant)."""
    big = "z" * 100
    out = patrol.compact_messages([_tool(big)], keep_chars=10)
    assert out[0].content == "z" * 10 + "\n" + _marker(10, 100)


# --------------------------------------- shape pass carries the compacted form

class _StubShapeModel:
    """A model stub for shape_report: with_structured_output returns a
    structured runnable whose ainvoke serves scripted results and records
    every prompt it received (the compaction assertion surface)."""

    def __init__(self, results):
        self.results = list(results)
        self.prompts = []

    def with_structured_output(self, schema, method=None, include_raw=None):
        stub = self

        class _Structured:
            async def ainvoke(self, msgs):
                stub.prompts.append(list(msgs))
                served = stub.results.pop(0)
                if isinstance(served, Exception):
                    raise served
                return served

        return _Structured()


def _report(wiki):
    return patrol.PatrolReport(findings=[], notifications=[], updated_wiki=wiki)


def _conversation():
    big = "x" * 4000
    return [HumanMessage("mission"),
            _tool(big),
            AIMessage("FINAL SUMMARY: all quiet")]


# Rule 7g's pinned corrective, verbatim (reporting-notifications.md).
CEILING_CORRECTIVE = (
    "Your previous attempt failed at the model gateway's request ceiling — "
    "the request took too long. Return the PatrolReport as concisely as "
    "possible: the shortest valid updated_wiki, findings reduced to one "
    "line each, no optional prose.")


def test_shape_pass_prompt_is_compacted():
    """Rule 7e: the reporting pass's prompt carries the compacted
    conversation — the tool body truncated with its marker, the final
    summary verbatim — and the corrective retry message is appended after
    compaction, untruncated. Rule 7g: a NON-gateway failure keeps exactly
    this append-only retry (the 1500-char base, the generic corrective),
    so the attempt-1 message list is extended, never rebuilt."""
    stub = _StubShapeModel([RuntimeError("nope"), _report("full wiki")])
    result = asyncio.run(patrol.shape_report(
        stub, _conversation(), budget_cut=False))
    assert result.updated_wiki == "full wiki"
    assert len(stub.prompts) == 2
    prompt = stub.prompts[0]
    tool_body = prompt[1].content
    assert tool_body.startswith("x" * 1500)
    assert _marker(1500, 4000) in tool_body
    assert "x" * 1600 not in tool_body           # the tail is gone
    assert "FINAL SUMMARY: all quiet" in prompt[2].content
    # the retry corrective rides after the compacted base, in full
    assert "not a valid PatrolReport" in stub.prompts[1][-1]["content"]
    assert stub.prompts[1][1].content == tool_body  # base unchanged on retry
    # the ceiling-aware leg is NOT taken: the smaller cap and the pinned
    # ceiling corrective belong to gateway failures alone (rule 7g)
    assert _marker(500, 4000) not in stub.prompts[1][1].content
    assert CEILING_CORRECTIVE not in stub.prompts[1][-1]["content"]


def test_gateway_failure_retry_shrinks_the_payload_and_asks_for_brevity():
    """Rule 7g (#88): a gateway-failure retry discards the attempt-1 message
    list, rebuilds the base from the raw conversation compacted at
    `SHAPE_TOOL_KEEP_RETRY` (500), and appends one user message: the mode
    prefix, the pinned ceiling corrective, `REPORT_CONTRACT`, then the
    rule-29a size note last."""
    wiki = "w" * 5000
    stub = _StubShapeModel([RuntimeError("Error code: 504 - upstream timed out"),
                            _report("full wiki")])
    result = asyncio.run(patrol.shape_report(
        stub, _conversation(), budget_cut=False, current_wiki=wiki))
    assert result.updated_wiki == "full wiki"
    assert len(stub.prompts) == 2
    first, retry = stub.prompts
    assert _marker(1500, 4000) in first[1].content   # attempt 1: rule 7e's cap
    # attempt 2: the base was rebuilt at the smaller cap — nothing of the
    # attempt-1 message list (its appended user message included) survives
    assert len(retry) == len(first) == 4             # 3 base + 1 appended
    assert retry[1].content.startswith("x" * 500)
    assert _marker(500, 4000) in retry[1].content
    assert "x" * 600 not in retry[1].content
    assert "FINAL SUMMARY: all quiet" in retry[2].content  # non-tool verbatim
    prompt = retry[-1]["content"]
    assert prompt.count(CEILING_CORRECTIVE) == 1
    assert "not a valid PatrolReport" not in prompt   # not the generic one
    # the order the rule pins: prefix, corrective, contract, note last
    assert prompt.index("You are the REPORTING PASS") < prompt.index(CEILING_CORRECTIVE)
    assert prompt.index(CEILING_CORRECTIVE) < prompt.index(patrol.REPORT_CONTRACT)
    note = patrol.wiki_size_note(wiki)
    assert note
    assert prompt.endswith("\n\n" + note)
    assert prompt.count("Current wiki size:") == 1


def test_gateway_failure_retry_applies_in_repair_and_wiki_repair_modes():
    """Rule 7g: every shape-pass invocation's internal retry is
    ceiling-aware — the validation-repair and wiki-repair invocations
    shrink their base too, and the wiki-repair mode still appends no size
    note (rule 29a)."""
    gateway = RuntimeError("Error code: 504 - upstream timed out")
    stub = _StubShapeModel([gateway, _report("fixed wiki")])
    result = asyncio.run(patrol.shape_report(
        stub, _conversation(), budget_cut=False, repair=("rejected", ["error one"])))
    assert result.updated_wiki == "fixed wiki"
    retry = stub.prompts[1]
    assert _marker(500, 4000) in retry[1].content
    assert CEILING_CORRECTIVE in retry[-1]["content"]
    assert patrol.REPORT_CONTRACT in retry[-1]["content"]
    # the mode's own framing is re-stated too — the rejected wiki rides verbatim
    assert "rejected" in retry[-1]["content"]

    stub = _StubShapeModel([gateway, _report("merged wiki")])
    result = asyncio.run(patrol.shape_report(
        stub, _conversation(), budget_cut=False, wiki_repair=("OLD WIKI", [], [])))
    assert result.updated_wiki == "merged wiki"
    retry = stub.prompts[1]
    assert _marker(500, 4000) in retry[1].content
    assert CEILING_CORRECTIVE in retry[-1]["content"]
    assert "OLD WIKI" in retry[-1]["content"]     # the wiki-repair ask survives
    assert "Current wiki size:" not in retry[-1]["content"]


def test_repair_and_wiki_repair_modes_are_compacted_too():
    """Rule 7e: compaction is uniform — every shape-pass mode (the rule-7a
    repair pass and the rule-7d wiki-repair pass) receives the same
    compacted conversation."""
    big = "x" * 4000
    conversation = _conversation()
    stub = _StubShapeModel([_report("fixed wiki")])
    result = asyncio.run(patrol.shape_report(
        stub, conversation, budget_cut=False, repair=("rejected", ["error one"])))
    assert result.updated_wiki == "fixed wiki"
    assert _marker(1500, 4000) in stub.prompts[0][1].content
    assert "x" * 1600 not in stub.prompts[0][1].content

    stub = _StubShapeModel([_report("merged wiki")])
    result = asyncio.run(patrol.shape_report(
        stub, conversation, budget_cut=False,
        wiki_repair=("OLD WIKI", [], [])))
    assert result.updated_wiki == "merged wiki"
    assert _marker(1500, 4000) in stub.prompts[0][1].content


# ------------------------------------------------ classify_exception matrix

def test_classify_exception_matrix():
    """Rule 7f: the pure classifier — an OpenAI-style 5xx status or a
    timeout marker becomes `gateway failure (...)`; anything else (4xx,
    plain errors) keeps the exception's own string."""
    classify = patrol.classify_exception
    # the #66 evidence, verbatim shape
    assert classify(RuntimeError("Error code: 504 - upstream timed out")) == \
        "gateway failure (Error code: 504 - upstream timed out)"
    assert classify(RuntimeError("Error code: 503")) == \
        "gateway failure (Error code: 503)"
    # timeout markers, case-insensitive
    assert classify(TimeoutError("Request timed out.")) == \
        "gateway failure (Request timed out.)"
    assert classify(RuntimeError("Connection timeout after 900s")) == \
        "gateway failure (Connection timeout after 900s)"
    # 4xx and plain errors keep their own string
    assert classify(RuntimeError("Error code: 429 - rate limited")) == \
        "Error code: 429 - rate limited"
    assert classify(ValueError("boom")) == "boom"


def test_failure_line_carries_elapsed_and_gateway_reason(capsys):
    """Rule 7f: the attempt-failure log line carries the attempt's elapsed
    seconds and the classified gateway reason — the ceiling is measurable
    from pod logs. Rule 7f's #88 amendment: it also carries the attempt's
    token counts, `?` on the exception leg (there is no raw response)."""
    stub = _StubShapeModel([RuntimeError("Error code: 504 - upstream timed out"),
                            RuntimeError("Error code: 504 - upstream timed out")])
    result = asyncio.run(patrol.shape_report(
        stub, [HumanMessage("mission")], budget_cut=False))
    assert result is None                        # nothing parsed, twice
    err = capsys.readouterr().err
    assert re.search(r"attempt 1 failed \(gateway failure "
                     r"\(Error code: 504 - upstream timed out\), took \d+ s, "
                     r"in \? out \? tokens\)", err)
    assert re.search(r"attempt 2 failed \(gateway failure "
                     r"\(Error code: 504 - upstream timed out\), took \d+ s, "
                     r"in \? out \? tokens\)", err)
