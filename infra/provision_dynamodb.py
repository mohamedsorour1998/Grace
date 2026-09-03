"""Create the `grace-cases` table. Idempotent: re-running is the recovery path.

One table holds both the ledger and the escalation queue, because they are the
same audit trail read two ways.
"""

from __future__ import annotations

import time

import boto3
from botocore.exceptions import ClientError

from infra import naming

# How many times to re-ask for point-in-time recovery, and how long to wait
# between attempts. `table_exists` returns as soon as the table reports ACTIVE,
# but continuous backups are not necessarily ready at that instant — measured on
# this account, `update_continuous_backups` refused with
# `ContinuousBackupsUnavailableException` on 1 of 3 consecutive fresh-table runs
# immediately after the waiter returned, and succeeded on the other 2. The
# duration of that window is not characterized (every run that refused once was
# not retried, and separate probes never refused at all), so this is a bounded
# retry rather than a computed bound.
_PITR_ATTEMPTS = 10
_PITR_RETRY_SECONDS = 3.0


def _enable_pitr(client) -> Exception | None:
    """Ask for point-in-time recovery, retrying the transient refusal.

    Returns the last transient error if every attempt was refused, else None.
    **Deliberately does not decide success** — the caller reads the state back
    and judges from that, so the arbiter is the table's actual configuration
    rather than whether an API call returned. A call that succeeds and a control
    that is on are two different claims.

    Anything other than `ContinuousBackupsUnavailableException` is re-raised
    immediately: a permissions or validation error is not something waiting will
    fix, and retrying it ten times only delays a real failure by 30 seconds.

    The original form of this function swallowed
    `ContinuousBackupsUnavailableException` and returned, on the reasoning that
    it means "not ready yet". It does — but swallowing it means the script exits
    0 having left recovery **off**, which is the silent-missing-control failure
    that is worse than a loud one: nothing in the output distinguishes it from
    success, and the next person to look is whoever needs to restore the ledger.
    Reproduced on this account, 1 run in 3. Enabling an already-enabled table is
    itself idempotent (verified), so the retry is safe on a re-run.
    """
    last: Exception | None = None
    for attempt in range(_PITR_ATTEMPTS):
        try:
            client.update_continuous_backups(
                TableName=naming.TABLE,
                PointInTimeRecoverySpecification={"PointInTimeRecoveryEnabled": True},
            )
            return None
        except ClientError as exc:
            if exc.response["Error"]["Code"] != "ContinuousBackupsUnavailableException":
                raise
            last = exc
            # No sleep after the final attempt — nothing follows it to wait for.
            if attempt < _PITR_ATTEMPTS - 1:
                time.sleep(_PITR_RETRY_SECONDS)
    return last


def provision(client=None) -> str:
    """Create the table and its GSI if absent; return the table name.

    Idempotent by design — `ResourceInUseException` means another run already
    created it, which is success, not failure. A provisioning script that cannot
    be re-run safely is useless when a deploy fails halfway.
    """
    client = client or boto3.client("dynamodb", region_name=naming.REGION)
    try:
        client.create_table(
            TableName=naming.TABLE,
            BillingMode="PAY_PER_REQUEST",
            AttributeDefinitions=[
                {"AttributeName": "pk", "AttributeType": "S"},
                {"AttributeName": "sk", "AttributeType": "S"},
                {"AttributeName": "status", "AttributeType": "S"},
                {"AttributeName": "escalated_at", "AttributeType": "S"},
            ],
            KeySchema=[
                {"AttributeName": "pk", "KeyType": "HASH"},
                {"AttributeName": "sk", "KeyType": "RANGE"},
            ],
            GlobalSecondaryIndexes=[
                {
                    "IndexName": naming.ESCALATION_GSI,
                    # Only escalation rows carry `status`, so the index holds
                    # exactly the caseworker queue — a sparse index, not a
                    # filtered scan of every ledger row.
                    "KeySchema": [
                        {"AttributeName": "status", "KeyType": "HASH"},
                        {"AttributeName": "escalated_at", "KeyType": "RANGE"},
                    ],
                    "Projection": {"ProjectionType": "ALL"},
                }
            ],
            Tags=[{"Key": k, "Value": v} for k, v in naming.TAGS.items()],
        )
    except ClientError as exc:
        if exc.response["Error"]["Code"] != "ResourceInUseException":
            raise

    client.get_waiter("table_exists").wait(TableName=naming.TABLE)

    # Point-in-time recovery, with a bounded retry and a verified read-back.
    #
    # **Do not swallow `ContinuousBackupsUnavailableException`.** Measured, not
    # theorised: running this function three times against throwaway tables gave
    # PITR `ENABLED, ENABLED, DISABLED` — one run in three hit a transient
    # refusal in the moments after `table_exists` returns, and a bare
    # `except ...: pass` left the ledger table with recovery **off** while the
    # script exited 0 reporting success. An audit trail for benefits decisions
    # that is silently unrecoverable is precisely the "control that looks present
    # and is absent" failure this codebase keeps finding.
    #
    # Verified while fixing it: enabling PITR on an already-enabled table
    # succeeds and returns `ENABLED` (so the retry is safely idempotent), and
    # `describe_continuous_backups` reads the new value back immediately (so the
    # verification below cannot produce a false alarm).
    #
    # Raising is the right failure here, unlike most of Grace's fail-open
    # observability decisions: this is a provisioning script, not the request
    # path. A loud failure blocks a deploy and the operator re-runs — the script
    # is idempotent precisely so that is the recovery path.
    last_error = _enable_pitr(client)

    status = (
        client.describe_continuous_backups(TableName=naming.TABLE)
        .get("ContinuousBackupsDescription", {})
        .get("PointInTimeRecoveryDescription", {})
        .get("PointInTimeRecoveryStatus")
    )
    if status != "ENABLED":
        raise RuntimeError(
            f"point-in-time recovery is {status} on {naming.TABLE} after retrying "
            f"(last error: {last_error}). The ledger is the audit trail for every "
            "autonomous benefits decision, so it must be recoverable. This script "
            "is idempotent — re-run it."
        )

    return naming.TABLE


if __name__ == "__main__":
    print(f"provisioned {provision()}")
