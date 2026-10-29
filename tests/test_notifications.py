"""Notifications — contract tests for #9 (apprise multi-sink delivery).

These tests pin the notification delivery contract: the `PATROL_NOTIFY_URLS`
sink configuration (rule 10a), the patrol-level severity → notify_type
mapping (rule 10b), and the per-sink best-effort delivery semantics
(rule 10c). They run against fake apprise objects — no network, no real
sinks.

Contract: .ai/spec/what/reporting-notifications.md rules 10, 10a-10c
[PLANNED: #9].

Run: uv run --group dev pytest tests/ -q
"""
import sys

import pytest

import patrol


# ------------------------------------------------------------------ helpers

class FakeApprise:
    """Stands in for apprise.Apprise: records added URLs and sent messages.

    Failure modes are class attributes so a test can arm them *before*
    patrol.notify() constructs the instance: `fail_add` is a set of URLs
    whose add() must fail (apprise refusing to parse the URL);
    `notify_fails`/`notify_raises` select the delivery failure mode.
    Instances are tracked on the class so tests can assert on construction
    counts.
    """

    instances: list["FakeApprise"] = []
    fail_add: set[str] = set()
    notify_fails = False
    notify_raises = False

    def __init__(self):
        self.added: list[str] = []
        self.sent: list[tuple[str, str, str]] = []  # (title, body, notify_type)
        FakeApprise.instances.append(self)

    def add(self, url: str) -> bool:
        if url in self.fail_add:
            return False
        self.added.append(url)
        return True

    def notify(self, *, title: str, body: str, notify_type: str) -> bool:
        if self.notify_raises:
            raise RuntimeError("sink down")
        if self.notify_fails:
            return False
        self.sent.append((title, body, notify_type))
        return True


@pytest.fixture
def fake_apprise(monkeypatch):
    """Point patrol's apprise seam at the fake and reset state."""
    FakeApprise.instances = []
    FakeApprise.fail_add = set()
    FakeApprise.notify_fails = False
    FakeApprise.notify_raises = False

    class _FakeModule:
        Apprise = FakeApprise

    monkeypatch.setattr(patrol, "apprise", _FakeModule(), raising=False)
    return FakeApprise


def _urls(monkeypatch, urls):
    monkeypatch.setattr(patrol, "NOTIFY_URLS", urls, raising=False)


# ------------------------------------------------------------------ rule 10a: config

def test_parse_notify_urls_matrix():
    """Pure parse: comma/newline separated, trimmed, blanks dropped."""
    assert patrol.parse_notify_urls("") == []
    assert patrol.parse_notify_urls("   ") == []
    assert patrol.parse_notify_urls(" , ,\n ,") == []
    assert patrol.parse_notify_urls("slack://token") == ["slack://token"]
    assert patrol.parse_notify_urls("slack://a,ntfys://b") == ["slack://a", "ntfys://b"]
    assert patrol.parse_notify_urls("slack://a\nntfys://b") == ["slack://a", "ntfys://b"]
    assert patrol.parse_notify_urls(" slack://a ,\n ntfys://b \n") == [
        "slack://a", "ntfys://b"]


# ------------------------------------------------------------------ rule 10b: severity

def test_report_notify_type_matrix():
    """Pure map: any crit finding -> FAILURE, else WARNING."""
    assert patrol.report_notify_type([]) == "WARNING"
    assert patrol.report_notify_type([{"severity": "info"}]) == "WARNING"
    assert patrol.report_notify_type([{"severity": "warn"}]) == "WARNING"
    assert patrol.report_notify_type(
        [{"severity": "info"}, {"severity": "warn"}]) == "WARNING"
    assert patrol.report_notify_type([{"severity": "crit"}]) == "FAILURE"
    assert patrol.report_notify_type(
        [{"severity": "info"}, {"severity": "crit"}, {"severity": "warn"}]) == "FAILURE"


# ------------------------------------------------------------------ rule 10c: delivery

def test_notify_console_line_unconditional(capsys, monkeypatch, fake_apprise):
    """The 🔔 NOTIFY console line prints with zero sinks (rule 9a surface)."""
    _urls(monkeypatch, [])
    patrol.notify(["something looks wrong"])
    out = capsys.readouterr().out
    assert "🔔 NOTIFY: something looks wrong" in out
    assert FakeApprise.instances == [], "zero sinks must construct no apprise"


def test_notify_apprise_delivery(capsys, monkeypatch, fake_apprise):
    """One apprise instance per invocation, one notify per message, mapped type."""
    _urls(monkeypatch, ["slack://a", "ntfys://b"])
    patrol.notify(["m1", "m2"], notify_type="FAILURE")
    assert len(FakeApprise.instances) == 1, (
        "one apprise instance per notify() invocation (rule 10c)"
    )
    inst = FakeApprise.instances[0]
    assert inst.added == ["slack://a", "ntfys://b"]
    assert inst.sent == [
        ("Cluster patrol", "m1", "FAILURE"),
        ("Cluster patrol", "m2", "FAILURE"),
    ], ("one apprise notify per message, title 'Cluster patrol', "
        "body the message verbatim, the passed notify_type (rule 10c)")
    assert "🔔 NOTIFY: m1" in capsys.readouterr().out


def test_notify_default_notify_type_is_warning(monkeypatch, fake_apprise):
    _urls(monkeypatch, ["slack://a"])
    patrol.notify(["m"])
    assert FakeApprise.instances[0].sent == [("Cluster patrol", "m", "WARNING")]


def test_notify_bad_url_skipped_and_logged(monkeypatch, fake_apprise, capsys):
    """A URL apprise refuses to load is skipped with a stderr line; the
    other sinks still deliver every message."""
    _urls(monkeypatch, ["slack://bad", "ntfys://good"])
    FakeApprise.fail_add = {"slack://bad"}
    patrol.notify(["m1", "m2"])
    inst = FakeApprise.instances[0]
    assert inst.added == ["ntfys://good"]
    assert inst.sent == [("Cluster patrol", "m1", "WARNING"),
                         ("Cluster patrol", "m2", "WARNING")]
    err = capsys.readouterr().err
    assert "slack://bad" in err, "the refused URL must be named on stderr"


def test_notify_failure_never_fatal(monkeypatch, fake_apprise, capsys):
    """Sink delivery failure (False return or exception) is logged to stderr
    and never raises."""
    _urls(monkeypatch, ["slack://a"])
    FakeApprise.notify_fails = True
    patrol.notify(["m1"])  # must not raise
    FakeApprise.notify_fails = False
    FakeApprise.notify_raises = True
    patrol.notify(["m2"])  # must not raise
    err = capsys.readouterr().err
    assert err, "delivery failures must be visible on stderr"


def test_notify_empty_messages_no_apprise_calls(monkeypatch, fake_apprise):
    _urls(monkeypatch, ["slack://a"])
    patrol.notify([])
    assert FakeApprise.instances[0].sent == []


# ------------------------------------------------------------------ env wiring

def test_notify_urls_read_from_env_at_import():
    """NOTIFY_URLS is module config derived from PATROL_NOTIFY_URLS via the
    pure parser (rule 10a) — the same env-derived-config convention as the
    other PATROL_* vars. Asserted indirectly: the module must expose the
    parsed list, and the parser must be the pure seam used to build it."""
    assert hasattr(patrol, "NOTIFY_URLS")
    assert isinstance(patrol.NOTIFY_URLS, list)
