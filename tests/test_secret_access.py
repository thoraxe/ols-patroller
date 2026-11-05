"""Secret-access ban — #33.

Contract: .ai/spec/what/kubectl-guard.md rules 5-6b (safety-model.md rules 2,
6a), .ai/spec/what/patrol-engine.md rule 2a,
.ai/spec/what/reporting-notifications.md rule 5, ADR 0028.

Direct guard calls (dry mode unless stated) plus prompt/report-contract pins.
The ban is total: any command containing `secret` is refused, whatever the
verb, output format, or position — names/metadata included, not only value
reads — and local-file/credential flags are refused so kubectl cannot read a
Secret manifest or local credentials instead.
"""
import pytest

import patrol
from support import fake_cli


def guard(command, *, kubectl="oc", dry=True, verbose=False):
    """One guarded call in dry mode unless stated otherwise."""
    return patrol.kubectl_readonly(command, kubectl=kubectl, dry=dry, verbose=verbose)


REFUSAL = ("REFUSED: Secret access is off-limits — names and metadata included. "
           "Do not retry with another output format; inspect workload metadata "
           "(get pods, describe deployment, get events) instead.")

LOCAL_FILE_REFUSAL = (
    "REFUSED: local-file and credential flags are off-limits — kubectl must use "
    "ambient configuration and inline resource names, never read local files or "
    "take alternate credentials.")


SECRET_COMMANDS = [
    # names / metadata (allowed before #33)
    "get secrets",
    "get secret demo-secret",
    "get secret demo-secret -n demo-app",
    "get secrets -A",
    "get secret demo-secret -o name",
    "describe secret demo-secret",
    # value-bearing formats (the old check's four, plus the forms it missed)
    "get secret demo-secret -o yaml",
    "get secret demo-secret -o json",
    "get secret demo-secret -o jsonpath={.data.token}",
    "get secret demo-secret -o go-template={{.data}}",
    "get secret demo-secret -o custom-columns=NAME:.metadata.name,DATA:.data",
    "get secret demo-secret --template={{.data}}",
    "get secret demo-secret -o=json",
    "get secret demo-secret --output=yaml",
    "get secret demo-secret --output json",
    # case, resource-position, and API-group forms
    "get SECRET demo-secret -o yaml",
    "get -n demo-app secret demo-secret",
    "get secrets.v1.",
]


@pytest.mark.parametrize("command", SECRET_COMMANDS)
def test_every_secret_command_refused(command):
    """Rules 5-6: the ban is on the word, not on a format — every Secret form
    is refused with the exact message."""
    out = guard(command)
    assert out.startswith("REFUSED:"), f"{command!r} must be refused, got {out!r}"
    assert out == REFUSAL


def test_refusal_never_recommends_a_secret_read():
    """Rule 5: the old message steered to `get secrets`; the new one must not
    offer any Secret read as the recovery path."""
    out = guard("get secrets")
    assert "get secrets" not in out
    assert "do not retry" in out.lower()
    assert "workload metadata" in out.lower()


@pytest.mark.parametrize("command", [
    "get deployment my-secret-app",       # workload name contains the word
    "get pods -n secret-ops",             # namespace contains the word
    "get deployment demo -o "
    "jsonpath={.spec.template.spec.volumes[0].secret.secretName}",  # field name
])
def test_overbroad_substring_still_refused(command):
    """Rule 6: the match is deliberately over-broad — a false refusal costs a
    retry, a miss leaks a credential."""
    out = guard(command)
    assert out.startswith("REFUSED:")
    assert out == REFUSAL


@pytest.mark.parametrize("command", [
    "get pods -A",
    "get deployments -A",
    "get events -A --field-selector=type=Warning",
    "describe deployment demo-app",
    "describe node worker-1",
    "get configmap app-config -n demo-app -o yaml",
    # incidental Secret *names* in workload output are not Secret-resource reads
    "get deployment demo-app -o yaml",
])
def test_non_secret_reads_still_allowed(command):
    """Rules 5-6 scope: only commands containing `secret` are refused —
    ordinary reads, including non-Secret resources with any output format,
    remain guard-permitted (broader sensitive-data filtering is future scope)."""
    out = guard(command)
    assert not out.startswith("REFUSED:"), (
        f"{command!r} is not a Secret read, got {out!r}")


# ------------------------------------- local-file & credential flags (rule 6b)

FILE_CREDENTIAL_COMMANDS = [
    # file input: the `get -f <secret-manifest>` bypass and its spellings
    "get -f /proc/self/environ",
    "get -f=/tmp/manifest.yaml",
    "get -f/tmp/manifest.yaml",
    "get --filename /tmp/manifest.yaml",
    "get --filename=/tmp/manifest.yaml",
    "describe -f /tmp/manifest.yaml",
    "get pods -Af /tmp/manifest.yaml",          # combined shorthand
    "get pods -wf /tmp/manifest.yaml",
    "get pods -Afi /tmp/manifest.yaml",
    # alternate credentials / kubeconfig
    "get --kubeconfig /tmp/other-kubeconfig",
    "get --kubeconfig=/tmp/other-kubeconfig",
    "get --token=abc",
    "get pods --username admin --password hunter2",
    "get --client-key=/tmp/key --client-certificate=/tmp/cert",
    "get --certificate-authority=/tmp/ca",
    # output formats that read a local template file
    "get pods -o go-template-file=/proc/self/environ",
    "get pods --output=go-template-file=/tmp/tmpl",
    "get pods -o jsonpath-file=/tmp/tmpl",
    "get pods -o templatefile=/tmp/tmpl",
    "get pods -o custom-columns-file=/tmp/tmpl",
    # ... and the same values with `o` spelled inside a combined boolean
    # shorthand — the #79 bypass: the value token (or attached remainder) must
    # be judged, not only tokens whose first option letter is `o`
    "get pods -Ao go-template-file=/tmp/tmpl",
    "get pods -Ao=go-template-file=/proc/self/environ",
    "get pods -Aogo-template-file=/tmp/tmpl",
    "get pods -Aho jsonpath-file=/tmp/tmpl",
    "get pods -wo templatefile=/tmp/tmpl",
    "get pods -Awo custom-columns-file=/tmp/tmpl",
    "get pods -Awo=custom-columns-file=/tmp/tmpl",
    "get pods -Awo custom-columns-FILE=/tmp/tmpl",
    "logs -fo go-template-file=/tmp/tmpl",
    # #83: `p` is boolean `--previous` on `logs`, so the walk runs past it and
    # the `o` after it is still an output option letter
    "logs -po go-template-file=/tmp/tmpl",
    "logs -po=go-template-file=/tmp/tmpl",
    "logs -pogo-template-file=/tmp/tmpl",
    "logs -po jsonpath-file=/tmp/tmpl",
    "logs -pfo go-template-file=/tmp/tmpl",
    "logs -pfo=templatefile=/tmp/tmpl",
]


@pytest.mark.parametrize("command", FILE_CREDENTIAL_COMMANDS)
def test_local_file_and_credential_flags_refused(command):
    """Rule 6b: kubectl must not read local files or take alternate
    credentials — `get -f` would otherwise fetch a Secret without the word
    `secret` ever appearing in the command."""
    out = guard(command)
    assert out.startswith("REFUSED:"), f"{command!r} must be refused, got {out!r}"
    assert out == LOCAL_FILE_REFUSAL


@pytest.mark.parametrize("command", [
    "logs -f pod/demo-app-abc",                 # -f is --follow on logs
    "logs --follow pod/demo-app-abc",
    "get pods -o jsonpath={.items[*].metadata.name}",   # inline formatter
    "get pods -o go-template={{range .items}}{{.metadata.name}}{{end}}",
    "get pods -o custom-columns=NAME:.metadata.name",
    # `f` in an attached shorthand value is not an option letter
    "get pods -nfoo",
    "get pods -lapp=frontend",
    "get pods -o=jsonpath={.metadata.labels.foo}",
    "get pods -l app=frontend",
    # inline formatters in the combined-shorthand spellings of #79
    "get pods -Ao yaml",
    "get pods -Ao name",
    "get pods -Ao jsonpath={.items[*].metadata.name}",
    "get pods -Ao=custom-columns=NAME:.metadata.name",
    "get pods -Awo go-template={{range .items}}{{.metadata.name}}{{end}}",
    "get pods -Aojsonpath={.items[*].metadata.name}",
    "logs -fo jsonpath={.items[*].metadata.name}",
    # #83: `p` is `--previous` on `logs`, so these are boolean-letter
    # spellings, not file-input or template-file reads
    "logs -p pod/demo-app-abc",
    "logs -pf pod/demo-app-abc",
    "logs -fp pod/demo-app-abc",
    "logs -po jsonpath={.metadata.name}",
    "logs -pfo=jsonpath={.metadata.name}",
    # ... and `p` stays value-taking (`--patch`-style) outside `logs`
    "describe pod/demo-app-abc -p old",
])
def test_logs_follow_and_inline_formatters_allowed(command):
    """Rule 6b scope: `logs -f` is follow (not a file flag), inline
    formatters stay allowed, and `f` inside an attached shorthand value
    (`-nfoo`, `-lapp=frontend`) is not a file-input flag."""
    out = guard(command)
    assert not out.startswith("REFUSED:"), (
        f"{command!r} is a legal read, got {out!r}")


def test_combined_shorthand_output_value_refused():
    """Rule 6b (#79): `o` inside a combined boolean shorthand still names the
    output value, so a template-file value in the next argument or in the
    attached remainder is a local-file read exactly like `-o <value>`."""
    for command in (
        "get pods -Ao go-template-file=/tmp/tmpl",
        "get pods -Ao=go-template-file=/tmp/tmpl",
        "get pods -Aogo-template-file=/tmp/tmpl",
    ):
        out = guard(command)
        assert out == LOCAL_FILE_REFUSAL, f"{command!r} -> {out!r}"


def test_combined_shorthand_refusal_preempts_dry_fixtures(monkeypatch):
    """Rule 6b with rule 13: the #79 forms refuse before fixture lookup."""
    monkeypatch.setattr(patrol, "FIXTURES", [("-Ao", "SHOULD-NOT-SERVE")])
    out = guard("get pods -Ao go-template-file=/tmp/tmpl")
    assert out == LOCAL_FILE_REFUSAL
    assert "SHOULD-NOT-SERVE" not in out


def test_previous_shorthand_refusal_preempts_dry_fixtures(monkeypatch):
    """Rule 6b with rule 13 (#83): `logs -po` refuses before fixture lookup."""
    monkeypatch.setattr(patrol, "FIXTURES", [("-po", "SHOULD-NOT-SERVE")])
    out = guard("logs -po go-template-file=/tmp/tmpl")
    assert out == LOCAL_FILE_REFUSAL
    assert "SHOULD-NOT-SERVE" not in out


def test_local_file_refusal_preempts_execution(tmp_path):
    """Rule 6b: the refusal fires before any subprocess runs."""
    cli = fake_cli(tmp_path, "print('SHOULD-NOT-RUN')")
    out = guard("get -f /proc/self/environ", kubectl=cli, dry=False)
    assert out == LOCAL_FILE_REFUSAL
    assert "SHOULD-NOT-RUN" not in out


def test_local_file_refusal_preempts_dry_fixtures(monkeypatch):
    """Rule 6b with rule 13: the refusal fires before fixture lookup."""
    monkeypatch.setattr(patrol, "FIXTURES", [("-f", "SHOULD-NOT-SERVE")])
    out = guard("get -f /proc/self/environ")
    assert out == LOCAL_FILE_REFUSAL
    assert "SHOULD-NOT-SERVE" not in out


def test_file_flag_check_runs_after_the_verb_whitelist():
    """Rule 6b ordering: a mutation verb keeps its verb refusal."""
    out = guard("apply -f /tmp/manifest.yaml")
    assert out.startswith("REFUSED:")
    assert "not a read-only verb" in out


def test_secret_refusal_preempts_execution(tmp_path):
    """Rule 5: the ban fires before any subprocess runs — the CLI is never
    invoked for a Secret command."""
    cli = fake_cli(tmp_path, "print('SHOULD-NOT-RUN')")
    out = guard("get secret demo-secret", kubectl=cli, dry=False)
    assert out == REFUSAL
    assert "SHOULD-NOT-RUN" not in out


def test_secret_refusal_preempts_dry_fixtures(monkeypatch):
    """Rule 5 with rule 13: in dry mode the ban fires before fixture lookup —
    even a fixture that would match the command never answers it."""
    monkeypatch.setattr(patrol, "FIXTURES",
                        [("secret", "SHOULD-NOT-SERVE")])
    out = guard("get secret demo-secret")
    assert out == REFUSAL
    assert "SHOULD-NOT-SERVE" not in out


def test_secret_check_runs_after_the_verb_whitelist():
    """Rule 5 ordering: a mutation verb keeps its verb refusal, whose allowed-
    verb list is the recovery information the model needs there."""
    out = guard("apply secret demo-secret")
    assert out.startswith("REFUSED:")
    assert "not a read-only verb" in out


# ------------------------------------------------------------------ prompt teaching (rule 2a)

def test_prompt_teaches_the_secret_ban():
    """Rule 2a: the system prompt states the ban in full, so the model does not
    learn it by wasting turns on refusals."""
    flat = " ".join(patrol.SYSTEM_PROMPT.split())
    assert "Secrets are OFF-LIMITS in full" in flat
    assert "not even names or metadata" in flat
    assert "any command containing `secret`" in flat
    assert "workload metadata" in flat


def test_report_contract_teaches_the_recheck_limit():
    """reporting-notifications.md rule 5: the model-facing recheck guidance
    carries the full ban, not the old `no Secret-value reads` wording."""
    flat = " ".join(patrol.REPORT_CONTRACT.split())
    assert "A recheck must never read a Secret" in flat
    assert "not even names or metadata" in flat
