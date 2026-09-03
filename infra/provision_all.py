"""Provision everything, in dependency order. Idempotent end to end.

The order matters: IAM before anything that needs a role, the table before the
runtime that writes to it, the runtime before the Lambda that invokes it, the
Lambda before the state machine, the state machine before the schedule, and the
alarm last — its metric filter sits over the log group `provision_stepfunctions`
creates.

The runtime itself is **not** created here — `agentcore deploy` owns that, because
it builds and pushes a container image. This script reads the deployed runtime's
ARN and wires everything else around it. Two owners for one resource is how a
redeploy silently reverts a setting.
"""

from __future__ import annotations

import boto3

from infra import (
    naming,
    provision_alarm,
    provision_dynamodb,
    provision_eventbridge,
    provision_iam,
    provision_lambda,
    provision_memory,
    provision_stepfunctions,
)

# The deployed runtime's name. `agentcore` prefixes the runtime with its project
# name, so `naming.RUNTIME` ("grace") yields `grace_grace` — matched as
# `grace_`-prefixed rather than by the exact string, because the project name and
# the runtime name are both "grace" only by coincidence of this project's layout.
_RUNTIME_PREFIX = f"{naming.RUNTIME}_"


def runtime_arn(control_client=None) -> str:
    """The deployed Grace runtime's ARN.

    **Paginates.** `list_agent_runtimes` returns 10 per page with a `nextToken`,
    and this account holds 16 runtimes across two projects — `grace_grace` is on
    page **two**. The plan's draft called `list_agent_runtimes()` once and read
    `agentRuntimes` from the single response, which measured against the real
    account would have raised "no Grace runtime found" for a runtime that is
    deployed and READY. The operator's fix would then have been to redeploy
    something already there. Same class of bug as `provision_memory`'s
    single-page `ListMemories` read and the single-page `ledger()` read.

    **Refuses a runtime that is not READY.** A CREATING runtime already has an
    ARN, so returning it would wire a Lambda and a state machine to something
    that cannot serve a case — and the failure would surface as an invocation
    error on the first sweep, long after this script exited 0.
    """
    client = control_client or boto3.client(
        "bedrock-agentcore-control", region_name=naming.REGION
    )

    token: str | None = None
    while True:
        kwargs = {"nextToken": token} if token else {}
        response = client.list_agent_runtimes(**kwargs)
        for runtime in response.get("agentRuntimes", []):
            name = str(runtime.get("agentRuntimeName", ""))
            # `startswith(_RUNTIME_PREFIX)` and not `startswith("grace")`: this
            # account really does hold `rosettacloud_education_memory` beside
            # `..._v2`, and a bare prefix would equally match a
            # `graceful_something` belonging to someone else.
            if not name.startswith(_RUNTIME_PREFIX):
                continue
            status = runtime.get("status")
            if status != "READY":
                raise RuntimeError(
                    f"runtime {name} is {status}, not READY — deploy it first "
                    "with `agentcore deploy`"
                )
            return str(runtime["agentRuntimeArn"])
        token = response.get("nextToken")
        if not token:
            raise RuntimeError(
                f"no runtime named {_RUNTIME_PREFIX}* found in {naming.REGION}. "
                "Run `agentcore deploy` (Task 7) before this — this script wires "
                "infrastructure around the deployed runtime and never creates it."
            )


# Retained under the old private name so nothing that already imports it breaks.
_runtime_arn = runtime_arn


def main() -> dict[str, str]:
    created: dict[str, str] = {}
    created["table"] = provision_dynamodb.provision()
    created["memory_id"] = provision_memory.provision()
    roles = provision_iam.provision()
    created.update({f"role_{k}": v for k, v in roles.items()})
    created["runtime"] = runtime_arn()
    created["lambda"] = provision_lambda.provision(
        created["runtime"], role_arn=roles["lambda"]
    )
    created["state_machine"] = provision_stepfunctions.provision(
        created["lambda"], role_arn=roles["stepfunctions"]
    )
    created["schedule"] = provision_eventbridge.provision(
        created["state_machine"], role_arn=roles["eventbridge"]
    )
    # Last: its metric filter sits over the log group the state machine's own
    # provisioning creates.
    created["alarm"] = provision_alarm.provision()
    return created


if __name__ == "__main__":
    for key, value in main().items():
        print(f"{key}: {value}")
