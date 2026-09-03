"""Telemetry setup, and the one span-redaction invariant.

`StrandsTelemetry()` hijacks the global tracer provider as a constructor side
effect — `strands/telemetry/config.py:114`, reached from `__init__` via
`_initialize_tracer`. On AgentCore Runtime that replaces a provider Runtime
already configured, so the setup must be conditional; the interesting assertion
is that it does *nothing* when Runtime is present.

**On proving these tests can fail.** Two of them target defects in this task's
first draft, and both drafts passed against the broken code:

- The Runtime-skip test asserted by raising `AssertionError` from a patched
  `StrandsTelemetry`. `setup_telemetry` wraps its construction in
  `except Exception`, and `AssertionError` *is* an `Exception` — so the raise was
  swallowed and logged, and the test passed with the guard deleted. Confirmed by
  running the draft body against a guardless copy: 1 passed. It now records
  construction in a list and asserts the list is empty, which no `except` can
  undo.
- The redaction test only distinguished "token present" from "token absent". A
  present token with a non-empty allowlist exports the whole household record and
  passed the draft check. Pinned below against the SDK's own `Tracer`, so the
  assertion rests on measured behaviour rather than on this module's reading of
  it.
"""

from __future__ import annotations

import pytest

from grace import observability


@pytest.fixture(autouse=True)
def _fresh_setup_state(monkeypatch):
    """Reset the once-per-process latch between tests.

    Via `monkeypatch` so it is restored afterwards: a bare assignment would leak
    a mutated module into whatever ran next, which is the sort of cross-test
    coupling that makes one test's pass depend on another's order.
    """
    monkeypatch.setattr(observability, "_configured", False)


def test_telemetry_setup_is_skipped_on_agentcore_runtime(monkeypatch):
    """`StrandsTelemetry.__init__` calls `set_tracer_provider` as a side
    effect, so *constructing* it is the damage — there is no later call to
    intercept. Runtime instruments the process itself, so doing it there
    replaces a working provider with a second one.
    `AGENT_OBSERVABILITY_ENABLED` is set by Runtime, so its presence means
    "hands off".

    **Asserted by recording construction, not by raising.** The draft version
    of this test patched the class to raise `AssertionError` — which
    `setup_telemetry`'s own `except Exception` swallows, since `AssertionError`
    is an `Exception`. Verified: that body passes against an implementation
    with the guard deleted. A sentinel list survives any `except`.
    """
    monkeypatch.setenv("AGENT_OBSERVABILITY_ENABLED", "true")
    constructed: list[str] = []

    def record(*args, **kwargs):
        constructed.append("StrandsTelemetry")
        raise AssertionError("setup_telemetry must not touch telemetry on Runtime")

    monkeypatch.setattr("strands.telemetry.StrandsTelemetry", record)
    observability.setup_telemetry()  # must return without raising

    assert constructed == [], (
        "setup_telemetry constructed StrandsTelemetry on Runtime, which "
        "replaces Runtime's own tracer provider"
    )


def test_telemetry_setup_runs_locally(monkeypatch):
    """Off Runtime, an exporter must actually be attached — exporters are
    opt-in, and without one traces are created and silently dropped."""
    monkeypatch.delenv("AGENT_OBSERVABILITY_ENABLED", raising=False)
    calls = []

    class FakeTelemetry:
        def setup_console_exporter(self):
            calls.append("console")
            return self

    monkeypatch.setattr("strands.telemetry.StrandsTelemetry", lambda: FakeTelemetry())
    observability.setup_telemetry()
    assert calls == ["console"]


def test_a_second_call_does_not_reconfigure(monkeypatch):
    """Task 4 calls `setup_telemetry()` once per invocation.

    Measured against the real SDK: a second `StrandsTelemetry()` logs
    "Overriding of current TracerProvider is not allowed", leaves the *first*
    provider global, and returns an orphan provider carrying a fresh console
    exporter attached to nothing. Nothing breaks, but every invocation after
    the first pays for a discarded provider and emits a log line that reads
    like a real misconfiguration.
    """
    monkeypatch.delenv("AGENT_OBSERVABILITY_ENABLED", raising=False)
    constructions = []

    class FakeTelemetry:
        def setup_console_exporter(self):
            return self

    def build():
        constructions.append("built")
        return FakeTelemetry()

    monkeypatch.setattr("strands.telemetry.StrandsTelemetry", build)
    observability.setup_telemetry()
    observability.setup_telemetry()
    observability.setup_telemetry()
    assert constructions == ["built"]


def test_a_failed_exporter_does_not_raise(monkeypatch):
    """The SDK's own stance is that failed exporter configuration is logged,
    not raised. Telemetry must never be the reason a sweep dies — losing
    traces is acceptable, losing the run is not. Same reasoning as Task 9's
    `_current_trace_id`: failing closed on an *observability* question harms
    the family, because nothing relies on a trace to decide anything.
    """
    monkeypatch.delenv("AGENT_OBSERVABILITY_ENABLED", raising=False)

    class BrokenTelemetry:
        def setup_console_exporter(self):
            raise RuntimeError("no exporter endpoint")

    monkeypatch.setattr("strands.telemetry.StrandsTelemetry", lambda: BrokenTelemetry())
    observability.setup_telemetry()  # must not raise


def test_a_failed_exporter_is_not_retried_every_invocation(monkeypatch):
    """A broken exporter stays broken. Retrying it per invocation buys
    nothing and logs a warning each time, which trains an operator to ignore
    the warning that would matter."""
    monkeypatch.delenv("AGENT_OBSERVABILITY_ENABLED", raising=False)
    attempts = []

    def build():
        attempts.append("attempt")
        raise RuntimeError("no exporter endpoint")

    monkeypatch.setattr("strands.telemetry.StrandsTelemetry", build)
    observability.setup_telemetry()
    observability.setup_telemetry()
    assert attempts == ["attempt"]


def test_the_redaction_token_keeps_its_trailing_equals():
    """Hard rule 8. The token's value lists what to leave *unredacted*, so an
    empty value means "redact everything" — and the trailing `=` is what makes
    it an empty value rather than an absent key. Absence of the token disables
    redaction entirely and exports the full household record to CloudWatch.
    """
    assert observability.REDACTION_TOKEN.endswith("gen_ai_unredacted_attributes=")


def test_grace_own_token_passes_its_own_check():
    """The token this repo ships must satisfy the guard that gates startup.
    Trivial-looking, and it is the one assertion that ties `REDACTION_TOKEN`
    to `redaction_is_configured` — they are separately editable."""
    assert observability.redaction_is_configured(
        {"OTEL_SEMCONV_STABILITY_OPT_IN": observability.REDACTION_TOKEN}
    )


def test_redaction_is_detected_as_configured_or_not():
    """A deployed runtime must be checkable, not assumed. `.env.example`
    having the token says nothing about what Runtime actually has set."""
    assert observability.redaction_is_configured(
        {"OTEL_SEMCONV_STABILITY_OPT_IN":
         "gen_ai_latest_experimental,gen_ai_unredacted_attributes="}
    )
    # The documented trap: enabling the experimental semconv alone does NOT
    # protect span content. Redaction needs the separate token.
    assert not observability.redaction_is_configured(
        {"OTEL_SEMCONV_STABILITY_OPT_IN": "gen_ai_latest_experimental"}
    )
    assert not observability.redaction_is_configured({})


def test_a_non_empty_allowlist_is_refused():
    """The defect a presence-only check cannot see, and the reason this
    function parses rather than substring-matches.

    `gen_ai_unredacted_attributes=<list>` exempts the named attributes from
    redaction. Allowlisting `gen_ai.input.messages;gen_ai.output.messages`
    exports every prompt and tool result — the full household record — while
    the token is unmistakably *present*. A `"gen_ai_unredacted_attributes=" in
    value` check passes it, so hard rule 8 would be defeated by the function
    written to enforce it and `runtime_app` would start.

    Grace's policy is redact-everything, so a non-empty allowlist fails.
    """
    assert not observability.redaction_is_configured(
        {"OTEL_SEMCONV_STABILITY_OPT_IN":
         "gen_ai_latest_experimental,"
         "gen_ai_unredacted_attributes=gen_ai.input.messages;gen_ai.output.messages"}
    )
    # A trailing-`*` glob is the SDK's wildcard form, and one attribute is
    # enough to leak.
    assert not observability.redaction_is_configured(
        {"OTEL_SEMCONV_STABILITY_OPT_IN": "gen_ai_unredacted_attributes=gen_ai.output.*"}
    )
    # Empty `;` entries are ignored by the SDK, so they still mean "redact
    # everything" and must still pass.
    assert observability.redaction_is_configured(
        {"OTEL_SEMCONV_STABILITY_OPT_IN": "gen_ai_unredacted_attributes=;; "}
    )


def test_the_check_agrees_with_the_sdks_own_parser(monkeypatch):
    """The assertion that keeps this module honest.

    `redaction_is_configured` re-implements a slice of the SDK's parsing, and a
    guard that disagrees with the thing it guards is worse than no guard. So
    this drives the real `strands.telemetry.Tracer` and compares what it
    actually does to span content against what this module claims.

    `Tracer` reads `os.environ` at construction, hence `monkeypatch.setenv`
    rather than a mapping argument. Both sensitive message attributes are
    checked, because a single-attribute allowlist protects one and leaks the
    other.
    """
    from strands.telemetry import Tracer

    cases = [
        ("gen_ai_latest_experimental", False),
        ("gen_ai_latest_experimental,gen_ai_unredacted_attributes=", True),
        ("gen_ai_unredacted_attributes=", True),
        ("gen_ai_unredacted_attributes=gen_ai.input.messages", False),
        ("gen_ai_unredacted_attributes=gen_ai.input.messages;gen_ai.output.messages", False),
        ("gen_ai_unredacted_attributes=gen_ai.output.*", False),
        # Space-separated is not a token list: the SDK splits on commas only,
        # so this leaves redaction OFF despite reading as if it were on. A
        # substring check calls it configured.
        ("gen_ai_latest_experimental gen_ai_unredacted_attributes=", False),
        # A different token that merely ends with the same characters.
        ("my_gen_ai_unredacted_attributes=", False),
    ]

    for value, expected in cases:
        monkeypatch.setenv("OTEL_SEMCONV_STABILITY_OPT_IN", value)
        tracer = Tracer()
        sdk_redacts_everything = tracer._redaction_enabled and not any(
            tracer._is_attribute_unredacted(name)
            for name in ("gen_ai.input.messages", "gen_ai.output.messages",
                         "gen_ai.system_instructions", "gen_ai.tool.call.arguments",
                         "gen_ai.tool.call.result")
        )
        assert sdk_redacts_everything is expected, (
            f"the SDK's own behaviour changed for {value!r}"
        )
        assert observability.redaction_is_configured() is expected, (
            f"redaction_is_configured disagrees with the SDK for {value!r}"
        )
