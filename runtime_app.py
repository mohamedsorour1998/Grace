"""AgentCore Runtime entrypoint for Grace.

Deliberately thin: everything of substance is in `grace.entrypoint`, which is
unit-tested offline. This module adapts a Runtime invocation into that call and
guarantees two things about the deployed process.

**It never raises.** An unhandled exception inside Runtime surfaces as an opaque
500. Step Functions can branch on `{"status": "error"}`; it cannot branch on a
stack trace it never receives.

**It refuses to serve without span redaction.** Hard rule 8: absence of the
`gen_ai_unredacted_attributes=` token disables redaction entirely, and a
*non-empty* value carves holes in it — both export the full household record to
CloudWatch, and `redaction_is_configured` checks for emptiness rather than
presence for exactly that reason. This is one of the few places where failing
closed means refusing to start, because the alternative is a silent, continuous
PII leak that nothing downstream would catch. Note this posture is the opposite
of `grace/observability.py`'s and `grace/ledger.py`'s, which deliberately fail
*open* on telemetry: those decide nothing, whereas this one decides whether a
household's record is safe to export at all.

The check runs at import, before `BedrockAgentCoreApp` exists, so the container
dies at start rather than serving one leaking invocation first.

The entrypoint is a plain `def` returning a dict, not the async generator the
CLI's template scaffolds. The template streams because it is a chat agent; a
sweep is a batch job with one answer per case. `BedrockAgentCoreApp` supports
both, and its docstring is explicit that payloads arrive **unchanged** — so this
validates the payload shape before forwarding.
"""

from __future__ import annotations

import logging

from bedrock_agentcore.runtime import BedrockAgentCoreApp

from grace.entrypoint import process_case
from grace.observability import REDACTION_TOKEN, redaction_is_configured, setup_telemetry

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

if not redaction_is_configured():
    # Refuse rather than leak. See the module docstring.
    raise RuntimeError(
        "span redaction is not configured: OTEL_SEMCONV_STABILITY_OPT_IN must "
        f"contain 'gen_ai_unredacted_attributes=' with an empty value (expected "
        f"{REDACTION_TOKEN!r}). Refusing to start, because without it the full "
        "household record exports to CloudWatch."
    )

app = BedrockAgentCoreApp()

# No-op on Runtime, which sets AGENT_OBSERVABILITY_ENABLED and owns the tracer
# provider. Called once at import so a local `python -m runtime_app` still
# exports traces.
setup_telemetry()


@app.entrypoint
def invoke(payload, context=None) -> dict:
    """Process one case. Never raises.

    `context` defaults so this is callable as `invoke({...})` from a local smoke
    test as well as with the context object Runtime supplies. It is unused:
    identity comes from the payload's `case_id` and the store, never from the
    invocation metadata — layer 2 of the escalation boundary, where the case is
    bound from the session rather than from anything a prompt can reach.
    """
    try:
        if not isinstance(payload, dict):
            # Payloads arrive unchanged, so validation is this module's job.
            return {
                "status": "error",
                "case_id": "",
                "detail": f"payload must be a JSON object, got {type(payload).__name__}",
            }
        return dict(process_case(payload))
    except Exception as exc:  # noqa: BLE001 — an opaque 500 is unactionable
        logger.exception("unhandled failure processing payload")
        return {
            "status": "error",
            "case_id": str(payload.get("case_id", "")) if isinstance(payload, dict) else "",
            "detail": str(exc),
        }


if __name__ == "__main__":
    app.run()
