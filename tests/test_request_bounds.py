"""Request bounds — per-call-shape model-client timeouts (#49 spec PR).

The v0.2.1 real-cluster validation caught the mismatch: one 300 s request
bound sat on the whole ChatOpenAI client, but the call shapes differ —
exploration turns are small and fast, while the single tool-free
structured reporting call carries the entire patrol conversation
(~100k tokens in, up to 16k out) and legitimately runs minutes on
reasoning-capable models (a fresh-cluster first patrol's reporting call
timed out at 300 s on attempt 1 in-cluster; attempt 2 succeeded). The
contract (reporting-notifications.md rule 8a): exploration turns keep
`REQUEST_TIMEOUT` (300 s), the structured reporting/repair calls get
`REPORT_TIMEOUT` (900 s). Unit pins here: the constants and the
`make_model` timeout passthrough; the two-model wiring is pinned by the
e2e in test_patrol_deepagents.py.

Run: uv run --group dev pytest tests/ -q
"""
import patrol


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
