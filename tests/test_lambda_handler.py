"""The Lambda is a thin adapter: one case in, one outcome out.

It must not contain classification logic. The gate's verdict comes from the
runtime; a Lambda that second-guessed it would be a second decision point with no
gate behind it.

The client-configuration tests are not hygiene. `invoke_agent_runtime` is **not
idempotent** — each attempt re-runs the whole graph against the same case — and
botocore's default retry policy treats a read timeout as retriable. See
`test_the_runtime_client_never_retries`.
"""

from __future__ import annotations

import json

from infra.lambda_src import handler as lambda_handler_module

RUNTIME_ARN = "arn:aws:bedrock-agentcore:us-east-1:1:runtime/grace"


class FakeRuntimeClient:
    def __init__(self, body):
        self._body = body
        self.calls = []

    def invoke_agent_runtime(self, **kwargs):
        self.calls.append(kwargs)

        class Body:
            @staticmethod
            def read():
                return json.dumps(self._body).encode()

        return {"response": Body()}


def test_the_case_id_is_passed_through_and_the_outcome_returned():
    client = FakeRuntimeClient({"status": "escalated", "case_id": "c-011",
                               "reason": "material_income_change"})
    out = lambda_handler_module.lambda_handler(
        {"case_id": "c-011", "today": "2026-10-01"}, None,
        client=client, runtime_arn=RUNTIME_ARN,
    )
    assert out["status"] == "escalated"
    assert out["case_id"] == "c-011"
    payload = json.loads(client.calls[0]["payload"])
    assert payload["case_id"] == "c-011"
    assert payload["today"] == "2026-10-01"


def test_the_session_id_is_at_least_33_characters():
    """A Runtime constraint: `runtimeSessionId` must be 33+ characters, and a
    shorter one is rejected at invoke time rather than at deploy."""
    client = FakeRuntimeClient({"status": "acted", "case_id": "c-001"})
    lambda_handler_module.lambda_handler(
        {"case_id": "c-001"}, None, client=client, runtime_arn=RUNTIME_ARN,
    )
    assert len(client.calls[0]["runtimeSessionId"]) >= 33


def test_each_case_gets_its_own_session():
    """One case per session, so `AuthorityGate._seen` can never span two
    households — the per-instance isolation Task 6 established, made
    structural."""
    client = FakeRuntimeClient({"status": "acted", "case_id": "c-001"})
    for case_id in ("c-001", "c-002"):
        lambda_handler_module.lambda_handler(
            {"case_id": case_id}, None, client=client, runtime_arn=RUNTIME_ARN,
        )
    sessions = [c["runtimeSessionId"] for c in client.calls]
    assert len(set(sessions)) == 2


def test_a_runtime_failure_becomes_an_error_outcome_not_an_exception():
    """Step Functions' Catch handles this too, but a structured error is more
    useful than a stack trace: it names the case, so the escalation row the
    Catch branch writes can identify the family."""

    class Boom:
        def invoke_agent_runtime(self, **kwargs):
            raise RuntimeError("runtime unavailable")

    out = lambda_handler_module.lambda_handler(
        {"case_id": "c-012"}, None, client=Boom(), runtime_arn=RUNTIME_ARN,
    )
    assert out["status"] == "error"
    assert out["case_id"] == "c-012"
    assert "runtime unavailable" in out["detail"]


def test_the_default_today_is_pinned_never_a_live_clock():
    """Fixture c-002 goes `closed` on 2026-10-31, so a live clock turns the
    9-act/3-escalate demo into 8/4 from that date."""
    client = FakeRuntimeClient({"status": "acted", "case_id": "c-001"})
    lambda_handler_module.lambda_handler(
        {"case_id": "c-001"}, None, client=client, runtime_arn=RUNTIME_ARN,
    )
    assert json.loads(client.calls[0]["payload"])["today"] == "2026-10-01"


# ---------------------------------------------------------------------------
# The outcome shape the state machine's Choice state reads
# ---------------------------------------------------------------------------


def test_a_response_without_a_status_becomes_an_error_outcome():
    """The state machine routes on `$.status`, and a Choice state referencing an
    absent path raises `States.Runtime`, which a Choice state cannot Catch — it
    fails the whole Map branch. Guaranteeing the field here is what keeps that
    unreachable, so the guarantee is load-bearing rather than defensive."""
    client = FakeRuntimeClient({"unexpected": "shape"})
    out = lambda_handler_module.lambda_handler(
        {"case_id": "c-003"}, None, client=client, runtime_arn=RUNTIME_ARN,
    )
    assert out["status"] == "error"
    assert out["case_id"] == "c-003"


def test_a_non_object_response_becomes_an_error_outcome():
    """`dict(json.loads(...))` raises on a JSON array or string, and that raise
    would reach the caller as an exception rather than a reportable outcome."""

    class Weird:
        def invoke_agent_runtime(self, **kwargs):
            class Body:
                @staticmethod
                def read():
                    return b'"not an object"'

            return {"response": Body()}

    out = lambda_handler_module.lambda_handler(
        {"case_id": "c-004"}, None, client=Weird(), runtime_arn=RUNTIME_ARN,
    )
    assert out["status"] == "error"
    assert out["case_id"] == "c-004"


def test_a_response_case_id_never_overrides_the_requested_one():
    """The escalation row is keyed on `case_id`. If a runtime echoed back a
    different one, the Choice branch would write a row against the wrong
    household — so the case the Lambda was *asked* about always wins."""
    client = FakeRuntimeClient({"status": "error", "case_id": "c-999"})
    out = lambda_handler_module.lambda_handler(
        {"case_id": "c-005"}, None, client=client, runtime_arn=RUNTIME_ARN,
    )
    assert out["case_id"] == "c-005"


def test_every_outcome_carries_a_string_status_and_case_id():
    """Both fields are read by the state machine as `$.status` and `$.case_id`.
    Asserted across every shape this handler can produce, not just the happy
    one."""
    bodies = [
        {"status": "acted", "case_id": "c-001", "filed": True},
        {"status": "escalated", "case_id": "c-010", "reason": "missing_document"},
        {"unexpected": "shape"},
        {"status": 7},
    ]
    for body in bodies:
        out = lambda_handler_module.lambda_handler(
            {"case_id": "c-001"}, None, client=FakeRuntimeClient(body),
            runtime_arn=RUNTIME_ARN,
        )
        assert isinstance(out["status"], str) and out["status"]
        assert isinstance(out["case_id"], str) and out["case_id"]


# ---------------------------------------------------------------------------
# Client configuration — the non-idempotent-retry defect
# ---------------------------------------------------------------------------


def test_the_runtime_client_never_retries():
    """**`invoke_agent_runtime` is not idempotent, and botocore retries a read
    timeout by default.**

    Measured with a black-hole socket server (accepts, never replies) against a
    real boto3 client: the default configuration made **5** HTTP attempts before
    raising `ReadTimeoutError`. Each attempt re-runs the entire graph for the
    same case, so a case that is merely slow — Plan 1 measured one real run at
    512s against a typical 75s — could file a renewal more than once. Hard rule
    6 forbids claiming an unconfirmed action; filing the same renewal five times
    is the same harm approached from the other side.

    `total_max_attempts` is the only setting that means "do not retry". The same
    probe measured `retries={"mode": "standard", "max_attempts": 1}` at **2**
    attempts, because `max_attempts` counts *retries* in standard mode while
    `total_max_attempts` counts attempts. A test asserting `max_attempts == 1`
    would therefore pass while the client still retried.
    """
    config = lambda_handler_module._runtime_client().meta.config
    assert config.retries["total_max_attempts"] == 1


def test_the_read_timeout_clears_the_graphs_own_budget():
    """A 60s socket timeout — botocore's default — is far below what a
    swarm-routed case takes.

    `grace/graph.py` sets `node_timeout=420` inside `execution_timeout=900`, and
    the two swarm cases are the slow ones. With the default read timeout the
    socket would give up at 60s on a case the graph was still handling
    correctly, and (before the no-retry fix) do it four more times.

    The upper bound is the Lambda's own deadline: staying below it makes the
    ordering deterministic, so the handler's structured error — which names the
    case — wins the race rather than a coin flip against the Lambda being
    killed. Both outcomes now write an escalation row (the Catch branch for the
    Lambda deadline, the Choice branch for the handler's error), so this is
    about which diagnostic a caseworker gets, not about whether the family is
    lost.
    """
    read_timeout = lambda_handler_module._runtime_client().meta.config.read_timeout
    assert read_timeout >= 420
    assert read_timeout < lambda_handler_module.LAMBDA_TIMEOUT_SECONDS


def test_the_lambda_timeout_is_shared_with_the_provisioner():
    """One number, one place. `provision_lambda` imports it rather than
    restating it, so the handler's read timeout and the function's configured
    timeout cannot drift into the wrong order."""
    from infra import provision_lambda

    assert (
        provision_lambda.TIMEOUT_SECONDS
        is lambda_handler_module.LAMBDA_TIMEOUT_SECONDS
    )
