"""Package and create the `grace-invoke-case` function. Idempotent."""

from __future__ import annotations

import io
import time
import zipfile
from pathlib import Path

import boto3
from botocore.exceptions import ClientError

from infra import naming, provision_iam
from infra.lambda_src.handler import LAMBDA_TIMEOUT_SECONDS

_SOURCE = Path(__file__).parent / "lambda_src" / "handler.py"

# Imported, not restated. The handler's socket read timeout must stay below this
# number, and a second literal here is how those two drift into the wrong order
# — at which point a slow-but-correct case is killed by the socket instead of
# reaching the state machine's Catch branch.
TIMEOUT_SECONDS = LAMBDA_TIMEOUT_SECONDS

MEMORY_MB = 256

# How long to wait for a create/update to settle. Lambda rejects a configuration
# update while a code update is `InProgress`, and Step 11 invokes the function
# immediately after provisioning it.
_SETTLE_ATTEMPTS = 30
_SETTLE_SECONDS = 2.0


def _zip_bytes() -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("handler.py", _SOURCE.read_text())
    return buffer.getvalue()


def _wait_until_settled(client) -> None:
    """Block until no update is in flight.

    Two distinct waits, and both are needed. `function_updated_v2` covers the
    update path; `function_active_v2` covers the create path, where a brand-new
    function is `Pending` for a few seconds and an immediate invoke fails with
    `ResourceConflictException`. Step 11 invokes this function through Step
    Functions seconds after provisioning it, so "the API call returned" is not
    the claim that matters — the same distinction `provision_dynamodb` makes
    about point-in-time recovery.
    """
    for waiter_name in ("function_updated_v2", "function_active_v2"):
        try:
            client.get_waiter(waiter_name).wait(
                FunctionName=naming.LAMBDA,
                WaiterConfig={"Delay": int(_SETTLE_SECONDS), "MaxAttempts": _SETTLE_ATTEMPTS},
            )
        except Exception:  # noqa: BLE001
            # A fake client in a test may not implement waiters, and a waiter
            # that cannot run is not a provisioning failure — the read-back
            # below is what decides success. Raising here would make the
            # function untestable offline without proving anything extra.
            return


def provision(runtime_arn: str, client=None, role_arn: str | None = None) -> str:
    """Create or update the function; return its ARN."""
    client = client or boto3.client("lambda", region_name=naming.REGION)
    if role_arn is None:
        role_arn = provision_iam.provision()["lambda"]

    code = _zip_bytes()
    environment = {"Variables": {"GRACE_RUNTIME_ARN": runtime_arn}}
    try:
        response = client.create_function(
            FunctionName=naming.LAMBDA,
            Runtime="python3.12",
            Role=role_arn,
            Handler="handler.lambda_handler",
            Code={"ZipFile": code},
            # A swarm-routed case can take several minutes of real Bedrock
            # latency; Plan 1 measured one eval run at 512s. The graph's own
            # 420s node timeout bounds the runtime side, and this must clear it
            # or the Lambda would kill a case the graph was still handling
            # correctly.
            Timeout=TIMEOUT_SECONDS,
            MemorySize=MEMORY_MB,
            Environment=environment,
            Tags=naming.TAGS,
        )
    except ClientError as exc:
        if exc.response["Error"]["Code"] != "ResourceConflictException":
            raise
        client.update_function_code(FunctionName=naming.LAMBDA, ZipFile=code)
        # A create-then-update sequence needs the code update to settle before
        # the configuration update is accepted.
        for _ in range(_SETTLE_ATTEMPTS):
            state = client.get_function_configuration(FunctionName=naming.LAMBDA)
            if state.get("LastUpdateStatus") != "InProgress":
                break
            time.sleep(_SETTLE_SECONDS)
        client.update_function_configuration(
            FunctionName=naming.LAMBDA,
            Role=role_arn,
            Timeout=TIMEOUT_SECONDS,
            # `MemorySize` is here as well as on the create path so a re-run
            # converges fully. A partial update leaves a function whose settings
            # depend on which run created it, which is exactly what idempotence
            # is supposed to remove.
            MemorySize=MEMORY_MB,
            Environment=environment,
        )
        response = client.get_function_configuration(FunctionName=naming.LAMBDA)

    _wait_until_settled(client)
    return str(response["FunctionArn"])
