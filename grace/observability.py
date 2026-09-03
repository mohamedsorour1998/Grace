"""Trace exporter setup, and the span-redaction invariant.

Two things here, both about knowing when not to act.

**Skip telemetry setup on AgentCore Runtime.** `StrandsTelemetry()` calls
`trace_api.set_tracer_provider(...)` as a constructor side effect — verified in
the installed SDK at `strands/telemetry/config.py:114`, inside
`_initialize_tracer`, which `__init__` calls whenever no `tracer_provider` is
passed. Runtime configures the OTEL environment and the global provider itself,
so constructing it there replaces a working provider with a second one.
`AGENT_OBSERVABILITY_ENABLED` is set by Runtime, so its presence means hands off.

**Exporters are opt-in.** Off Runtime, a provider with no exporter creates spans
and silently drops them, so `setup_console_exporter()` is what makes local traces
visible at all.

**Called at most once per process.** Task 4's entrypoint calls this per
invocation. Measured: a second `StrandsTelemetry()` logs
`Overriding of current TracerProvider is not allowed`, leaves the *first*
provider global, and hands back an orphan provider with a fresh console exporter
attached to nothing. Harmless but pure waste, and the log line reads like a real
misconfiguration. `_configured` makes the second call a no-op.

Failure here is swallowed, matching the SDK's own stance that failed exporter
configuration is logged rather than raised — and matching Task 9's reasoning
about `_current_trace_id`: failing closed on an *observability* question harms the
family, because nothing relies on a trace to decide anything. Lose the traces;
keep the sweep. Note this is the opposite posture from `infra/`, where a
provisioning script raises: there, a loud failure blocks a deploy and the
operator re-runs.
"""

from __future__ import annotations

import logging
import os
from typing import Mapping

logger = logging.getLogger(__name__)

# Hard rule 8. Set this verbatim in any environment that runs Grace.
#
# The value after the `=` lists what to leave **unredacted**, so the *empty*
# value is what means "redact everything sensitive". The trailing `=` is
# load-bearing because it is what makes the value empty rather than the key
# absent — and absence disables redaction outright, exporting every prompt and
# tool result (the full household record) to CloudWatch verbatim.
REDACTION_TOKEN = "gen_ai_latest_experimental,gen_ai_unredacted_attributes="

_ENV_KEY = "OTEL_SEMCONV_STABILITY_OPT_IN"
_UNREDACTED_PREFIX = "gen_ai_unredacted_attributes="

# The SDK splits an allowlist value on `;` (see `Tracer._compile_unredacted_
# patterns`). Parsed the same way here so the two cannot disagree about what a
# given value means.
_ALLOWLIST_SEPARATOR = ";"

# Set once `setup_telemetry` has done its work — including when it deliberately
# did nothing on Runtime, since "already handled" is the same answer either way.
_configured = False


def redaction_is_configured(env: Mapping[str, str] | None = None) -> bool:
    """Whether span content is actually redacted in this environment.

    Checkable rather than assumed, because `.env.example` carrying the token says
    nothing about what a deployed Runtime has set.

    **This asks whether content is redacted, not whether the token is present —
    and those are different claims.** Measured against the SDK's own `Tracer`,
    three configurations behave three ways:

    | `OTEL_SEMCONV_STABILITY_OPT_IN`                       | input/output messages |
    |---|---|
    | (token absent)                                        | exported verbatim |
    | `...,gen_ai_unredacted_attributes=`                   | `[REDACTED]` |
    | `...,gen_ai_unredacted_attributes=gen_ai.input.messages;gen_ai.output.messages` | exported verbatim |

    A presence-only check — `_UNREDACTED_PREFIX in value` — returns True for that
    third row, which exports the entire household record while passing the guard.
    That is hard rule 8 defeated by the function written to enforce it, and
    `runtime_app` would start happily. So the value is parsed and required to be
    **empty**: Grace's policy is redact-everything, therefore any non-empty
    allowlist fails this check rather than passing it.

    Parsed the way the SDK parses it — comma-separated tokens, the
    `gen_ai_unredacted_attributes=` prefix, the remainder split on `;` with empty
    entries ignored — rather than by matching strings independently, because two
    parsers that disagree about a value are how the guard drifts away from the
    behaviour it guards.

    Also guards the documented trap: enabling `gen_ai_latest_experimental` alone
    does **not** protect span content. Redaction needs the separate token.
    """
    value = (env if env is not None else os.environ).get(_ENV_KEY, "")

    # The SDK reads tokens as a comma-separated list and strips each one, so a
    # space-separated variant is not a token at all and a `my_gen_ai_unredacted_
    # attributes=` prefix on some other token does not count. Substring matching
    # gets both of those wrong in the unsafe direction.
    tokens = [token.strip() for token in value.split(",")]

    found = False
    for token in tokens:
        if not token.startswith(_UNREDACTED_PREFIX):
            continue
        found = True
        allowlist = token.partition("=")[2]
        # A non-empty allowlist exempts named attributes from redaction. Grace
        # allowlists nothing, so anything here fails the check.
        #
        # (A pathological value repeating the token twice is ambiguous to the
        # SDK itself — it picks one arbitrarily out of a `set` — so refusing
        # when *any* copy carries an allowlist is the only safe reading.)
        if any(entry.strip() for entry in allowlist.split(_ALLOWLIST_SEPARATOR)):
            return False

    return found


def setup_telemetry() -> None:
    """Attach a trace exporter for local runs only. Safe to call repeatedly."""
    global _configured

    if _configured:
        return

    if os.getenv("AGENT_OBSERVABILITY_ENABLED"):
        # Runtime already did this. Constructing StrandsTelemetry here would
        # replace its provider — `__init__` calls `set_tracer_provider` as a
        # side effect, so merely constructing the object is the damage.
        _configured = True
        return

    try:
        from strands.telemetry import StrandsTelemetry

        StrandsTelemetry().setup_console_exporter()
    except Exception:  # noqa: BLE001 — telemetry must not break the run
        logger.warning("trace exporter setup failed; continuing without traces",
                       exc_info=True)

    # Set even on failure: retrying a broken exporter on every invocation buys
    # nothing and logs a warning each time.
    _configured = True
