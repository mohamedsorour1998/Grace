"""One case → one Runtime invocation → one outcome.

Deliberately outside the `grace` package: this is packaged into a zip and must
import with only boto3 available, which the Lambda runtime provides. It contains
no classification logic — the gate's verdict comes from the runtime, and a Lambda
that second-guessed it would be a second decision point with no gate behind it.

**Every return carries a string `status` and a string `case_id`.** The state
machine routes on `$.status` and keys its escalation row on `$.case_id`. A Choice
state that references an absent path raises `States.Runtime`, which a Choice
state cannot itself Catch, so guaranteeing both fields here is what keeps that
failure unreachable rather than merely unlikely.
"""

from __future__ import annotations

import json
import os
import uuid

import boto3
from botocore.config import Config

_REGION = os.getenv("AWS_REGION", "us-east-1")
_RUNTIME_ARN_ENV = "GRACE_RUNTIME_ARN"
_DEFAULT_TODAY = "2026-10-01"

# The Lambda's configured timeout. `infra/provision_lambda.py` imports this
# rather than restating it, so the two numbers below cannot drift into the wrong
# order. A swarm-routed case can take several minutes of real Bedrock latency —
# Plan 1 measured one eval run at 512s against a typical 75s — and the graph's
# own 420s node timeout inside a 900s execution timeout bounds the runtime side.
LAMBDA_TIMEOUT_SECONDS = 900

# Slightly under the Lambda's deadline, so the handler's own structured error
# (which names the case) wins the race deterministically rather than the Lambda
# being killed mid-call. Both outcomes now reach a human — the Catch branch
# covers a killed Lambda and the Choice branch covers a returned error — so this
# decides which diagnostic a caseworker reads, not whether the family is lost.
_READ_TIMEOUT_SECONDS = 870

# **`invoke_agent_runtime` is not idempotent, and botocore retries a read
# timeout by default.** Measured against a real boto3 client with a black-hole
# socket server (accepts the connection, never replies): the default
# configuration made **five** HTTP attempts before raising `ReadTimeoutError`.
# `ReadTimeoutError` is mapped to `GENERAL_CONNECTION_ERROR` in botocore's retry
# table, so a slow runtime looks identical to a dropped connection. Each attempt
# re-runs the entire graph for the same case, which means a case that is merely
# slow could file the same renewal more than once — hard rule 6's harm approached
# from the other direction.
#
# `total_max_attempts` is the only setting that means "do not retry": the same
# probe measured `{"mode": "standard", "max_attempts": 1}` at **two** attempts,
# because `max_attempts` counts retries in standard mode while
# `total_max_attempts` counts attempts. A single timed-out case escalates
# through the state machine, which is the correct outcome; a silently repeated
# renewal is not.
_CLIENT_CONFIG = Config(
    read_timeout=_READ_TIMEOUT_SECONDS,
    connect_timeout=10,
    retries={"total_max_attempts": 1},
)


def _runtime_client():
    """The AgentCore Runtime client, configured never to retry.

    A function rather than a module-level client so the configuration is
    assertable in a test without a live endpoint, and so a cold start does not
    pay for a client the handler may never use.
    """
    return boto3.client("bedrock-agentcore", region_name=_REGION, config=_CLIENT_CONFIG)


def lambda_handler(event: dict, context: object, client=None, runtime_arn: str | None = None) -> dict:
    """Invoke the deployed runtime for exactly one case.

    `client` and `runtime_arn` are injectable so this is testable offline; in
    Lambda both come from the environment.
    """
    case_id = str(event.get("case_id", "")).strip() if isinstance(event, dict) else ""
    today = str(event.get("today") or _DEFAULT_TODAY) if isinstance(event, dict) else _DEFAULT_TODAY

    try:
        client = client or _runtime_client()
        runtime_arn = runtime_arn or os.environ[_RUNTIME_ARN_ENV]

        # A fresh session per case. `runtimeSessionId` must be 33+ characters — a
        # shorter one is rejected at invoke time — and a distinct session per case is
        # what keeps `AuthorityGate._seen` from ever spanning two households.
        session_id = f"grace-{case_id}-{uuid.uuid4()}"

        response = client.invoke_agent_runtime(
            agentRuntimeArn=runtime_arn,
            runtimeSessionId=session_id,
            payload=json.dumps({"case_id": case_id, "today": today}).encode(),
        )
        outcome = json.loads(response["response"].read())
        if not isinstance(outcome, dict):
            raise TypeError(
                f"runtime returned {type(outcome).__name__}, expected a JSON object"
            )
    except Exception as exc:  # noqa: BLE001 — name the case, always
        # Step Functions' Catch covers a *killed* Lambda, but a returned error
        # does not trip it — the state machine's Choice state routes on
        # `$.status == "error"` for exactly this shape. Naming the case is what
        # lets the escalation row that branch writes identify the family.
        return {"status": "error", "case_id": case_id, "detail": str(exc)}

    status = outcome.get("status")
    if not isinstance(status, str) or not status:
        # A runtime that answered without a status has not produced a verdict,
        # and "no verdict" must reach a human rather than pass as success.
        return {
            "status": "error",
            "case_id": case_id,
            "detail": f"runtime returned no status: {json.dumps(outcome)[:400]}",
        }

    # The case the Lambda was *asked* about always wins. The escalation row is
    # keyed on `case_id`, so echoing back a different one would write a row
    # against the wrong household.
    return {**outcome, "status": status, "case_id": case_id}
