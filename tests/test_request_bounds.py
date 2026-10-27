"""Request bounds — per-call-shape model-client timeouts and completion-token
budgets (#49 and #63 spec PRs).

The v0.2.1 real-cluster validation caught the mismatch twice, one layer at
a time: first #49 — one 300 s request bound sat on the whole ChatOpenAI
client, but the call shapes differ (exploration turns are small and fast;
the single tool-free structured reporting call carries the entire patrol
conversation and legitimately runs minutes on reasoning-capable models).
Then #63 — the same mismatch one layer down: one `max_tokens=16000` sat on
the whole client, but the shape pass must emit the entire wiki (~15 KB) in
one structured response, and on a reasoning model the reasoning tokens come
out of the same allowance — a live 43.5k-token report prompt spent all
16000 completion tokens reasoning (`finish_reason=length`, zero content,
twice per patrol), freezing the wiki for ~14 patrols; the same run at a
64k reporting budget completed at ~20k tokens and committed.

The contracts (reporting-notifications.md rules 8a + 8b): exploration turns
keep `REQUEST_TIMEOUT` (300 s) / `REQUEST_MAX_TOKENS` (16000), the
structured reporting/repair calls get `REPORT_TIMEOUT` (900 s) /
`REPORT_MAX_TOKENS` (64000). Unit pins here: the constants and the
`make_model` passthrough for both bounds; the two-model wiring is pinned by
the e2e in test_patrol_deepagents.py, and the empty-result reason
classification (rule 7c) in test_empty_report.py.

Run: uv run --group dev pytest tests/ -q
"""
import patrol


def test_request_bound_constants():
    """Rule 8a: the two call-shape timeout bounds, as specced constants."""
    assert patrol.REQUEST_TIMEOUT == 300
    assert patrol.REPORT_TIMEOUT == 900


def test_token_bound_constants():
    """Rule 8b (#63): the two call-shape completion-token bounds, as
    specced constants."""
    assert patrol.REQUEST_MAX_TOKENS == 16000
    assert patrol.REPORT_MAX_TOKENS == 64000


def test_make_model_passes_the_request_bound(monkeypatch):
    """Rule 8a: make_model is the parametrized seam — the bound it is
    given is the bound the ChatOpenAI client carries; the default is the
    exploration bound."""
    monkeypatch.setattr(patrol, "MODEL", "fake-model")
    monkeypatch.setattr(patrol, "BASE_URL", "http://fake.test/v1")
    monkeypatch.setattr(patrol, "API_KEY", "test-key")
    assert patrol.make_model(timeout=42).request_timeout == 42
    assert patrol.make_model().request_timeout == patrol.REQUEST_TIMEOUT


def test_make_model_passes_the_token_bound(monkeypatch):
    """Rule 8b (#63): max_tokens parametrizes the same seam — the bound it
    is given is the bound the ChatOpenAI client carries; the default is the
    exploration bound."""
    monkeypatch.setattr(patrol, "MODEL", "fake-model")
    monkeypatch.setattr(patrol, "BASE_URL", "http://fake.test/v1")
    monkeypatch.setattr(patrol, "API_KEY", "test-key")
    assert patrol.make_model(max_tokens=42).max_tokens == 42
    assert patrol.make_model().max_tokens == patrol.REQUEST_MAX_TOKENS
