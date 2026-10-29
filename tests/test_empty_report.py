"""Empty PatrolReport — the wiki-required contract and its classification
(#51 spec PR).

Real-cluster validation caught the failure twice: a local patrol
delivered 8 findings but omitted `updated_wiki` (the field's lenient
default parsed it to ""); an in-cluster patrol returned a valid
PatrolReport with zero findings and an empty wiki after a 59-tool-call
exploration — 508k input tokens, no write, no commit, no output line.
The process could not distinguish "the model chose not to update
anything" from "the model failed to emit what it meant to".

The contract (reporting-notifications.md rules 5 + 7b, ADR 0019): the
report contract requires `updated_wiki` on every report ("nothing
changed" is expressed by returning the wiki unchanged, never by
omitting it); a parsed result with an empty `updated_wiki` is a failed
reporting attempt — one retry with a corrective message naming the
empty wiki; a still-empty retry delivers its findings, writes nothing,
and warns on stderr. The pydantic field keeps its lenient default (the
parser never enforces — findings survive a wiki-omitting parse).

Driven here against `shape_report` directly with a stub structured
model; the wiring-level e2e (warning, no write, findings preserved)
lives in test_patrol_deepagents.py. The empty-result reason
classification (#63, rule 7c) is also driven here: the pure classifier
matrix over fake raw responses, and the shape pass's attempt-failure
log lines carrying the classified reason.

Run: uv run --group dev pytest tests/ -q
"""
import asyncio
import re

import patrol
from langchain_core.messages import AIMessage


# ------------------------------------------------------ contract pins

def test_report_contract_requires_the_wiki():
    """Rule 5 (#51): the report contract demands the full wiki on every
    report and states an empty updated_wiki is never a valid answer."""
    contract = patrol.REPORT_CONTRACT
    assert "updated_wiki is required on every report" in contract
    assert "copy unchanged sections verbatim" in contract
    assert "An empty updated_wiki is never a valid answer" in contract


# ------------------------------------------------- shape-pass matrix

class _StubShapeModel:
    """A model stub for shape_report: with_structured_output returns a
    structured runnable whose ainvoke serves scripted results (an entry
    may be an exception, raised) and records every prompt it received.
    It accepts (and records) the include_raw kwarg of #63 — the pass's
    observability seam. A served dict with a "parsed" key is an
    include_raw envelope; anything else is a directly-parsed result."""

    def __init__(self, results):
        self.results = list(results)
        self.prompts = []
        self.include_raw = []

    def with_structured_output(self, schema, method=None, include_raw=None):
        stub = self
        stub.include_raw.append(include_raw)

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


def _shape(stub):
    return asyncio.run(patrol.shape_report(
        stub, [{"role": "user", "content": "patrol transcript"}], budget_cut=False))


def test_empty_wiki_result_is_retried_with_named_corrective():
    """Rule 7b: an empty-updated_wiki result is a failed attempt — one
    retry whose corrective message names the empty wiki; a good retry
    recovers."""
    stub = _StubShapeModel([_report(""), _report("full wiki")])
    result = _shape(stub)
    assert result.updated_wiki == "full wiki"
    assert len(stub.prompts) == 2
    corrective = stub.prompts[1][-1]["content"]
    assert "updated_wiki" in corrective
    assert "empty" in corrective.lower()
    assert "not a valid PatrolReport" not in corrective  # distinct from rule 7's


def test_double_empty_wiki_returns_the_report():
    """Rule 7b: a still-empty retry is not discarded — the report (its
    findings) returns to the caller, which warns and writes nothing."""
    first, second = _report(""), _report("")
    stub = _StubShapeModel([first, second])
    result = _shape(stub)
    assert result is second
    assert len(stub.prompts) == 2


def test_repair_pass_empty_wiki_is_retried_too():
    """Rule 7b's repair clause: the repair pass shares the empty-wiki
    classification — a repaired report with an empty wiki is retried the
    same way (named corrective) before the keep-old-wiki outcome applies."""
    stub = _StubShapeModel([_report(""), _report("fixed wiki")])
    result = asyncio.run(patrol.shape_report(
        stub, [{"role": "user", "content": "patrol transcript"}],
        budget_cut=False, repair=("rejected wiki", ["error one"])))
    assert result.updated_wiki == "fixed wiki"
    assert len(stub.prompts) == 2
    corrective = stub.prompts[1][-1]["content"]
    assert "updated_wiki" in corrective
    assert "empty" in corrective.lower()


def test_empty_wiki_then_exception_returns_the_parsed_report():
    """Rule 7b's composition rule: the pass returns the last result that
    parsed — an empty-wiki attempt followed by an exception still
    delivers the parsed report's findings (None only when nothing
    parsed)."""
    parsed = _report("")
    stub = _StubShapeModel([parsed, RuntimeError("endpoint hung")])
    result = _shape(stub)
    assert result is parsed
    assert len(stub.prompts) == 2


def test_zero_findings_with_a_wiki_never_retries():
    """Rule 7b's keying guard: the classification keys on the wiki field
    alone — a zero-findings report with a good wiki is a legal outcome,
    served in one shape request, no retry."""
    good = _report("full wiki")
    stub = _StubShapeModel([good])
    result = _shape(stub)
    assert result is good
    assert len(stub.prompts) == 1


def test_parse_failure_keeps_the_generic_retry_message():
    """Rule 7 (unchanged by #51): an exception or None result keeps the
    generic corrective message and the one-retry bound."""
    stub = _StubShapeModel([RuntimeError("endpoint hung"), _report("full wiki")])
    result = _shape(stub)
    assert result.updated_wiki == "full wiki"
    assert len(stub.prompts) == 2
    assert "not a valid PatrolReport" in stub.prompts[1][-1]["content"]


# --------------------------------------- empty-result reasons (#63, rule 7c)

def _raw(finish_reason, output_tokens):
    """A fake raw model response in langchain's message shape: the finish
    reason lives in response_metadata, the completion-token count (reasoning
    included) in usage_metadata's output_tokens."""
    return AIMessage(
        content="",
        response_metadata={"finish_reason": finish_reason},
        usage_metadata={"input_tokens": 43_500, "output_tokens": output_tokens,
                        "total_tokens": 43_500 + output_tokens},
    )


def _envelope(raw, parsed=None, parsing_error=None):
    """An include_raw structured-output envelope: the pass observes the raw
    response alongside the (missing) parse."""
    return {"raw": raw, "parsed": parsed, "parsing_error": parsing_error}


def test_classify_empty_result_matrix():
    """Rule 7c: the pure classifier over the raw response — truncated at
    max_tokens (finish_reason 'length', or the completion-token count
    reached the bound) vs no tool call (everything else)."""
    classify = patrol.classify_empty_result
    # The #63 root cause, verbatim shape: all tokens spent reasoning.
    assert classify(_raw("length", 16_000)) == \
        "empty result (truncated at max_tokens)"
    # Tokens at the bound even with a non-length finish reason (the
    # bound in force is the parameter, here the exploration one).
    assert classify(_raw("stop", 16_000), max_tokens=16_000) == \
        "empty result (truncated at max_tokens)"
    # Under the bound, finished, no tool call emitted.
    assert classify(_raw("stop", 200)) == "empty result (no tool call)"
    # The bound is a parameter — the reporting bound is the default.
    assert classify(_raw("stop", 64_000)) == \
        "empty result (truncated at max_tokens)"
    assert classify(_raw("stop", 64_000), max_tokens=128_000) == \
        "empty result (no tool call)"
    # Missing/garbled metadata defaults to no-tool-call, never crashes.
    assert classify(AIMessage(content="")) == "empty result (no tool call)"


def test_shape_pass_classifies_the_empty_result_reason(capsys):
    """Rule 7c: a None parse invoked with include_raw=True — the
    attempt-failure log line names the cause, on every attempt, and the
    pass still returns None only when nothing parsed."""
    stub = _StubShapeModel([_envelope(_raw("length", 64_000)),
                            _envelope(_raw("stop", 100))])
    result = _shape(stub)
    assert result is None                      # nothing parsed, twice
    assert len(stub.prompts) == 2              # the one-retry bound holds
    assert stub.include_raw == [True, True]    # invoked with the raw envelope
    err = capsys.readouterr().err
    # #66 (rule 7f) flipped the pin: the line now carries the elapsed suffix
    assert re.search(r"attempt 1 failed \(empty result \(truncated at max_tokens\)"
                     r", took \d+ s\)", err)
    assert re.search(r"attempt 2 failed \(empty result \(no tool call\)"
                     r", took \d+ s\)", err)


def test_shape_pass_parsing_error_keeps_its_message(capsys):
    """Rule 7c: a parsing error reported in the envelope (rather than
    raised) is treated as the exception it replaces — its message is the
    reason, not a classified guess."""
    stub = _StubShapeModel([
        _envelope(_raw("stop", 100), parsing_error=ValueError("bad tool args"),
                  ),
        _report("full wiki")])
    result = _shape(stub)
    assert result.updated_wiki == "full wiki"
    err = capsys.readouterr().err
    assert re.search(r"attempt 1 failed \(bad tool args, took \d+ s\)", err)


# ------------------------------------------- wiki-repair mode (rule 7d, #60)

def test_wiki_repair_mode_carries_old_wiki_and_findings():
    """Rule 7d (#60): the wiki-repair prompt states the reporting pass
    could not emit the wiki, carries the old wiki verbatim and the
    observed findings/notifications, and asks for a minimal merge."""
    stub = _StubShapeModel([_report("merged wiki")])
    result = asyncio.run(patrol.shape_report(
        stub, [{"role": "user", "content": "patrol transcript"}], budget_cut=False,
        wiki_repair=("OLD WIKI BODY", ["finding one"], ["notify one"])))
    assert result.updated_wiki == "merged wiki"
    assert len(stub.prompts) == 1
    prompt = stub.prompts[0][-1]["content"]
    assert "OLD WIKI BODY" in prompt, "the old wiki must ride verbatim (rule 7d)"
    assert "finding one" in prompt, "the observed findings must ride (rule 7d)"
    assert "notify one" in prompt, "the observed notifications must ride (rule 7d)"
    assert "minimal" in prompt.lower(), "the ask must be a minimal rewrite (rule 7d)"


def test_wiki_repair_mode_empty_wiki_is_retried():
    """Rule 7d: the mode shares the empty-wiki classification — one named
    corrective retry before the caller's fallback applies."""
    stub = _StubShapeModel([_report(""), _report("merged wiki")])
    result = asyncio.run(patrol.shape_report(
        stub, [{"role": "user", "content": "patrol transcript"}], budget_cut=False,
        wiki_repair=("OLD WIKI BODY", [], [])))
    assert result.updated_wiki == "merged wiki"
    assert len(stub.prompts) == 2
    corrective = stub.prompts[1][-1]["content"]
    assert "updated_wiki" in corrective
    assert "empty" in corrective.lower()
