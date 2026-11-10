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
64k reporting budget completed at ~20k tokens and committed. Then #88 —
the 64k bound itself outran the model gateway's ~900 s wall clock (both
attempts 504ing at ~902 s, ≈71 output tok/s), so the reporting bound
halves to 32k: ~450 s of generation, still ~12k above the observed 20k
completion, so #63's reasoning-exhaustion fix stays intact. Then #97 — 3 of
5 post-#88 patrols STILL 504ed at ~902 s on the first attempt while the
retry (500-char tool results + the brevity ask) finished in ~260 s every
time, so the cheap shape becomes the default and the bound drops again to
24000: ~600–685 s at the ~35–40 output tok/s that retry implies, still
~4k above the observed 20k completion (the floor is "above 20k", not
"12k above"). Then #101 — the post-#97 first attempt no longer hits the
clock but the token bound: `out 24000 tokens, bound 24000 tokens` at
289 s with an empty wiki, while the rule-7b retry produced a valid report
in ~230 s on the same 24000. So the retune is 24000 → 28000: +4000 tokens
of reasoning-variance headroom over the bound that was actually exhausted,
and ~700–800 s at the slow ~35–40 tok/s rate — inside the gateway window,
where 32000 (~800–914 s at that rate) would race it again.

The contracts (reporting-notifications.md rules 8a + 8b): exploration turns
keep `REQUEST_TIMEOUT` (300 s) / `REQUEST_MAX_TOKENS` (16000), the
structured reporting/repair calls get `REPORT_TIMEOUT` (900 s) /
`REPORT_MAX_TOKENS` (28000 since #101; 24000 from #97, 32000 from #88).
Unit pins here: the
constants, the two inequality guards the retune's ADR 0036 rests on
(headroom over the bound the v0.3.2 soak exhausted, and the gateway window
at the slowest measured throughput), and the
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
    """Rule 8b (#63, flipped by #88, #97 and again by #101): the two
    call-shape completion-token bounds, as specced constants — the reporting
    bound is 28k so a generation finishes inside the gateway's ~900 s window
    at the slowest measured throughput while keeping headroom over the 24k
    bound the v0.3.2 soak exhausted."""
    assert patrol.REQUEST_MAX_TOKENS == 16000
    assert patrol.REPORT_MAX_TOKENS == 28000


def test_reporting_bound_headroom_and_gateway_window():
    """Rule 8b's #101 retune rests on two inequalities, not on the literal
    alone (ADR 0036): the reporting bound must sit ABOVE the bound the #101
    soak actually exhausted (24000 — a valid report fits under it, so the
    overshoot was reasoning variance and the retune buys headroom over it)
    and BELOW the gateway's ~900 s wall clock at the slowest output rate
    measured on this payload shape (~35–40 output tok/s from the #97 retry
    — 32000 would be ~800–914 s there, the #88/#97 race returning)."""
    exhausted_bound = 24_000          # #101 attempt 1: out 24000, bound 24000
    reasoning_variance_headroom = 4_000
    gateway_window_seconds = 900
    slowest_observed_output_tok_s = 35

    # Above the truncation point, plus headroom.
    assert patrol.REPORT_MAX_TOKENS >= exhausted_bound + reasoning_variance_headroom
    # Inside the window at the slow rate: the more expensive failure is the
    # 504 (it burns the window AND the retry), not the truncation.
    assert patrol.REPORT_MAX_TOKENS <= (
        gateway_window_seconds * slowest_observed_output_tok_s)


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
