"""A2A server entrypoint for Grace, on port 9000, beside the HTTP runtime.

Deliberately thin, like `runtime_app.py`: the request handling is
`grace.a2a.CaseExecutor`, and the server is the SDK's own
(`bedrock_agentcore.runtime.build_a2a_app` / `serve_a2a`), which provides the
AgentCore A2A contract — JSON-RPC 2.0 on `POST /`, `GET /ping`, and the agent
card at `GET /.well-known/agent-card.json`.

It makes the same two refusals as `runtime_app.py`, at import and in the same
order, so a misconfigured container never serves a request:

1. **No span redaction, no start** (hard rule 8). Without the empty
   `gen_ai_unredacted_attributes=` token the full household record exports to
   CloudWatch, continuously and silently.
2. **No supported region, no start.** Grace's inference profiles exist only in
   `infra.naming.SUPPORTED_REGIONS`; anywhere else the first case would fail.

The agent card advertises `http://localhost:9000/`, which is what `serve_a2a`
sets; on AgentCore Runtime the SDK replaces it with `AGENTCORE_RUNTIME_URL`,
which Runtime sets.
"""

from __future__ import annotations

import logging

# Imported here, not only inside build_a2a_app, so every SDK logger exists
# before `withhold_sdk_request_content` attaches its filter to them below.
import a2a.server.apps  # noqa: F401
import a2a.server.request_handlers  # noqa: F401
from bedrock_agentcore.runtime import build_a2a_app, serve_a2a
from bedrock_agentcore.runtime.a2a import A2A_CONTRACT_PORT

from grace.a2a import CaseExecutor, build_agent_card, withhold_sdk_request_content
from grace.observability import REDACTION_TOKEN, redaction_is_configured, setup_telemetry
from infra import naming

logging.basicConfig(level=logging.INFO)

if not redaction_is_configured():
    # Refuse rather than leak. See the module docstring.
    raise RuntimeError(
        "span redaction is not configured: OTEL_SEMCONV_STABILITY_OPT_IN must "
        f"contain 'gen_ai_unredacted_attributes=' with an empty value (expected "
        f"{REDACTION_TOKEN!r}). Refusing to start, because without it the full "
        "household record exports to CloudWatch."
    )

# Second, and only after redaction, as in runtime_app.py.
naming.require_supported_region()

# No-op on Runtime, which owns the tracer provider; local runs still export.
setup_telemetry()

# Hard rule 9: the SDK's own log lines quote request content. See grace/a2a.py.
withhold_sdk_request_content()

# The same address `serve_a2a` advertises, so a test of `build_app` sees the
# card a real run serves.
CARD_URL = f"http://localhost:{A2A_CONTRACT_PORT}/"


def build_app(executor: CaseExecutor | None = None):
    """The ASGI app `main` serves, and the one the tests drive."""
    app = build_a2a_app(executor or CaseExecutor(), build_agent_card(CARD_URL))
    withhold_sdk_request_content()  # again, for any logger building the app created
    return app


def main() -> None:
    # `serve_a2a` binds 0.0.0.0 when it believes it is containerized
    # (`/.dockerenv` or `DOCKER_CONTAINER`, which Dockerfile.a2a sets), else
    # loopback — the same rule, and the same Podman caveat, as runtime_app.py.
    serve_a2a(CaseExecutor(), build_agent_card(CARD_URL), port=A2A_CONTRACT_PORT)


if __name__ == "__main__":
    main()
