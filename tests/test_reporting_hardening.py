"""Reporting hardening — per-call-shape request bounds and the empty-wiki
classification (#49, #50 spec PR).

Two contracts, both born of the v0.2.1 milestone's real-cluster validation:

- #49 (reporting-notifications.md rule 8a): exploration turns and the
  structured reporting/repair calls carry different HTTP request bounds —
  `REQUEST_TIMEOUT` 300 s / `REPORT_TIMEOUT` 900 s. The single fat
  structured call carries the whole patrol conversation and legitimately
  runs minutes on reasoning-capable models (a 300 s bound timed out
  attempt 1 of a fresh-cluster first patrol in-cluster). Unit pins here:
  the constants, the `make_model` timeout passthrough, and — for #50 —
  the report contract's wiki-required instruction.
- #50 (reporting-notifications.md rule 7b): a parsed report whose
  `updated_wiki` is empty is a failed reporting attempt — one retry with
  a corrective message naming the empty wiki, then (still empty) the
  report returns to the caller, which delivers findings and warns on
  stderr. Driven here against `shape_report` directly with a stub
  structured model; the wiring-level e2e (warning, no write, findings
  preserved) lives in test_patrol_deepagents.py.

Run: uv run --group dev pytest tests/ -q
"""
import asyncio

import patrol


# ---------------------------------------------------------------- #49 pins

def test_request_bound_constants():
    """Rule 8a: the two call-shape bounds, as specced constants."""
    assert patrol.REQUEST_TIMEOUT == 300
    assert patrol.REPORT_TIMEOUT == 900


def test_make_model_passes_the_request_bound(monkeypatch):
    """Rule 8a: make_model is the parametrized seam — the bound it is
    given is the bound the ChatOpenAI client carries; the default is the
    exploration bound."""
    monkeypatch.setattr(patrol, "MODEL", "fake-model")
    monkeypatch.setattr(patrol, "BASE_URL", "http://fake.test/v1")
    monkeypatch.setattr(patrol, "API_KEY", "test-key")
    assert patrol.make_model(timeout=42).request_timeout == 42
    assert patrol.make_model().request_timeout == patrol.REQUEST_TIMEOUT


def test_report_contract_requires_the_wiki():
    """Rule 5 (#50): the report contract demands the full wiki on every
    report and states an empty updated_wiki is never a valid answer."""
    contract = patrol.REPORT_CONTRACT
    assert "updated_wiki is required on every report" in contract
    assert "copy unchanged sections verbatim" in contract
    assert "An empty updated_wiki is never a valid answer" in contract


# ------------------------------------------------- #50 shape-pass matrix

class _StubShapeModel:
    """A model stub for shape_report: with_structured_output returns a
    structured runnable whose ainvoke serves scripted results (an entry
    may be an exception, raised) and records every prompt it received."""

    def __init__(self, results):
        self.results = list(results)
        self.prompts = []

    def with_structured_output(self, schema, method=None):
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
    """Rule 7 (unchanged by #50): an exception or None result keeps the
    generic corrective message and the one-retry bound."""
    stub = _StubShapeModel([RuntimeError("endpoint hung"), _report("full wiki")])
    result = _shape(stub)
    assert result.updated_wiki == "full wiki"
    assert len(stub.prompts) == 2
    assert "not a valid PatrolReport" in stub.prompts[1][-1]["content"]
