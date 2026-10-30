"""Compacted shape-pass input + visible failure timing (#66 spec PR).

Real-cluster soak evidence (v0.2.3, ols-demo-2): the shape pass's input —
the full raw patrol conversation plus the wiki plus the contract — grew
with the wiki until the pass legitimately flirted with the model
gateway's ~900 s request ceiling: one patrol's both attempts 504ed at
~902 s, the next patrol's first attempt too, and `report is None` lost
everything (findings, notifications, wiki write) with only timestamps to
infer the ceiling from. Raising REPORT_TIMEOUT (#49) cannot help while
the *gateway* caps the request.

The contract (reporting-notifications.md rules 6, 7, 7e, 7f;
wiki-memory.md rule 33; ADR 0024):
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

The pure-function matrices and the shape-pass prompt compaction are
driven here; the total-failure tail wiring (notification + minimal
write) is driven in test_patrol_deepagents.py, which also carries the
flipped pins of the old attempt-failure log-line format.

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


def test_shape_pass_prompt_is_compacted():
    """Rule 7e: the reporting pass's prompt carries the compacted
    conversation — the tool body truncated with its marker, the final
    summary verbatim — and the corrective retry message is appended after
    compaction, untruncated."""
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
    from pod logs."""
    stub = _StubShapeModel([RuntimeError("Error code: 504 - upstream timed out"),
                            RuntimeError("Error code: 504 - upstream timed out")])
    result = asyncio.run(patrol.shape_report(
        stub, [HumanMessage("mission")], budget_cut=False))
    assert result is None                        # nothing parsed, twice
    err = capsys.readouterr().err
    assert re.search(r"attempt 1 failed \(gateway failure "
                     r"\(Error code: 504 - upstream timed out\), took \d+ s\)", err)
    assert re.search(r"attempt 2 failed \(gateway failure "
                     r"\(Error code: 504 - upstream timed out\), took \d+ s\)", err)
