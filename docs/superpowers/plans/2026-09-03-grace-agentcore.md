# Grace AgentCore Deployment Implementation Plan (Plan 2 of 3)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Deploy the unmodified `grace` package to AgentCore Runtime with a real DynamoDB ledger, per-household Memory, and an EventBridge → Step Functions → Lambda sweep, so the 9-acted/3-escalated claim holds against deployed infrastructure.

**Architecture:** Nothing in Plan 1's decision path changes. Four additive modules (`grace/cases/dynamo_store.py`, `grace/entrypoint.py`, `grace/memory.py`, `grace/observability.py`) sit behind interfaces that already exist, and `infra/` holds idempotent `boto3` provisioning scripts. The sweep loop moves out of Python into a Step Functions Map state, one case per Lambda invocation, one case per Runtime session.

**Tech Stack:** Python 3.12 / `uv`, `strands-agents[otel]==1.54.0`, `bedrock-agentcore` (new, 2 marginal packages), `boto3`, AgentCore Runtime + Memory, DynamoDB, Step Functions, EventBridge, Lambda, CloudWatch.

**Spec:** `docs/superpowers/specs/2026-09-03-grace-agentcore-design.md` — read it before Task 1. Plan 1 (`docs/superpowers/plans/2026-08-28-grace-core.md`) carries Appendices C/D/E, which are the verified AgentCore research this plan rests on.

---

## Global Constraints

Every task's requirements implicitly include this section. Values are copied verbatim from the spec and from CLAUDE.md.

- **Plan 1's 360 tests must pass, unchanged, at every commit.** If a task requires editing `grace/authority.py`, `grace/steering.py`, `grace/graph.py`, or `grace/swarm.py`, **stop and report** rather than editing. New tests are additive. Run `.venv/bin/python -m pytest` (fast suite, ~30s, excludes `evals/` via `testpaths = ["tests"]`).
- **Amazon Nova only.** Model IDs live in `grace/models.py`, referenced by role (`nova("verifier")`), never inlined. A test walks `grace/` with `pkgutil` and fails on a non-Nova vendor ID in any module — new modules are covered automatically.
- **`grace/authority.py` stays pure.** No `strands`, no `boto3`, no I/O. A test greps for violations. Nothing in this plan touches it.
- **All household data is synthetic.** Phone numbers use the reserved `+1555` range; a test asserts it. The DynamoDB table holds ledger and escalation rows only — never household records — so fixtures stay the single source of truth.
- **Never claim an action succeeded without tool confirmation** (hard rule 6). `renewal_submitted` on the ledger is the only evidence a renewal was filed.
- **Escalating is always allowed.** `escalate_to_caseworker` is never gated.
- **Never remove the span-redaction token.** `OTEL_SEMCONV_STABILITY_OPT_IN` must keep the `gen_ai_unredacted_attributes=` suffix. The trailing `=` is load-bearing; absence of the token disables redaction entirely and exports the full household record to CloudWatch.
- **Never put household identity in a span attribute.** `grace.case_id` yes; name, phone, or address never.
- **Dependencies stay minimal.** This plan adds exactly one declared dependency (`bedrock-agentcore`), measured at 2 marginal packages. **Never add `strands-agents-tools`** (30 packages incl. `slack-bolt`, `pillow`) or `strands-agents-evals` (depends on the former). **Never add `aws-opentelemetry-distro`** — Runtime instruments itself.
- **Pin the date.** `TODAY = date(2026, 10, 1)` in tests; `DEFAULT_TODAY = "2026-10-01"`. Fixture `c-002` goes `closed` on 2026-10-31, so a `date.today()` anywhere turns 9/3 into 8/4.
- **Region `us-east-1`, account `<AWS_ACCOUNT_ID>`.** Resource names are `grace-*` throughout.
- **Verify the SDK, do not trust its docs.** Introspect before use; `strands-agents` and the `agentcore` CLI both drift.
- **Conventional commits** (`feat:`, `test:`, `fix:`, `docs:`, `chore:`). Commit after each completed task, and tick the plan's checkboxes.

---

## File Structure

**New Grace modules** — each one responsibility, each behind an existing interface:

```text
grace/
  cases/dynamo_store.py   # DynamoDBCaseStore: the CaseStore Protocol over one table
  entrypoint.py           # the invoke_agent_runtime handler; one case per invocation
  memory.py               # AgentCoreMemorySessionManager wiring, orchestrator only
  observability.py        # conditional telemetry setup (skips itself on Runtime)
  store_factory.py        # which CaseStore this process uses, from GRACE_STORE
```

**Infrastructure** — idempotent, re-runnable, no new dependency:

```text
infra/
  __init__.py
  naming.py               # every resource name and ARN shape in one place
  provision_dynamodb.py   # grace-cases table + escalation-queue GSI
  provision_iam.py        # the four roles, incl. the explicit Deny
  provision_memory.py     # AgentCore Memory + namespace strategies
  provision_lambda.py     # grace-invoke-case
  provision_stepfunctions.py  # grace-sweep state machine
  provision_eventbridge.py    # daily schedule
  provision_alarm.py      # escalated < 3
  provision_all.py        # runs the above in dependency order
  teardown.py             # delete what provision_all created
  lambda_src/handler.py   # the Lambda's own source (packaged, not imported by grace)
```

**Tests** — additive; nothing in `tests/` is rewritten:

```text
tests/
  test_dynamo_store.py    # parametrized over BOTH stores — the anti-drift guard
  test_store_factory.py
  test_entrypoint.py
  test_memory.py
  test_observability.py
  test_infra_naming.py
```

**Docs:**

```text
docs/runbook-deploy.md    # the ordered, verified deploy sequence
README.md                 # updated: what shipped, what did not, and why
```

### Why these boundaries

`store_factory.py` exists so no module needs to know *which* store it got — `entrypoint.py` asks the factory and receives a `CaseStore`. Without it, the env-var branch would end up duplicated in the entrypoint and the Lambda.

`naming.py` exists because seven provisioning scripts and one runbook otherwise each hardcode `grace-cases`, and a rename becomes a grep. It is also what `test_infra_naming.py` asserts against, so a typo in a resource name fails a test rather than a deploy.

`infra/lambda_src/handler.py` is deliberately **not** part of the `grace` package: it is packaged into a zip and must stay importable without Grace's dependencies. It calls `invoke_agent_runtime` and nothing else.

---

## Task 0: Preflight — fail loudly before writing any code

Two blockers were confirmed on 2026-09-03 and will stop a deploy dead. Discovering either on demo
day is the expensive version. This task writes nothing to AWS.

**Files:**

- Create: `docs/runbook-deploy.md` (the preflight section only; later tasks append)

**Interfaces:**

- Consumes: nothing
- Produces: a verified-green environment. No code artifacts.

- [x] **Step 1: Confirm what is already satisfied**

```bash
export AWS_PAGER=""
aws sts get-caller-identity
aws xray get-trace-segment-destination --region us-east-1
```

Expected: an identity in account `<AWS_ACCOUNT_ID>`, and
`{"Destination": "CloudWatchLogs", "Status": "ACTIVE"}`.

**CloudWatch Transaction Search is already ACTIVE — do not re-enable it.** CLAUDE.md warns it is a
one-time account action taking up to ten minutes; it is done. If `Status` is anything other than
`ACTIVE`, stop and report, because the demo's central query depends on it.

- [x] **Step 2: Confirm the three Nova profiles**

```bash
for m in global.amazon.nova-2-lite-v1:0 us.amazon.nova-pro-v1:0 us.amazon.nova-micro-v1:0; do
  aws bedrock get-inference-profile --inference-profile-identifier "$m" \
    --region us-east-1 --query 'status' --output text
done
```

Expected: `ACTIVE` three times. These are the advocate, verifier, and referee models (hard rule 2 —
three *different* models). A non-ACTIVE profile means the swarm cannot run and the plan stops.

- [x] **Step 3: Start the container engine — Podman, not Docker**

**This project uses Podman 6.1.0.** Docker's binary is installed but its daemon is not running and
is not used. Verified working on 2026-09-03:

```bash
podman machine start
export DOCKER_HOST="$(podman machine inspect --format '{{.ConnectionInfo.PodmanSocket.Path}}' podman-machine-default)"
podman ps
podman info --format '{{.Version.OsArch}}'
```

Expected: `podman ps` prints a table header, and `podman info` reports **`linux/arm64`**. The VM is
native arm64, which is what AgentCore Runtime requires — so `--platform linux/arm64` is a no-op
rather than a slow cross-build under emulation.

**The `DOCKER_HOST` export is required** for any Docker-API client — including the `agentcore` CLI —
to find Podman. `podman-mac-helper` is not installed, so the conventional
`/var/run/docker.sock` path does not exist; installing it needs sudo and is deliberately avoided
because the env var is sufficient and needs no privilege escalation. **Export it in every shell that
runs a build or a deploy**, including inside any subagent that reaches Task 7.

`agentcore deploy` builds a container image and pushes it to ECR, so a stopped machine fails that
task several minutes in. If `podman machine start` fails, stop and report rather than falling back
to Docker.

- [x] **Step 4: Install the `agentcore` CLI and re-introspect its commands**

```bash
npm install -g @aws/agentcore
agentcore --version
agentcore --help
agentcore create --help
```

Expected: version **0.28.1 or later**. Plan 1's appendices were verified against **0.24.2**, so
treat every recorded command shape as a hint and confirm it against `--help` output before running
it. The current CLI is `create` → `add` → `deploy`; the older `configure` / `launch` pair is from
the deprecated `bedrock-agentcore-starter-toolkit`, which shadows this package and must not be
installed.

Record the actual flags you observe in `docs/runbook-deploy.md`. A later task runs these commands
for real, and a wrong flag there costs a failed deploy.

- [x] **Step 5: Confirm no Grace resources exist yet**

```bash
aws dynamodb list-tables --region us-east-1 --query 'TableNames[?starts_with(@, `grace`)]'
aws bedrock-agentcore-control list-agent-runtimes --region us-east-1 \
  --query 'agentRuntimes[?starts_with(agentRuntimeName, `grace`)].agentRuntimeName'
```

Expected: two empty lists. Prior projects' resources (`theagentorg_*`, `rosettacloud_*`) will appear
in unfiltered output and are unrelated — do not touch them. If a `grace-*` resource already exists,
a previous run of this plan got partway; read `infra/` and re-run the provisioning scripts, which are
idempotent, rather than creating a second copy.

- [x] **Step 6: Confirm the fast suite is green before changing anything**

Run: `.venv/bin/python -m pytest`
Expected: **360 passed**. This is the baseline every later task is measured against. If it is not
360, stop — something is wrong before Plan 2 began.

- [x] **Step 7: Write the preflight section of the runbook**

Create `docs/runbook-deploy.md` with a `## Preflight` section recording: the four checks above, the
observed `agentcore` version and its real command flags, and the note that Transaction Search is
already ACTIVE. This file becomes the deploy sequence; later tasks append to it.

- [x] **Step 8: Commit**

```bash
git add docs/runbook-deploy.md
git commit -m "docs: Plan 2 preflight — verified prerequisites and CLI drift"
```

---

## Task 1: Resource naming and the `grace-cases` table

The table is the foundation everything else writes to. Naming comes first so nothing hardcodes a
string twice.

**Files:**

- Create: `infra/__init__.py`, `infra/naming.py`, `infra/provision_dynamodb.py`
- Test: `tests/test_infra_naming.py`

**Interfaces:**

- Consumes: nothing
- Produces:
  - `infra.naming.REGION: str`, `ACCOUNT_ID: str | None`, `TABLE: str`, `ESCALATION_GSI: str`,
    `RUNTIME: str`, `MEMORY: str`, `LAMBDA: str`, `STATE_MACHINE: str`, `SCHEDULE_RULE: str`,
    `ALARM: str`, `TAGS: dict[str, str]`
  - `infra.naming.case_pk(case_id: str) -> str` → `"CASE#c-011"`
  - `infra.naming.ledger_sk(at: datetime, seq: int) -> str` → `"LEDGER#<iso>#<seq:06d>"`
  - `infra.naming.escalation_sk(at: datetime) -> str` → `"ESCALATION#<iso>"`
  - `infra.provision_dynamodb.provision(client=None) -> str` (returns the table name; idempotent)

- [x] **Step 1: Write the failing naming test**

Create `tests/test_infra_naming.py`:

```python
"""Resource names are asserted, not eyeballed.

Seven provisioning scripts and a runbook otherwise each hardcode `grace-cases`,
and one typo becomes a resource nobody notices is orphaned. These tests are
cheap and they make a rename a one-file change.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from infra import naming


def test_every_resource_name_is_grace_prefixed():
    """So `list-*` output can be filtered, and teardown cannot match a
    resource belonging to another project in this shared account."""
    names = [
        naming.TABLE, naming.RUNTIME, naming.MEMORY, naming.LAMBDA,
        naming.STATE_MACHINE, naming.SCHEDULE_RULE, naming.ALARM,
    ]
    for name in names:
        assert name.startswith("grace"), name


def test_the_ledger_sort_key_sorts_lexically_in_time_order():
    """DynamoDB sorts the SK as a string, so ISO-8601 plus a zero-padded
    sequence is what makes `ScanIndexForward=True` mean chronological.
    An unpadded sequence sorts 10 before 9."""
    at = datetime(2026, 10, 1, 12, 0, 0, tzinfo=timezone.utc)
    keys = [naming.ledger_sk(at, n) for n in (1, 2, 9, 10, 11)]
    assert keys == sorted(keys)


def test_two_entries_in_the_same_microsecond_get_different_sort_keys():
    """The collision this design exists to prevent. `LedgerEntry.at` is
    `datetime.now(timezone.utc)`, and one tool call writes `tool_call` and
    `tool_result` back to back; two rows sharing a timestamp would collide
    on the sort key and one would silently overwrite the other, losing an
    audit row."""
    at = datetime(2026, 10, 1, 12, 0, 0, tzinfo=timezone.utc)
    assert naming.ledger_sk(at, 1) != naming.ledger_sk(at, 2)


def test_a_naive_timestamp_is_refused():
    """`LedgerEntry` already rejects a naive datetime at construction; the
    key builder must not be the place a naive one sneaks back in and sorts
    inconsistently against aware ones."""
    with pytest.raises(ValueError):
        naming.ledger_sk(datetime(2026, 10, 1, 12, 0, 0), 1)


def test_a_non_utc_offset_still_sorts_chronologically():
    """The defect a UTC-only test cannot see.

    `LedgerEntry` requires an *aware* datetime, not a UTC one. Confirmed
    against the real type: it accepts `-05:00`. And a `-05:00` 08:00 is
    chronologically *after* a UTC 12:00, while `"...T08:00:00-05:00"`
    string-sorts *before* `"...T12:00:00+00:00"` — so without normalization
    DynamoDB returns the audit trail in the wrong order, with no error, and
    the trajectory evals read that order as ground truth.

    A test written only with UTC inputs passes against the buggy version,
    which is why this one is explicit about the mixed-offset case.
    """
    utc_noon = datetime(2026, 10, 1, 12, 0, 0, tzinfo=timezone.utc)
    est_later = datetime(2026, 10, 1, 8, 0, 0, tzinfo=timezone(timedelta(hours=-5)))
    assert est_later > utc_noon  # 13:00 UTC vs 12:00 UTC — sanity check the premise
    assert naming.ledger_sk(est_later, 1) > naming.ledger_sk(utc_noon, 1)


def test_the_escalation_key_normalizes_the_offset_too():
    """Same latent bug, same fix. Escalation rows are one per case per moment
    so collisions are unlikely, but a mis-sorted caseworker queue is still a
    mis-sorted queue."""
    utc_noon = datetime(2026, 10, 1, 12, 0, 0, tzinfo=timezone.utc)
    est_later = datetime(2026, 10, 1, 8, 0, 0, tzinfo=timezone(timedelta(hours=-5)))
    assert naming.escalation_sk(est_later) > naming.escalation_sk(utc_noon)
    with pytest.raises(ValueError):
        naming.escalation_sk(datetime(2026, 10, 1, 12, 0, 0))


def test_the_case_partition_key_is_opaque():
    """Hard rule 9's reasoning applied to storage: the key carries the case
    id, never a household name, phone, or address."""
    assert naming.case_pk("c-011") == "CASE#c-011"
```

- [x] **Step 2: Run it to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_infra_naming.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'infra'`.

- [x] **Step 3: Write `infra/naming.py`**

```python
"""Every Grace resource name and key shape, in one place.

Two reasons this is a module rather than string literals at each call site:
a rename is a one-file change, and `tests/test_infra_naming.py` can assert the
shapes — a typo in a resource name fails a test instead of a deploy.
"""

from __future__ import annotations

import os
from datetime import datetime

REGION = os.getenv("AWS_REGION", "us-east-1")
ACCOUNT_ID = os.getenv("AWS_ACCOUNT_ID")

TABLE = "grace-cases"
ESCALATION_GSI = "escalation-queue"
RUNTIME = "grace"
MEMORY = "grace_household_memory"
LAMBDA = "grace-invoke-case"
STATE_MACHINE = "grace-sweep"
SCHEDULE_RULE = "grace-daily-sweep"
ALARM = "grace-escalations-below-expected"

# Tagged at creation so Grace's spend is separable in Cost Explorer against a
# $50 credit budget, and so teardown can identify what it owns.
TAGS = {"Project": "Grace", "Environment": "dev"}

# The escalation-queue GSI's partition key value. One value, so the GSI is a
# queue rather than a scan.
PENDING = "PENDING_CASEWORKER"


def case_pk(case_id: str) -> str:
    """The partition key for one case's rows.

    Carries the case id only — never a household name, phone, or address. Same
    rule as span attributes and the JWT `sub` (hard rule 9): this key appears in
    CloudWatch metrics, DynamoDB Streams, and anything that reads the table.
    """
    return f"CASE#{case_id}"


def ledger_sk(at: datetime, seq: int) -> str:
    """Sort key for one ledger entry.

    ISO-8601 **normalized to UTC**, plus a **zero-padded** sequence.

    Every part of that is load-bearing, because DynamoDB sorts the range key
    lexically and the trajectory evals read ledger *position* to assert reads
    precede actions:

    - **UTC normalization.** `LedgerEntry` requires an *aware* datetime but not a
      UTC one, and a non-UTC offset breaks lexical ordering outright: a
      `-05:00` 08:00 is chronologically *after* a UTC 12:00, yet
      `"...T08:00:00-05:00"` string-sorts *before* `"...T12:00:00+00:00"`.
      Confirmed against the real type. Silent misordering of an audit trail.
    - **The naive check stays first.** `.astimezone()` on a naive datetime
      silently assumes local time, turning an unknown value into a confidently
      wrong one. Guard, then convert — never rely on `astimezone` alone.
    - **Zero padding.** An unpadded sequence sorts 10 before 9.
    - **The sequence itself.** `LedgerEntry.at` is `datetime.now(timezone.utc)`
      and one tool call writes `tool_call` then `tool_result` back to back; two
      rows sharing a microsecond would collide and one would silently overwrite
      the other, losing an audit row.
    """
    if at.tzinfo is None:
        raise ValueError("ledger_sk requires a timezone-aware datetime")
    return f"LEDGER#{at.astimezone(timezone.utc).isoformat()}#{seq:06d}"


def escalation_sk(at: datetime) -> str:
    """Sort key for a pending-caseworker row.

    Same UTC normalization and same guard ordering as `ledger_sk`, for the same
    reason — one escalation per case per moment means collisions are unlikely,
    but a non-UTC offset would still sort the queue wrongly.
    """
    if at.tzinfo is None:
        raise ValueError("escalation_sk requires a timezone-aware datetime")
    return f"ESCALATION#{at.astimezone(timezone.utc).isoformat()}"
```

Also create an empty `infra/__init__.py`.

- [x] **Step 4: Run the naming tests**

Run: `.venv/bin/python -m pytest tests/test_infra_naming.py -v`
Expected: PASS, 5 tests.

- [x] **Step 5: Write `infra/provision_dynamodb.py`**

```python
"""Create the `grace-cases` table. Idempotent: re-running is the recovery path.

One table holds both the ledger and the escalation queue, because they are the
same audit trail read two ways.
"""

from __future__ import annotations

import time

import boto3
from botocore.exceptions import ClientError

from infra import naming


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
    # `except ... : pass` left the ledger table with recovery **off** while the
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
    last_error: Exception | None = None
    for _ in range(10):
        try:
            client.update_continuous_backups(
                TableName=naming.TABLE,
                PointInTimeRecoverySpecification={"PointInTimeRecoveryEnabled": True},
            )
            break
        except ClientError as exc:
            if exc.response["Error"]["Code"] != "ContinuousBackupsUnavailableException":
                raise
            last_error = exc
            time.sleep(3)

    status = client.describe_continuous_backups(TableName=naming.TABLE)[
        "ContinuousBackupsDescription"
    ]["PointInTimeRecoveryDescription"]["PointInTimeRecoveryStatus"]
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
```

- [x] **Step 6: Create the table for real and verify it**

```bash
.venv/bin/python -m infra.provision_dynamodb
.venv/bin/python -m infra.provision_dynamodb   # again — must succeed unchanged
aws dynamodb describe-table --table-name grace-cases --region us-east-1 \
  --query '{status:Table.TableStatus,gsi:Table.GlobalSecondaryIndexes[0].IndexName}'
```

Expected: both runs succeed (that is the idempotence check, not a mistake), and
`{"status": "ACTIVE", "gsi": "escalation-queue"}`.

Then verify the durability control actually landed, rather than trusting the script's exit code:

```bash
aws dynamodb describe-continuous-backups --table-name grace-cases --region us-east-1 \
  --query 'ContinuousBackupsDescription.PointInTimeRecoveryDescription.PointInTimeRecoveryStatus' \
  --output text
```

Expected: `ENABLED`. **This check exists because the original version of this script silently left it
`DISABLED` one run in three** — a transient `ContinuousBackupsUnavailableException` in the moments
after `table_exists` returns, swallowed by a bare `except`, exiting 0. Measured across three real
runs: `ENABLED, ENABLED, DISABLED`. The retry-and-verify above is the fix; this command is the
independent confirmation, because "the script said it worked" is exactly the evidence that failed
here.

- [x] **Step 7: Run the whole suite**

Run: `.venv/bin/python -m pytest`
Expected: PASS — **365 tests** (360 baseline + 5 new). Report the real number.

- [x] **Step 8: Commit**

```bash
git add infra/__init__.py infra/naming.py infra/provision_dynamodb.py tests/test_infra_naming.py
git commit -m "feat: grace-cases table and one home for every resource name"
```

---

## Task 2: `DynamoDBCaseStore` and the store factory

The second `CaseStore` implementation. The test strategy is the point of this task: **one test body,
parametrized over both stores.** A separate test file for the new store is how the two drift, and
drift here breaks the trajectory evals in a way that reads as a gate regression.

**Files:**

- Create: `grace/cases/dynamo_store.py`, `grace/store_factory.py`
- Test: `tests/test_dynamo_store.py`, `tests/test_store_factory.py`

**Interfaces:**

- Consumes: `grace.cases.store.CaseStore` (a `@runtime_checkable` Protocol),
  `grace.cases.models.{Case, LedgerEntry, LedgerDetailValue}`,
  `grace.cases.store.load_fixture_cases`, `infra.naming`
- Produces:
  - `grace.cases.dynamo_store.DynamoDBCaseStore(cases: list[Case], table_name: str | None = None, client=None)`
    with `open_cases()`, `get(case_id)`, `append_ledger(entry)`, `ledger(case_id)`,
    and `write_escalation(case_id, reason, question, deadline) -> None`
  - `grace.cases.dynamo_store.to_dynamo(value: LedgerDetailValue) -> object`
  - `grace.store_factory.build_store(cases: list[Case] | None = None) -> CaseStore`

**Why the constructor still takes `cases`.** Household records stay in `fixtures/households.yaml`
(Global Constraints — the table holds ledger and escalation rows only, so there is no second copy of
case data to drift, and hard rule 3 needs no new enforcement surface). This store is a DynamoDB
implementation of the *ledger*, reading cases from the same fixtures the local store does.

- [x] **Step 1: Write the failing parametrized store test**

Create `tests/test_dynamo_store.py`:

```python
"""One test body, both stores.

`InMemoryCaseStore` and `DynamoDBCaseStore` must be behaviourally
interchangeable, because Task 8's trajectory evals read ledger *position* to
assert reads precede actions, and Task 6's `sweep` classifies a case by scanning
for a `renewal_submitted` row. If the two stores returned entries in different
orders, every eval would break in a way that looks like a gate regression rather
than a storage bug.

So the conformance tests below are parametrized over both. A separate test file
for the new store is exactly how the two would drift apart — the same reasoning
that makes `_most_recent` an import shared with the gate rather than a second
dict comprehension.

The DynamoDB store is driven against a local fake client here, not against AWS:
these must run in the fast suite, which is offline. Task 8's real deployed sweep
is what exercises the wire format.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

import pytest

from grace.cases.models import LedgerEntry
from grace.cases.store import CaseStore, InMemoryCaseStore, load_fixture_cases
from grace.cases.dynamo_store import DynamoDBCaseStore, _attr, to_dynamo

TODAY = date(2026, 10, 1)
AT = datetime(2026, 10, 1, 12, 0, 0, tzinfo=timezone.utc)


class FakeTable:
    """The slice of the DynamoDB client the store uses, in memory.

    Deliberately not `moto`: that is a new test dependency for four API calls,
    and Global Constraints keep dependencies minimal.

    **A fake that cannot fail the way the real service fails is worse than no
    fake**, so this one enforces two real DynamoDB behaviours: an `N` value must
    parse as a finite `Decimal` (the real service rejects Infinity and NaN), and
    a Query returns at most one page, signalling more with `LastEvaluatedKey`.
    The page size is tiny so the store's pagination loop is genuinely exercised
    rather than merely present.
    """

    PAGE_SIZE = 3

    def __init__(self) -> None:
        self.items: dict[tuple[str, str], dict] = {}

    def put_item(self, TableName: str, Item: dict) -> dict:
        for key, value in Item.items():
            if isinstance(value, dict) and "N" in value:
                # Real DynamoDB refuses a non-numeric or non-finite N.
                parsed = Decimal(str(value["N"]))
                if not parsed.is_finite():
                    raise TypeError(f"non-finite N reached the wire for {key!r}")
        self.items[(Item["pk"]["S"], Item["sk"]["S"])] = Item
        return {}

    def query(self, **kwargs) -> dict:
        pk = kwargs["ExpressionAttributeValues"][":pk"]["S"]
        prefix = kwargs["ExpressionAttributeValues"].get(":prefix", {}).get("S", "")
        rows = [
            item for (item_pk, sk), item in self.items.items()
            if item_pk == pk and sk.startswith(prefix)
        ]
        rows.sort(key=lambda i: i["sk"]["S"], reverse=not kwargs.get("ScanIndexForward", True))
        start = kwargs.get("ExclusiveStartKey")
        if start is not None:
            after = start["sk"]["S"]
            rows = [r for r in rows if r["sk"]["S"] > after]
        page, rest = rows[: self.PAGE_SIZE], rows[self.PAGE_SIZE :]
        response: dict = {"Items": page}
        if rest:
            response["LastEvaluatedKey"] = {"pk": page[-1]["pk"], "sk": page[-1]["sk"]}
        return response


def _dynamo_store(cases) -> DynamoDBCaseStore:
    return DynamoDBCaseStore(cases, table_name="grace-cases-test", client=FakeTable())


@pytest.fixture(params=["memory", "dynamo"])
def store(request) -> CaseStore:
    cases = load_fixture_cases()
    if request.param == "memory":
        return InMemoryCaseStore(cases)
    return _dynamo_store(cases)


# ---------------------------------------------------------------------------
# Conformance — every assertion below must hold for BOTH implementations
# ---------------------------------------------------------------------------


def test_the_store_satisfies_the_protocol(store):
    """`CaseStore` is `@runtime_checkable` precisely so this is checkable
    rather than merely documented."""
    assert isinstance(store, CaseStore)


def test_all_twelve_fixtures_are_open(store):
    """The demo's arithmetic depends on twelve. A store that dropped one
    would report 8/3 and look plausible."""
    assert len(store.open_cases()) == 12


def test_an_unknown_case_raises_key_error(store):
    """Fail closed: an unreadable case must escalate, never be assumed
    clean (Tasks 3 and 4). Both stores raise the same type so a caller's
    `except KeyError` works against either."""
    with pytest.raises(KeyError):
        store.get("c-999")


def test_the_ledger_starts_empty_and_is_per_case(store):
    """One family's trail must never leak into another's."""
    assert store.ledger("c-001") == []
    store.append_ledger(LedgerEntry(case_id="c-001", at=AT, kind="tool_call",
                                    detail={"tool": "read_case"}))
    assert len(store.ledger("c-001")) == 1
    assert store.ledger("c-002") == []


def test_entries_come_back_in_append_order(store):
    """The property the evals depend on. `read_case` must be readable as
    having preceded `submit_renewal`, and that is positional."""
    for n, tool in enumerate(["read_case", "check_window", "list_documents", "submit_renewal"]):
        store.append_ledger(LedgerEntry(case_id="c-001", at=AT + timedelta(seconds=n),
                                        kind="tool_call", detail={"tool": tool}))
    assert [e.detail["tool"] for e in store.ledger("c-001")] == [
        "read_case", "check_window", "list_documents", "submit_renewal",
    ]


def test_two_entries_with_an_identical_timestamp_both_survive(store):
    """The collision case, asserted on both stores. One tool call writes
    `tool_call` and `tool_result` back to back and they can share a
    microsecond; if the second overwrote the first, an audit row would
    vanish silently."""
    store.append_ledger(LedgerEntry(case_id="c-001", at=AT, kind="tool_call",
                                    detail={"tool": "submit_renewal"}))
    store.append_ledger(LedgerEntry(case_id="c-001", at=AT, kind="tool_result",
                                    detail={"tool": "submit_renewal", "status": "success"}))
    assert [e.kind for e in store.ledger("c-001")] == ["tool_call", "tool_result"]


def test_a_returned_entry_cannot_be_edited(store):
    """`LedgerEntry.detail` is a `MappingProxyType` (Task 2), and the evals
    read the ledger as ground truth — a mutable `detail` would let something
    retroactively change what the eval sees."""
    store.append_ledger(LedgerEntry(case_id="c-001", at=AT, kind="tool_call",
                                    detail={"tool": "read_case"}))
    entry = store.ledger("c-001")[0]
    with pytest.raises(TypeError):
        entry.detail["tool"] = "submit_renewal"  # type: ignore[index]


def test_a_none_trace_id_round_trips_as_none(store):
    """Task 9 writes `trace_id: None` when tracing is not configured, and
    the key must be present rather than absent — a reader must not have to
    guess whether tracing was on. DynamoDB's NULL type must come back as
    `None`, not as the string "None"."""
    store.append_ledger(LedgerEntry(case_id="c-001", at=AT, kind="tool_call",
                                    detail={"tool": "read_case", "trace_id": None}))
    entry = store.ledger("c-001")[0]
    assert "trace_id" in entry.detail
    assert entry.detail["trace_id"] is None


def test_every_scalar_type_round_trips(store):
    """`LedgerDetailValue` is `str | int | float | bool | None`. All five
    must survive storage, because a type that fails at the storage boundary
    fails *after* the action already happened.

    Float is 1.1, not 1.5: an exactly-representable value cannot detect a
    `Decimal(x)`-instead-of-`Decimal(str(x))` conversion, and `approx` would
    hide it even then. Compared exactly.
    """
    store.append_ledger(LedgerEntry(
        case_id="c-001", at=AT, kind="tool_result",
        detail={"s": "text", "i": 42, "f": 1.1, "b": True, "n": None},
    ))
    d = store.ledger("c-001")[0].detail
    assert d["s"] == "text"
    assert d["i"] == 42
    assert float(d["f"]) == 1.1
    assert d["b"] is True
    assert d["n"] is None


# ---------------------------------------------------------------------------
# DynamoDB-specific: the serialization trap
# ---------------------------------------------------------------------------


def test_a_float_becomes_a_decimal_not_a_float():
    """DynamoDB has no float type and boto3's serializer *raises* on one
    rather than coercing.

    This matters more than it looks. A float in `detail` would fail the write
    **after** the underlying action already succeeded — the family's renewal
    filed, the audit row lost. That is hard rule 6 inverted, and it is the
    same failure `str(channel.send(...))` was added to prevent in Task 4:
    a `Channel` is a plain Protocol, so a real SNS implementation returning a
    boto3 response shape is exactly how a non-scalar reaches the ledger.

    **Uses 1.1, not 1.5, deliberately.** `Decimal(1.5) == Decimal("1.5")` is
    True because 1.5 is exactly representable in binary — so 1.5 cannot tell
    `Decimal(x)` apart from `Decimal(str(x))` and a test written with it
    passes against the wrong conversion. `Decimal(1.1)` is
    `1.10000000000000008881784197001252323389053344726562 5`, which does not
    equal `Decimal("1.1")`. Verified both.
    """
    from decimal import Decimal

    assert isinstance(to_dynamo(1.1), Decimal)
    assert to_dynamo(1.1) == Decimal("1.1")
    assert to_dynamo(1.1) != Decimal(1.1)  # the binary-noise form


def test_a_bool_serializes_to_BOOL_and_an_int_to_N():
    """`isinstance(True, int)` is True in Python, so a serializer that checks
    `int` before `bool` silently stores `True` as the number 1.

    **Asserted at the wire shape, not the return value.** `to_dynamo(True) is
    True` passes against the buggy int-first ordering — verified — because the
    value comes back unchanged either way. The distinction only exists in the
    attribute shape, so that is where it must be tested.
    """
    assert _attr(True) == {"BOOL": True}
    assert _attr(False) == {"BOOL": False}
    assert _attr(1) == {"N": "1"}
    assert _attr(0) == {"N": "0"}


def test_a_non_finite_float_is_refused():
    """DynamoDB rejects Infinity and NaN, and `Decimal("Infinity")` would
    round-trip into an `int("Infinity")` ValueError on read.

    The deeper reason: CLAUDE.md's Task 1 finding records that a NaN threshold
    silently disables a comparison, because every comparison against NaN is
    False. A NaN reaching an audit row is the same family of bug — it reads
    back as a number and behaves like nothing.
    """
    for value in (float("inf"), float("-inf"), float("nan")):
        with pytest.raises(TypeError):
            to_dynamo(value)


def test_a_non_scalar_is_refused_loudly():
    """Anything outside `LedgerDetailValue` must raise here rather than
    reaching the wire and failing inside botocore with an unrelated message."""
    with pytest.raises(TypeError):
        to_dynamo({"nested": "dict"})  # type: ignore[arg-type]


def test_the_ledger_query_follows_every_page():
    """A DynamoDB Query returns at most 1MB and signals more with
    `LastEvaluatedKey`.

    At ~20-40 small rows per case this cannot bite today, but the failure mode
    is **silent truncation of an audit trail**: `sweep` classifies a case by
    scanning for a `renewal_submitted` row, so a dropped page would report a
    filed renewal as unfiled with no error anywhere — the same class as the
    sort-key defect Task 1 found.

    `FakeTable` pages at a small size so the loop is genuinely exercised. A
    pagination loop that never iterates in any test is not tested.
    """
    store = _dynamo_store(load_fixture_cases())
    for n in range(7):
        store.append_ledger(LedgerEntry(case_id="c-001", at=AT + timedelta(seconds=n),
                                        kind="tool_call", detail={"tool": f"t{n}"}))
    assert [e.detail["tool"] for e in store.ledger("c-001")] == [f"t{n}" for n in range(7)]


def test_an_escalation_row_lands_on_the_queue_index():
    """The pending-caseworker row carries `status` and `escalated_at`, which
    are the escalation-queue GSI's keys. Only escalation rows carry them, so
    the index is sparse — it holds the queue, not a filtered scan of every
    ledger row."""
    store = _dynamo_store(load_fixture_cases())
    store.write_escalation("c-011", reason="material_income_change: Income moved 30.0%",
                           question="Which income figure applies?", deadline="2026-10-31")
    row = next(i for (pk, sk), i in store._client.items.items() if sk.startswith("ESCALATION#"))
    assert row["status"]["S"] == "PENDING_CASEWORKER"
    assert "escalated_at" in row
    assert row["pk"]["S"] == "CASE#c-011"
```

- [x] **Step 2: Run it to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_dynamo_store.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'grace.cases.dynamo_store'`. Note the
`memory`-parametrized cases fail on the same import, which is expected: the import is at module
scope.

- [x] **Step 3: Write `grace/cases/dynamo_store.py`**

```python
"""DynamoDB case store. The deployed ledger.

Behaviourally interchangeable with `InMemoryCaseStore` — that is a requirement,
not an aspiration. Task 8's trajectory evals read ledger *position* to assert
reads precede actions, and `sweep` classifies a case by scanning for a
`renewal_submitted` row, so a different ordering here would break both in a way
that reads as a gate regression. `tests/test_dynamo_store.py` parametrizes one
test body over both stores for exactly that reason.

**Household records are not stored here.** Cases come from
`fixtures/households.yaml`, the same source the local store reads. This table
holds the ledger and the escalation queue, so there is no second copy of case
data to drift and hard rule 3 (synthetic data only) needs no new enforcement
surface.

**Error posture, and it differs deliberately from Task 9's.** Read failures and
ledger-write failures both propagate. An unreadable case must escalate rather
than be assumed clean (Tasks 3 and 4), and an action that happened with no audit
row is worse than a visible error — Step Functions' Catch converts either into
an escalation row. This is the *opposite* of `_current_trace_id`'s fail-open
handling, for the reason Task 9 stated: a trace ID is observability and losing it
harms nobody, while a ledger row is evidence.
"""

from __future__ import annotations

import itertools
import math
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any

import boto3

from grace.cases.models import Case, LedgerDetailValue, LedgerEntry
from infra import naming


def to_dynamo(value: LedgerDetailValue) -> Any:
    """Convert one `LedgerDetailValue` to something DynamoDB accepts.

    `bool` is checked **before** `int` on purpose: `isinstance(True, int)` is
    True in Python, so the obvious ordering silently stores `True` as the number
    1 and the ledger reads a boolean flag back as an integer.

    `float` becomes `Decimal` because DynamoDB has no float type and boto3's
    serializer *raises* rather than coercing. Left unhandled, that raise lands
    **after** the underlying action already succeeded — the renewal filed, the
    audit row lost, which is hard rule 6 inverted. Same failure shape as a
    `Channel` returning a boto3 dict (Task 4).

    `Decimal(str(value))`, never `Decimal(value)`: the latter captures binary
    float noise (`Decimal(1.1)` is `1.1000000000000000888…`).

    Non-finite floats are refused outright. DynamoDB rejects Infinity and NaN,
    and `Decimal("Infinity")` would round-trip into an `int("Infinity")`
    ValueError on read — but the sharper reason is the one CLAUDE.md already
    records from Task 1: a NaN silently disables every comparison it appears in,
    because comparisons against NaN are all False. A NaN in an audit row reads
    back as a number and behaves like nothing.
    """
    if value is None or isinstance(value, bool) or isinstance(value, str):
        return value
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise TypeError(
                f"LedgerEntry.detail values must be finite numbers, got {value!r}"
            )
        return Decimal(str(value))
    raise TypeError(f"LedgerEntry.detail values must be JSON-safe scalars, got {value!r}")


def _attr(value: LedgerDetailValue) -> dict[str, Any]:
    """Wrap a scalar in DynamoDB's attribute-value shape."""
    converted = to_dynamo(value)
    if converted is None:
        return {"NULL": True}
    if isinstance(converted, bool):
        return {"BOOL": converted}
    if isinstance(converted, str):
        return {"S": converted}
    return {"N": str(converted)}


def _from_attr(attr: dict[str, Any]) -> LedgerDetailValue:
    """Read a scalar back. `NULL` must become `None`, never the string "None" —
    Task 9 writes `trace_id: None` when tracing is off, and a reader must be
    able to tell that apart from a real value."""
    if attr.get("NULL"):
        return None
    if "BOOL" in attr:
        return bool(attr["BOOL"])
    if "S" in attr:
        return str(attr["S"])
    if "N" in attr:
        raw = str(attr["N"])
        return int(raw) if "." not in raw and "e" not in raw.lower() else float(raw)
    raise TypeError(f"unreadable ledger attribute: {attr!r}")


class DynamoDBCaseStore:
    """One table, two row kinds: ledger entries and escalation rows."""

    def __init__(self, cases: list[Case], table_name: str | None = None, client=None) -> None:
        ids = [c.case_id for c in cases]
        duplicates = sorted({i for i in ids if ids.count(i) > 1})
        if duplicates:
            # Same refusal as the in-memory store: keying by id would silently
            # drop a duplicate, shrinking the caseload with no error while the
            # sweep still reported success.
            raise ValueError(f"duplicate case ids: {duplicates}")
        self._cases = {c.case_id: c for c in cases}
        self._table = table_name or naming.TABLE
        self._client = client or boto3.client("dynamodb", region_name=naming.REGION)
        # Per-case monotonic sequence, making the sort key collision-proof
        # within this process. Two entries sharing a microsecond is routine —
        # one tool call writes `tool_call` then `tool_result` — and without the
        # sequence the second would overwrite the first.
        self._seq: dict[str, itertools.count] = {}

    def open_cases(self) -> list[Case]:
        return list(self._cases.values())

    def get(self, case_id: str) -> Case:
        if case_id not in self._cases:
            raise KeyError(f"No such case: {case_id}")
        return self._cases[case_id]

    def append_ledger(self, entry: LedgerEntry) -> None:
        if entry.case_id not in self._cases:
            # A ledger row for an unknown case is a typo at the call site, not a
            # new case. Failing loudly beats opening a phantom bucket that
            # `ledger()` would later report as an innocent empty list.
            raise KeyError(f"Cannot append ledger entry for unknown case: {entry.case_id}")
        seq = next(self._seq.setdefault(entry.case_id, itertools.count(1)))
        item = {
            "pk": {"S": naming.case_pk(entry.case_id)},
            "sk": {"S": naming.ledger_sk(entry.at, seq)},
            "case_id": {"S": entry.case_id},
            "at": {"S": entry.at.isoformat()},
            "kind": {"S": entry.kind},
        }
        for key, value in entry.detail.items():
            item[f"d_{key}"] = _attr(value)
        self._client.put_item(TableName=self._table, Item=item)

    def ledger(self, case_id: str) -> list[LedgerEntry]:
        """Every ledger row for one case, in append order.

        **Paginated, because a Query returns at most 1MB** and signals more with
        `LastEvaluatedKey`. At ~20-40 small rows per case that limit cannot bite
        today, but the failure mode is silent truncation of an audit trail:
        `sweep` classifies a case by scanning for a `renewal_submitted` row, so a
        dropped page would report a filed renewal as unfiled with no error
        anywhere. Same class of defect as an unsorted key.

        `ScanIndexForward=True` is chronological, matching `InMemoryCaseStore`'s
        append order — the evals read position, so this is load-bearing.
        """
        entries: list[LedgerEntry] = []
        start_key: dict | None = None
        while True:
            kwargs: dict[str, Any] = {
                "TableName": self._table,
                "KeyConditionExpression": "pk = :pk AND begins_with(sk, :prefix)",
                "ExpressionAttributeValues": {
                    ":pk": {"S": naming.case_pk(case_id)},
                    ":prefix": {"S": "LEDGER#"},
                },
                "ScanIndexForward": True,
            }
            if start_key is not None:
                kwargs["ExclusiveStartKey"] = start_key
            response = self._client.query(**kwargs)
            for item in response.get("Items", []):
                detail = {
                    key[2:]: _from_attr(value)
                    for key, value in item.items()
                    if key.startswith("d_")
                }
                entries.append(
                    LedgerEntry(
                        case_id=str(item["case_id"]["S"]),
                        at=datetime.fromisoformat(str(item["at"]["S"])),
                        kind=str(item["kind"]["S"]),
                        detail=detail,
                    )
                )
            start_key = response.get("LastEvaluatedKey")
            if not start_key:
                return entries

    def write_escalation(
        self, case_id: str, reason: str, question: str, deadline: str
    ) -> None:
        """Record that this case is waiting for a human.

        `status` and `escalated_at` are the escalation-queue GSI's keys, and only
        these rows carry them — so the index is a sparse queue rather than a
        filtered scan over every ledger row. Plan 3's dashboard reads it
        directly.
        """
        at = datetime.now(timezone.utc)
        self._client.put_item(
            TableName=self._table,
            Item={
                "pk": {"S": naming.case_pk(case_id)},
                "sk": {"S": naming.escalation_sk(at)},
                "case_id": {"S": case_id},
                "status": {"S": naming.PENDING},
                "escalated_at": {"S": at.isoformat()},
                "reason": {"S": reason},
                "question": {"S": question},
                "deadline": {"S": deadline},
            },
        )
```

- [x] **Step 4: Run the store tests**

Run: `.venv/bin/python -m pytest tests/test_dynamo_store.py -v`
Expected: PASS — 22 tests (9 conformance × 2 stores, plus 4 DynamoDB-specific).

- [x] **Step 5: Write the failing store-factory test**

Create `tests/test_store_factory.py`:

```python
"""Which store this process uses, decided in one place.

Without a factory the `GRACE_STORE` branch ends up duplicated in the entrypoint
and anywhere else that needs a store, and the two copies disagree about the
default. The default must be in-memory: the fast suite is offline, and a default
of "dynamo" would make 360 passing tests suddenly require AWS.
"""

from __future__ import annotations

import pytest

from grace.cases.dynamo_store import DynamoDBCaseStore
from grace.cases.store import CaseStore, InMemoryCaseStore
from grace.store_factory import build_store


def test_the_default_is_in_memory(monkeypatch):
    """No env var means offline. The fast suite depends on this."""
    monkeypatch.delenv("GRACE_STORE", raising=False)
    assert isinstance(build_store(), InMemoryCaseStore)


def test_dynamo_is_selected_explicitly(monkeypatch):
    monkeypatch.setenv("GRACE_STORE", "dynamodb")
    store = build_store()
    assert isinstance(store, DynamoDBCaseStore)


def test_an_unrecognized_value_raises_rather_than_defaulting(monkeypatch):
    """A typo'd `GRACE_STORE=dynamo` must not silently fall back to
    in-memory in the deployed runtime — the ledger would look empty to the
    dashboard while the sweep reported success, and nothing would say why."""
    monkeypatch.setenv("GRACE_STORE", "dynamo")
    with pytest.raises(ValueError, match="GRACE_STORE"):
        build_store()


def test_whatever_it_returns_satisfies_the_protocol(monkeypatch):
    monkeypatch.delenv("GRACE_STORE", raising=False)
    assert isinstance(build_store(), CaseStore)
```

- [x] **Step 6: Run it to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_store_factory.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'grace.store_factory'`.

- [x] **Step 7: Write `grace/store_factory.py`**

```python
"""Which `CaseStore` this process uses.

One place, so the `GRACE_STORE` branch is not duplicated between the entrypoint
and anything else that needs a store — two copies would eventually disagree
about the default.

The default is in-memory on purpose: the fast suite runs offline, and defaulting
to DynamoDB would make 360 passing tests require AWS credentials.
"""

from __future__ import annotations

import os

from grace.cases.models import Case
from grace.cases.store import CaseStore, InMemoryCaseStore, load_fixture_cases

_IN_MEMORY = "memory"
_DYNAMODB = "dynamodb"


def build_store(cases: list[Case] | None = None) -> CaseStore:
    """Build the store this process should use.

    An unrecognized `GRACE_STORE` raises rather than falling back. A typo'd
    `GRACE_STORE=dynamo` in the deployed runtime would otherwise write the
    ledger to memory and discard it at process exit — the dashboard would show
    an empty ledger while the sweep reported success, with nothing anywhere
    saying why. Failing at startup is the only version of that a human notices.
    """
    cases = cases if cases is not None else load_fixture_cases()
    kind = os.getenv("GRACE_STORE", _IN_MEMORY).strip().lower()
    if kind == _IN_MEMORY:
        return InMemoryCaseStore(cases)
    if kind == _DYNAMODB:
        # Imported here, not at module scope: the in-memory path must not
        # require boto3 to be importable, and this keeps the fast suite's import
        # graph unchanged.
        from grace.cases.dynamo_store import DynamoDBCaseStore

        return DynamoDBCaseStore(cases)
    raise ValueError(
        f"GRACE_STORE must be {_IN_MEMORY!r} or {_DYNAMODB!r}, got {kind!r}"
    )
```

- [x] **Step 8: Run the factory tests**

Run: `.venv/bin/python -m pytest tests/test_store_factory.py -v`
Expected: PASS, 4 tests.

- [x] **Step 9: Run the whole suite**

Run: `.venv/bin/python -m pytest`
Expected: PASS — **391 tests** (365 + 22 + 4). Report the real number; every prior estimate in
Plan 1 proved stale once written.

- [x] **Step 10: Verify the new store against the real table, once**

```bash
GRACE_STORE=dynamodb .venv/bin/python -c "
from grace.store_factory import build_store
from grace.cases.models import LedgerEntry
from datetime import datetime, timezone
s = build_store()
s.append_ledger(LedgerEntry(case_id='c-001', at=datetime.now(timezone.utc),
                            kind='tool_call', detail={'tool':'read_case','trace_id':None}))
rows = s.ledger('c-001')
print('rows:', [(e.kind, dict(e.detail)) for e in rows])
assert rows and rows[-1].detail['trace_id'] is None
print('OK: NULL round-tripped as None against the real table')
"
```

Expected: the row prints and the assertion passes. This is the one check the fake client cannot
make — that the wire format is actually accepted by DynamoDB. Leave the row in place; it is
synthetic and harmless.

- [x] **Step 11: Commit**

```bash
git add grace/cases/dynamo_store.py grace/store_factory.py \
        tests/test_dynamo_store.py tests/test_store_factory.py
git commit -m "feat: DynamoDB ledger behind the existing CaseStore protocol"
```

---

## Task 3: Conditional telemetry setup

Small, and it must land before the entrypoint imports it. The whole content of this task is knowing
when *not* to act.

**Files:**

- Create: `grace/observability.py`
- Test: `tests/test_observability.py`

**Interfaces:**

- Consumes: nothing from earlier tasks
- Produces: `grace.observability.setup_telemetry() -> None`,
  `grace.observability.REDACTION_TOKEN: str`,
  `grace.observability.redaction_is_configured(env: Mapping[str, str] | None = None) -> bool`

- [x] **Step 1: Write the failing test**

Create `tests/test_observability.py`:

```python
"""Telemetry setup, and the one span-redaction invariant.

`StrandsTelemetry()` hijacks the global tracer provider as a constructor side
effect. On AgentCore Runtime that replaces a provider Runtime already
configured, so the setup must be conditional — the interesting assertion is that
it does *nothing* when Runtime is present.
"""

from __future__ import annotations

from grace import observability


def test_telemetry_setup_is_skipped_on_agentcore_runtime(monkeypatch):
    """`StrandsTelemetry.__init__` calls `set_tracer_provider` as a side
    effect. Runtime instruments the process itself, so constructing it there
    replaces a working provider with a second one. `AGENT_OBSERVABILITY_ENABLED`
    is set by Runtime, so its presence means "hands off".

    Asserted by making the import itself fail: if `setup_telemetry` tried to
    construct the telemetry object, this test would raise rather than pass.
    """
    monkeypatch.setenv("AGENT_OBSERVABILITY_ENABLED", "true")

    def explode(*args, **kwargs):  # pragma: no cover — must never be called
        raise AssertionError("setup_telemetry must not touch telemetry on Runtime")

    monkeypatch.setattr("strands.telemetry.StrandsTelemetry", explode)
    observability.setup_telemetry()  # must return without raising


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


def test_the_redaction_token_keeps_its_trailing_equals():
    """Hard rule 8. The token's value lists what to leave *unredacted*, so an
    empty value means "redact everything" — and the trailing `=` is what makes
    it an empty value rather than an absent key. Absence of the token disables
    redaction entirely and exports the full household record to CloudWatch.
    """
    assert observability.REDACTION_TOKEN.endswith("gen_ai_unredacted_attributes=")


def test_redaction_is_detected_as_configured_or_not():
    """A deployed runtime must be checkable, not assumed. `.env.example`
    having the token says nothing about what Runtime actually has set.

    The third case is the one that matters most and the one a substring check
    gets wrong: a token that is *present* but carries an allowlist reports
    redaction enabled and still exports the household record. Verified against
    the real `Tracer` — `_redaction_enabled` is True there while
    `gen_ai.input.messages` and `gen_ai.output.messages` are both unredacted.
    """
    # Grace's policy: token present, value empty -> redact everything.
    assert observability.redaction_is_configured(
        {"OTEL_SEMCONV_STABILITY_OPT_IN":
         "gen_ai_latest_experimental,gen_ai_unredacted_attributes="}
    )
    # The documented trap: the experimental semconv alone protects nothing.
    assert not observability.redaction_is_configured(
        {"OTEL_SEMCONV_STABILITY_OPT_IN": "gen_ai_latest_experimental"}
    )
    assert not observability.redaction_is_configured({})
    # Present but allowlisting the two attributes that carry the household
    # record. This is hard rule 8 defeated while the token is technically there.
    assert not observability.redaction_is_configured(
        {"OTEL_SEMCONV_STABILITY_OPT_IN":
         "gen_ai_unredacted_attributes=gen_ai.input.messages;gen_ai.output.messages"}
    )
    # A single allowlisted attribute is still a hole.
    assert not observability.redaction_is_configured(
        {"OTEL_SEMCONV_STABILITY_OPT_IN": "gen_ai_unredacted_attributes=gen_ai.system_instructions"}
    )


def test_the_redaction_check_agrees_with_the_sdks_own_tracer():
    """The guard and the SDK must not disagree about what a value means.

    `redaction_is_configured` is Grace's gate; `Tracer` is what actually
    redacts. If they diverge, the gate passes a configuration that leaks — so
    this drives the real `Tracer` and asserts the two agree on every case.
    """
    import os as _os

    from strands.telemetry import Tracer

    cases = [
        ("gen_ai_latest_experimental,gen_ai_unredacted_attributes=", True),
        ("gen_ai_latest_experimental", False),
        ("gen_ai_unredacted_attributes=gen_ai.input.messages;gen_ai.output.messages", False),
    ]
    previous = _os.environ.get("OTEL_SEMCONV_STABILITY_OPT_IN")
    try:
        for value, grace_says_safe in cases:
            _os.environ["OTEL_SEMCONV_STABILITY_OPT_IN"] = value
            tracer = Tracer()
            sdk_redacts_messages = tracer._redaction_enabled and not any(
                tracer._is_attribute_unredacted(name)
                for name in ("gen_ai.input.messages", "gen_ai.output.messages")
            )
            assert observability.redaction_is_configured({"OTEL_SEMCONV_STABILITY_OPT_IN": value}) == (
                grace_says_safe
            ), value
            assert sdk_redacts_messages == grace_says_safe, value
    finally:
        if previous is None:
            _os.environ.pop("OTEL_SEMCONV_STABILITY_OPT_IN", None)
        else:
            _os.environ["OTEL_SEMCONV_STABILITY_OPT_IN"] = previous
```

- [x] **Step 2: Run it to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_observability.py -v`
Expected: FAIL — `ImportError: cannot import name 'observability' from 'grace'`.

- [x] **Step 3: Write `grace/observability.py`**

```python
"""Trace exporter setup, and the span-redaction invariant.

Two things here, both about knowing when not to act.

**Skip telemetry setup on AgentCore Runtime.** `StrandsTelemetry()` calls
`trace_api.set_tracer_provider(...)` as a constructor side effect. Runtime
configures the OTEL environment and the global provider itself, so constructing
it there replaces a working provider with a second one. `AGENT_OBSERVABILITY_
ENABLED` is set by Runtime, so its presence means hands off.

**Exporters are opt-in.** Off Runtime, a provider with no exporter creates spans
and silently drops them, so `setup_console_exporter()` is what makes local traces
visible at all.

Failure here is swallowed, matching the SDK's own stance that failed exporter
configuration is logged rather than raised — and matching Task 9's reasoning
about `_current_trace_id`: failing closed on an *observability* question harms the
family, because nothing relies on a trace to decide anything. Lose the traces;
keep the sweep.
"""

from __future__ import annotations

import logging
import os
from typing import Mapping

logger = logging.getLogger(__name__)

# Hard rule 8. The value lists what to leave *unredacted*, so the empty value
# after the `=` means "redact everything". The trailing `=` is load-bearing:
# without the token at all, every prompt and tool result — the full household
# record — exports to CloudWatch verbatim.
REDACTION_TOKEN = "gen_ai_latest_experimental,gen_ai_unredacted_attributes="

_UNREDACTED_PREFIX = "gen_ai_unredacted_attributes="
_ENV_KEY = "OTEL_SEMCONV_STABILITY_OPT_IN"


def redaction_is_configured(env: Mapping[str, str] | None = None) -> bool:
    """Whether span redaction is on **and covers everything**, in this environment.

    Checkable rather than assumed, because `.env.example` carrying the token says
    nothing about what a deployed Runtime has set.

    **Presence of the token is not the same claim as content being redacted, and
    conflating the two defeats hard rule 8.** Read the SDK's own parsing
    (`strands.telemetry.tracer`, ~line 131): it takes the first token starting
    with `gen_ai_unredacted_attributes=`, sets `_redaction_enabled` from its mere
    *presence*, then compiles everything after the `=` into an **allowlist of
    attributes to leave unredacted**. Measured against the real `Tracer`:

        gen_ai_latest_experimental
            -> enabled=False, messages exported
        gen_ai_latest_experimental,gen_ai_unredacted_attributes=
            -> enabled=True,  messages REDACTED   (Grace's policy)
        gen_ai_unredacted_attributes=gen_ai.input.messages;gen_ai.output.messages
            -> enabled=True,  messages exported

    The third case passes a substring check, reports redaction "enabled", and
    exports the full household record. So this function requires the value to be
    **empty** — Grace's policy is redact everything, so any allowlist entry is a
    hole, not a configuration. The trailing `=` matters because it is what makes
    the value empty rather than absent; emptiness is the property, not the
    character.
    """
    raw = (env if env is not None else os.environ).get(_ENV_KEY, "")
    # Split the way the SDK splits, so the two cannot disagree about what a
    # given value means.
    for token in (t.strip() for t in raw.split(",")):
        if token.startswith(_UNREDACTED_PREFIX):
            return token.partition("=")[2].strip() == ""
    return False


def setup_telemetry() -> None:
    """Attach a trace exporter for local runs only."""
    if os.getenv("AGENT_OBSERVABILITY_ENABLED"):
        # Runtime already did this. Constructing StrandsTelemetry here would
        # replace its provider.
        return
    try:
        from strands.telemetry import StrandsTelemetry

        StrandsTelemetry().setup_console_exporter()
    except Exception:  # noqa: BLE001 — telemetry must not break the run
        logger.warning("trace exporter setup failed; continuing without traces",
                       exc_info=True)
```

- [x] **Step 4: Run the tests**

Run: `.venv/bin/python -m pytest tests/test_observability.py -v`
Expected: PASS, 5 tests.

- [x] **Step 5: Run the whole suite**

Run: `.venv/bin/python -m pytest`
Expected: PASS — **396 tests**. Report the real number.

- [x] **Step 6: Commit**

```bash
git add grace/observability.py tests/test_observability.py
git commit -m "feat: conditional telemetry setup that stays out of Runtime's way"
```

---

## Task 4: The Runtime entrypoint

The one genuinely new piece of logic, and where Plan 1's findings either hold or quietly break.
Read §5 of the spec before starting.

**Files:**

- Create: `grace/entrypoint.py`
- Modify: `grace/run.py` — promote four private helpers to public names (see Step 1)
- Test: `tests/test_entrypoint.py`

**Interfaces:**

- Consumes: `grace.store_factory.build_store`, `grace.graph.build_case_graph`,
  `grace.observability.setup_telemetry`, `grace.tools.action.TranscriptChannel`,
  `grace.cases.dynamo_store.DynamoDBCaseStore.write_escalation`,
  and from `grace.run`: `gate_reason`, `renewal_filed`, `outreach_sent`, `deliberation_note`
- Produces:
  - `grace.entrypoint.process_case(payload: dict, store=None, channel=None) -> dict`
  - `grace.entrypoint.CaseOutcome` — `TypedDict` with `status`, `case_id`, and the per-status fields
  - `grace.entrypoint.invoke(payload: dict) -> dict` — the Runtime handler

### Why `grace/run.py` is modified, and why that is not a violation

Global Constraints forbid editing `graph.py`, `swarm.py`, `authority.py`, and `steering.py` — the
decision path. `run.py` is the local CLI, not the decision path, and this change is a **rename
only**: `_gate_reason` → `gate_reason`, `_renewal_filed` → `renewal_filed`, `_outreach_sent` →
`outreach_sent`, `_deliberation_note` → `deliberation_note`, with module-level aliases kept for the
old names so no existing test breaks.

The alternative is worse. The entrypoint must classify a case *identically* to `sweep`, and Task 7
established what a second implementation of `_deliberation_note` costs: its failure mode is printing
the advocate's unchecked argument to a caseworker as though a verifier had confirmed it. Importing a
private name across modules is a smell; reimplementing this logic is a safety defect. Promote the
names.

**The aliases are mandatory, not politeness.** Six existing tests call `run._deliberation_note(...)`
directly — two in `tests/test_swarm.py`, four in `tests/test_graph.py` — and Global Constraints
forbid editing an existing test file. Verified by grep before this task was written:

```text
tests/test_swarm.py:300,320       run._deliberation_note(...)
tests/test_graph.py:1600,1623,1643,1781,1782  run._deliberation_note(...)
```

So `_deliberation_note = deliberation_note` must exist, or Task 3's green suite goes red for a reason
that has nothing to do with the entrypoint. The other three helpers have no external callers today,
but keep their aliases too — symmetry costs one line each and the next task to reach for one will not
have to check.

- [ ] **Step 1: Promote the four helpers in `grace/run.py`**

Rename the four functions and add aliases immediately after each definition. Example for the first:

```python
def gate_reason(store: CaseStore, case_id: str, today: date) -> str | None:
    """Why this case needs a human, or `None` if it does not.

    Public because `grace/entrypoint.py` must classify a deployed case
    identically to the local sweep. A second implementation would drift, and
    Task 7 documented what that costs when the drifting function is the one
    choosing what a caseworker reads.
    """
    # ... body unchanged ...


# Retained so nothing that already imports the private name breaks.
_gate_reason = gate_reason
```

Do the same for `renewal_filed`, `outreach_sent`, and `deliberation_note`. **Do not change any
function body.** Update the four internal call sites inside `sweep` to the new names.

- [ ] **Step 2: Confirm nothing broke**

Run: `.venv/bin/python -m pytest`
Expected: PASS — **396 tests**, unchanged from Task 3. A rename that changes a count means a body
changed too.

- [ ] **Step 3: Write the failing entrypoint test**

Create `tests/test_entrypoint.py`:

```python
"""The deployed entrypoint. One case per invocation, three possible outcomes.

Every test here uses a fake graph. The real graph is exercised by Task 8's
deployed sweep; what needs asserting here is the *contract* — that each case
lands in exactly one bucket, that an interrupt is never resumed, and that the
classification matches `sweep`'s rather than being re-derived.
"""

from __future__ import annotations

from datetime import date

import pytest
from strands.multiagent.base import Status

from grace import entrypoint
from grace.cases.store import InMemoryCaseStore, load_fixture_cases
from grace.tools.action import TranscriptChannel

TODAY = "2026-10-01"


class FakeGraph:
    """Stands in for `build_case_graph`'s result."""

    def __init__(self, status=Status.COMPLETED, interrupts=(), results=None):
        self._status = status
        self._interrupts = list(interrupts)
        self._results = results or {}
        self.calls = 0

    def __call__(self, task):
        self.calls += 1
        return self

    @property
    def status(self):
        return self._status

    @property
    def interrupts(self):
        return self._interrupts

    @property
    def results(self):
        return self._results


class FakeInterrupt:
    def __init__(self, message):
        self.id = "int-1"
        self.name = "authority_gate"
        self.reason = {"message": message}


def _payload(case_id="c-001"):
    return {"case_id": case_id, "today": TODAY}


def test_a_clean_case_with_a_filed_renewal_is_acted(monkeypatch):
    store = InMemoryCaseStore(load_fixture_cases())
    monkeypatch.setattr(entrypoint, "build_case_graph",
                        lambda *a, **k: FakeGraph())
    monkeypatch.setattr(entrypoint, "renewal_filed", lambda *a: True)
    out = entrypoint.process_case(_payload("c-001"), store=store,
                                  channel=TranscriptChannel())
    assert out["status"] == "acted"
    assert out["filed"] is True
    assert out["case_id"] == "c-001"


def test_an_interrupt_is_never_resumed(monkeypatch):
    """The safety property this design turns on.

    Task 6 proved that resuming with a truthy response *approves* the blocked
    tool: confirmed against the real executor, "Escalate.", "no, hold this
    one", and "needs review" all resumed and filed a renewal for `c-010`, a
    household missing a required document. The deployed path has no human to
    ask, so it must never resume at all — a path that cannot resume cannot be
    talked into filing.

    Asserted by call count: the graph must be invoked exactly once.
    """
    store = InMemoryCaseStore(load_fixture_cases())
    graph = FakeGraph(status=Status.INTERRUPTED,
                      interrupts=[FakeInterrupt("Cannot file: document missing")])
    monkeypatch.setattr(entrypoint, "build_case_graph", lambda *a, **k: graph)
    out = entrypoint.process_case(_payload("c-010"), store=store,
                                  channel=TranscriptChannel())
    assert graph.calls == 1, "the deployed path must never resume an interrupt"
    assert out["status"] == "escalated"


def test_the_gates_typed_reason_beats_a_generic_run_status(monkeypatch):
    """Task 7's finding. A FAILED node does not stop the graph, so `decide`
    still runs and `evaluate()` still has a specific verdict — but a naive
    implementation reports "the run ended in state 'failed'" and drops
    `material_income_change: Income moved 30.0%`, the one fact the caseworker
    needed."""
    store = InMemoryCaseStore(load_fixture_cases())
    monkeypatch.setattr(entrypoint, "build_case_graph",
                        lambda *a, **k: FakeGraph(status=Status.FAILED))
    out = entrypoint.process_case(_payload("c-011"), store=store,
                                  channel=TranscriptChannel())
    assert out["status"] == "escalated"
    assert "material_income_change" in out["reason"]
    assert "failed" not in out["reason"].lower()


def test_an_escalating_case_reports_no_filing(monkeypatch):
    """Hard rule 6, at the contract boundary: the payload must never claim a
    renewal that the ledger does not confirm."""
    store = InMemoryCaseStore(load_fixture_cases())
    monkeypatch.setattr(entrypoint, "build_case_graph",
                        lambda *a, **k: FakeGraph())
    out = entrypoint.process_case(_payload("c-012"), store=store,
                                  channel=TranscriptChannel())
    assert out["status"] == "escalated"
    assert out.get("filed") is not True


def test_a_raising_graph_becomes_an_error_not_a_silent_pass(monkeypatch):
    """Fail closed. An exception must not be reported as a handled case."""
    store = InMemoryCaseStore(load_fixture_cases())

    def boom(*a, **k):
        raise RuntimeError("bedrock exploded")

    monkeypatch.setattr(entrypoint, "build_case_graph", boom)
    out = entrypoint.process_case(_payload("c-001"), store=store,
                                  channel=TranscriptChannel())
    assert out["status"] == "error"
    assert "bedrock exploded" in out["detail"]


def test_every_outcome_carries_exactly_one_status(monkeypatch):
    """Task 6's partition rule. A case counted twice, or counted nowhere,
    makes "nine handled alone, three escalated" arithmetic that does not add
    up while each count still looks plausible."""
    store = InMemoryCaseStore(load_fixture_cases())
    monkeypatch.setattr(entrypoint, "build_case_graph", lambda *a, **k: FakeGraph())
    for case_id in ("c-001", "c-010", "c-011", "c-012"):
        out = entrypoint.process_case(_payload(case_id), store=store,
                                      channel=TranscriptChannel())
        assert out["status"] in {"acted", "escalated", "error"}
        assert out["case_id"] == case_id


def test_a_missing_case_id_is_an_error_not_a_crash():
    """The payload comes from Step Functions. A malformed one must produce a
    reportable outcome, not an unhandled exception that Step Functions has to
    interpret."""
    out = entrypoint.process_case({"today": TODAY})
    assert out["status"] == "error"
    assert "case_id" in out["detail"]


def test_a_bad_today_is_refused_rather_than_defaulted():
    """A silent `date.today()` fallback evaluates every renewal window against
    the wrong day — and fixture c-002 flips from `in_grace` to `closed` on
    2026-10-31, turning 9/3 into 8/4 with no error."""
    out = entrypoint.process_case({"case_id": "c-001", "today": "not-a-date"})
    assert out["status"] == "error"


def test_the_default_today_is_pinned():
    """Never `date.today()`. See above."""
    assert entrypoint.DEFAULT_TODAY == "2026-10-01"
```

- [ ] **Step 4: Run it to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_entrypoint.py -v`
Expected: FAIL — `ImportError: cannot import name 'entrypoint' from 'grace'`.

- [ ] **Step 5: Write `grace/entrypoint.py`**

```python
"""The AgentCore Runtime handler. One case per invocation.

**One case per invocation, not a sweep.** The loop lives in Step Functions' Map
state. This matters beyond tidiness: Task 6 established that
`AuthorityGate._seen` is per-instance and in-memory, and that a fresh process
starts with it empty. One case per microVM session means `_seen` can never span
two households — the isolation is structural rather than conventional.

**Classification is `sweep`'s, imported, not re-derived.** Task 6 found the
alternative broken: classifying by "did an interrupt fire" reported an incomplete
household as handled — 10/2 instead of 9/3, no error, because on `c-010` the
model called `send_family_message` rather than `submit_renewal` and the gate
correctly allowed it. Classification therefore comes from two things that cannot
be argued with: `evaluate()` run directly on the case, and the ledger's
`renewal_submitted` row (hard rule 6).

**This path never resumes an interrupt, and that is stronger than the local
CLI.** Task 6 confirmed against the real executor that resuming with a truthy
response *approves* the blocked tool — "Escalate.", "no, hold this one", and
"needs review" all resumed and filed a renewal for a household missing a required
document. `run.py` guards that with an allowlist because a human is present to
answer. Here nobody is, so the graph is invoked exactly once and an interrupt
becomes an escalation row. A path with no resume cannot be talked into filing,
so `MAX_RESUME_ROUNDS` and `APPROVE_DECISIONS` are deliberately absent.
"""

from __future__ import annotations

from datetime import date
from typing import Any, TypedDict

from strands.multiagent.base import Status

from grace.graph import build_case_graph
from grace.observability import setup_telemetry
from grace.run import deliberation_note, gate_reason, outreach_sent, renewal_filed
from grace.store_factory import build_store
from grace.tools.action import Channel, TranscriptChannel

# Never `date.today()`. Fixture c-002's grace period ends 2026-10-30, so a
# live clock turns the 9-act/3-escalate demo into 8/4 from 2026-10-31.
DEFAULT_TODAY = "2026-10-01"

_UNEXPLAINED_INTERRUPT = (
    "The run paused without saying why. A caseworker must review this case."
)


class CaseOutcome(TypedDict, total=False):
    """What one case reports back to Step Functions.

    `status` is always one of `acted` / `escalated` / `error`, and exactly one —
    Task 6's partition rule, which is what makes the 9/3 claim arithmetic that
    adds up rather than three counts that each look plausible.
    """

    status: str
    case_id: str
    filed: bool
    reason: str
    question: str
    deadline: str
    detail: str
    trace_id: str | None


def _reason_text(interrupt: object) -> str:
    """One interrupt's reason as the sentence a caseworker reads.

    The steering handler wraps the gate's text as
    `reason={"message": action.reason}`, so a bare `str()` yields a Python dict
    repr — in the caseworker's brief, which is also the demo's headline output.
    """
    reason = getattr(interrupt, "reason", None)
    if isinstance(reason, dict):
        message = reason.get("message")
        if message is not None:
            return str(message)
    return "no reason given" if reason is None else str(reason)


def process_case(
    payload: dict[str, Any],
    store: Any = None,
    channel: Channel | None = None,
) -> CaseOutcome:
    """Process exactly one case and report which bucket it landed in."""
    from grace.ledger import _current_trace_id

    case_id = payload.get("case_id")
    if not isinstance(case_id, str) or not case_id.strip():
        # The payload comes from Step Functions. A malformed one must produce a
        # reportable outcome, not an unhandled exception the state machine has
        # to interpret.
        return {"status": "error", "case_id": str(case_id),
                "detail": "payload must carry a non-empty string case_id"}

    try:
        today = date.fromisoformat(str(payload.get("today") or DEFAULT_TODAY))
    except ValueError as exc:
        # Never fall back to `date.today()` — see DEFAULT_TODAY.
        return {"status": "error", "case_id": case_id,
                "detail": f"today must be an ISO date: {exc}"}

    store = store if store is not None else build_store()
    channel = channel if channel is not None else TranscriptChannel()

    reason: str | None = None
    reason_is_run_status = False
    deliberation: str | None = None

    try:
        graph = build_case_graph(store, case_id, today, channel)
        result = graph(
            f"Process the renewal for case {case_id}. Today is {today.isoformat()}."
        )

        # `status`, never `stop_reason`: GraphResult has no such field, so a
        # `getattr` check silently never fires (Task 6).
        if result.status == Status.INTERRUPTED:
            interrupts = list(result.interrupts or [])
            reason = (
                "; ".join(_reason_text(i) for i in interrupts)
                if interrupts else _UNEXPLAINED_INTERRUPT
            )
            # No resume. See the module docstring.
        elif result.status != Status.COMPLETED:
            reason = (
                f"The run ended in state "
                f"'{getattr(result.status, 'value', result.status)}' without "
                "completing. A caseworker must review this case."
            )
            reason_is_run_status = True

        deliberation = deliberation_note(result)

    except Exception as exc:  # noqa: BLE001 — fail closed
        if reason is None:
            return {"status": "error", "case_id": case_id, "detail": str(exc),
                    "trace_id": _current_trace_id()}
        reason = f"{reason} (the run then failed: {exc})"

    # Classification proper, identical to `sweep`'s.
    gate = gate_reason(store, case_id, today)
    if gate is not None:
        # The gate's typed reason wins over a generic run-status message: a
        # FAILED node does not stop the graph, so `decide` still ran and the
        # verdict is known and specific (Task 7).
        detail = gate if reason_is_run_status else (reason or gate)
        if outreach_sent(store, case_id):
            detail = f"{detail} (Grace has already messaged the family.)"
        # Appended, never substituted. The gate's typed reason is what makes the
        # escalation auditable; the referee's question is what makes it useful.
        if deliberation:
            detail = f"{detail} Deliberation — {deliberation}"
        return _escalate(store, case_id, detail, today)

    if reason is not None:
        if deliberation:
            reason = f"{reason} Deliberation — {deliberation}"
        return _escalate(store, case_id, reason, today)

    if renewal_filed(store, case_id):
        return {"status": "acted", "case_id": case_id, "filed": True,
                "trace_id": _current_trace_id()}

    # Clean case, clean run, no renewal on the ledger. Grace did not do the one
    # thing this case needed, so it cannot be reported as handled — "acted" is
    # exactly the unconfirmed claim hard rule 6 forbids.
    return _escalate(
        store, case_id,
        "The case is clean but no renewal was filed. A caseworker must file it "
        "or say why not.",
        today,
    )


def _escalate(store: Any, case_id: str, detail: str, today: date) -> CaseOutcome:
    """Record the escalation and report it.

    The row is written here rather than in Step Functions so that a case which
    escalates always leaves durable evidence, even if the state machine's own
    write later fails. Writing it twice is harmless — the sort key carries a
    timestamp — whereas not writing it at all loses the caseworker's queue entry.
    """
    from grace.ledger import _current_trace_id

    deadline = ""
    try:
        deadline = store.get(case_id).cert_end.isoformat()
    except Exception:  # noqa: BLE001 — a missing deadline must not lose the row
        pass

    writer = getattr(store, "write_escalation", None)
    if callable(writer):
        try:
            writer(case_id, reason=detail, question=detail, deadline=deadline)
        except Exception:  # noqa: BLE001 — the returned payload is still evidence
            pass

    return {"status": "escalated", "case_id": case_id, "reason": detail,
            "question": detail, "deadline": deadline,
            "trace_id": _current_trace_id()}


def invoke(payload: dict[str, Any]) -> CaseOutcome:
    """The Runtime entrypoint. Sets telemetry up once, then processes the case."""
    setup_telemetry()
    return process_case(payload)
```

- [ ] **Step 6: Run the entrypoint tests**

Run: `.venv/bin/python -m pytest tests/test_entrypoint.py -v`
Expected: PASS, 9 tests.

- [ ] **Step 7: Run the whole suite**

Run: `.venv/bin/python -m pytest`
Expected: PASS — **405 tests**. Report the real number.

- [ ] **Step 8: Verify the entrypoint against a real graph, one case**

```bash
.venv/bin/python -c "
from grace.entrypoint import process_case
out = process_case({'case_id': 'c-010', 'today': '2026-10-01'})
print(out)
assert out['status'] == 'escalated', out
assert out.get('filed') is not True
print('OK: c-010 escalated against a real Bedrock run, nothing filed')
"
```

Expected: `status: escalated` for `c-010` (missing `proof_of_residency`). This is one real graph
invocation, roughly 9 Bedrock calls.

- [ ] **Step 9: Commit**

```bash
git add grace/entrypoint.py grace/run.py tests/test_entrypoint.py
git commit -m "feat: Runtime entrypoint that escalates without ever resuming"
```

---

## Task 5: AgentCore Memory, attached where it is legal to attach it

A recert cycle is annual. "Income verified via pay stubs last cycle" and "prefers Arabic, evenings"
must survive the eleven months between contacts. Read §3.1–3.3 of the spec first — two of the spec's
original assumptions about Memory were wrong.

**Files:**

- Create: `grace/memory.py`, `infra/provision_memory.py`
- Modify: `pyproject.toml` — add `bedrock-agentcore`
- Test: `tests/test_memory.py`

**Interfaces:**

- Consumes: `infra.naming.{MEMORY, REGION, TAGS}`
- Produces:
  - `grace.memory.RETRIEVAL_NAMESPACES: dict[str, RetrievalConfig]`
  - `grace.memory.build_session_manager(case_id: str, session_id: str, memory_id: str | None = None)`
    `-> AgentCoreMemorySessionManager | None`
  - `grace.memory.actor_id(case_id: str) -> str`
  - `infra.provision_memory.provision(client=None) -> str` (returns the memory id)

### Three findings that change this task from what the spec assumed

1. **`AgentCoreMemorySessionManager` is not in `strands-agents`.** `strands.session` ships only
   `file`, `s3`, `repository`, and `snapshot` managers. It lives in `bedrock-agentcore`, which is not
   installed. Measured marginal cost against Grace's real venv: **2 packages**
   (`bedrock-agentcore`, `websockets`) — everything else it wants is already satisfied by
   `strands-agents`. Smaller than the `[otel]` extra's 10, and first-party AWS.
2. **`PersistenceMode` is `FULL` or `NONE` only.** The spec's §3.7 says Grace "writes to memory
   selectively — blocked or errored turns are not persisted." No per-turn selectivity exists. So the
   hazard must be handled by *where* Memory attaches, not by a config flag.
3. **Hard rule 2 constrains where it may attach at all.** Agents inside a Graph or Swarm must not
   have their own `session_manager` — Python raises `ValueError`. So Memory attaches to the
   orchestrator only.
4. **`namespaces` is a legacy parameter; use `namespaceTemplates`.** Verified against the live API
   model: `CreateMemory`'s strategy shape documents `namespaces` as *"a legacy parameter, use
   `namespaceTemplates`"*. Both fields exist and both accept the same list, which is exactly why this
   is dangerous — writing the legacy field succeeds, and CLAUDE.md already warns that a retrieval
   namespace not matching what was set at creation **retrieves nothing, silently**. There is no error
   to notice. Step 9's agreement check therefore reads *both* spellings and asserts the result is
   non-empty, so it cannot pass vacuously because the service echoed the other field.

**Verified signatures** (introspected against the real package, with `strands` present — do not
re-derive these from docs):

```text
AgentCoreMemorySessionManager(agentcore_memory_config, region_name=None, boto_session=None,
                              boto_client_config=None, *, converter=None, **kwargs)

AgentCoreMemoryConfig(*, memory_id: str, session_id: str, actor_id: str,   # all three REQUIRED
                      retrieval_config: dict[str, RetrievalConfig] | None = None, ...)

RetrievalConfig(*, top_k: int = 10, relevance_score: float = 0.2,
                strategy_id: str | None = None, initialization_query: str | None = None)
```

- [ ] **Step 1: Add the dependency and record why**

In `pyproject.toml`, add to `dependencies` with this comment:

```toml
    # AgentCore Memory's Strands session manager lives here, NOT in
    # strands-agents — `strands.session` ships only file/s3/repository/snapshot.
    # Measured marginal cost against the existing venv: 2 packages
    # (bedrock-agentcore, websockets); everything else is already satisfied by
    # strands-agents. Smaller than the [otel] extra's 10, and first-party AWS.
    "bedrock-agentcore>=1.22",
```

Then: `uv pip install -e ".[dev]"` (or `uv pip install bedrock-agentcore` into `.venv`).

- [ ] **Step 2: Confirm the marginal cost was really 2 packages**

```bash
.venv/bin/python -c "import bedrock_agentcore, websockets; print('importable')"
.venv/bin/python -c "
from bedrock_agentcore.memory.integrations.strands.session_manager import AgentCoreMemorySessionManager
from bedrock_agentcore.memory.integrations.strands.config import AgentCoreMemoryConfig, RetrievalConfig, PersistenceMode
print('PersistenceMode:', [m.name for m in PersistenceMode])
print('OK')
"
```

Expected: `importable`, then `PersistenceMode: ['FULL', 'NONE']` — confirming finding 2 rather than
taking it on trust. If more than `bedrock-agentcore` and `websockets` were installed, stop and
report: the dependency budget was part of the approval.

- [ ] **Step 3: Run the whole suite before writing anything**

Run: `.venv/bin/python -m pytest`
Expected: PASS — **405 tests**. A new dependency must not change a single existing result.

- [ ] **Step 4: Write the failing memory test**

Create `tests/test_memory.py`:

```python
"""Memory wiring. Mostly about where it must NOT go.

`build_session_manager` returns `None` when no memory is configured, which is the
normal case for the fast suite and for a local sweep — Memory is an AWS resource
and these tests are offline.
"""

from __future__ import annotations

import pytest

from grace import memory


def test_the_actor_is_the_household_case(monkeypatch):
    """`actor_id` scopes long-term facts. One actor per case means one
    family's history can never be retrieved into another's run."""
    assert memory.actor_id("c-011") == "c-011"


def test_no_memory_configured_returns_none(monkeypatch):
    """The offline path. A local sweep must not require an AWS memory
    resource, and a missing one must not raise — it must simply mean "no
    long-term recall this run"."""
    monkeypatch.delenv("GRACE_MEMORY_ID", raising=False)
    assert memory.build_session_manager("c-001", "session-" + "x" * 30) is None


def test_the_namespaces_match_what_provisioning_creates():
    """The namespace must match the `namespaceTemplates` set at memory
    creation. The AWS blog's `/users/{actorId}/facts` form does not match
    working code — `/facts/{actorId}` does — and a mismatch silently
    retrieves nothing rather than erroring."""
    assert set(memory.RETRIEVAL_NAMESPACES) == {
        "/facts/{actorId}",
        "/preferences/{actorId}",
    }


def test_every_retrieval_config_has_a_relevance_floor():
    """A floor of 0 retrieves noise into an eligibility decision. Reflection
    lessons and remembered facts may only make Grace more cautious (hard rule
    5), so what gets recalled must at least be relevant."""
    for namespace, config in memory.RETRIEVAL_NAMESPACES.items():
        assert config.relevance_score > 0, namespace
        assert config.top_k > 0, namespace


def test_memory_is_never_attached_to_a_node_inside_the_graph():
    """Hard rule 2, asserted structurally rather than trusted.

    Agents inside a Graph or Swarm must not carry their own `session_manager` —
    Python raises `ValueError`. Task 7 established that a `Swarm` node's manager
    is the *public* `session_manager` attribute and that the equivalent Task 6
    test passed **vacuously** through `getattr(..., None)`, so this recurses
    into `executor.nodes` instead of skipping what it cannot introspect.
    """
    from datetime import date

    from grace.cases.store import InMemoryCaseStore, load_fixture_cases
    from grace.graph import build_case_graph
    from grace.tools.action import TranscriptChannel

    graph = build_case_graph(
        InMemoryCaseStore(load_fixture_cases()), "c-011",
        date(2026, 10, 1), TranscriptChannel(),
    )

    def assert_clean(node, path):
        executor = getattr(node, "executor", node)
        # Both spellings: an Agent uses the private attribute, a Swarm exposes
        # the public one. Checking only one is how the vacuous version passed.
        for attribute in ("_session_manager", "session_manager"):
            assert getattr(executor, attribute, None) is None, f"{path}.{attribute}"
        nested = getattr(executor, "nodes", None)
        if isinstance(nested, dict):
            for name, child in nested.items():
                assert_clean(child, f"{path}.{name}")

    assert graph.nodes, "the graph has no nodes, so this test proves nothing"
    for name, node in graph.nodes.items():
        assert_clean(node, name)
```

- [ ] **Step 5: Run it to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_memory.py -v`
Expected: FAIL — `ImportError: cannot import name 'memory' from 'grace'`.

- [ ] **Step 6: Write `grace/memory.py`**

```python
"""AgentCore Memory wiring: per-household facts across the annual gap.

A recert cycle is annual, so "income verified via pay stubs last cycle" and
"prefers Arabic, evenings" have to survive eleven months between contacts.

**Where this may attach.** The orchestrator only. Agents inside a Graph or Swarm
must not carry their own `session_manager` — Python raises `ValueError` (hard rule
2) — so `decide`, `intake`, `documents`, and the swarm's three agents never get
one. `tests/test_memory.py` asserts that structurally, recursing into nested
nodes, because Task 7 found the equivalent Task 6 assertion passing vacuously
through `getattr(..., None)`.

**The spec's selective-write plan is not implementable as written.**
`PersistenceMode` is `FULL` or `NONE` only — verified against the installed
package, no per-turn selectivity exists. The hazard the spec was guarding
(guardrail-blocked or errored turns persisting and poisoning later runs) is
therefore handled by *scope*: Memory sits on the orchestrator, whose turns are
the case's opening task and final summary, not on the nodes that carry raw tool
output and model errors.

**Retrieval is advisory.** Anything recalled here may make Grace more cautious
and may never satisfy a gate condition (hard rule 5). The gate reads the case
record, never memory.
"""

from __future__ import annotations

import logging
import os

from bedrock_agentcore.memory.integrations.strands.config import (
    AgentCoreMemoryConfig,
    RetrievalConfig,
)
from bedrock_agentcore.memory.integrations.strands.session_manager import (
    AgentCoreMemorySessionManager,
)

from infra import naming

logger = logging.getLogger(__name__)

# `/facts/{actorId}`, NOT the AWS blog post's `/users/{actorId}/facts`. The
# namespace must match the `namespaceTemplates` set at memory creation, and a
# mismatch retrieves nothing silently rather than raising — so this dict and
# `infra/provision_memory.py` must be changed together.
RETRIEVAL_NAMESPACES: dict[str, RetrievalConfig] = {
    # What was verified and how, last cycle.
    "/facts/{actorId}": RetrievalConfig(top_k=10, relevance_score=0.3),
    # Language and contact-time preferences for outreach.
    "/preferences/{actorId}": RetrievalConfig(top_k=5, relevance_score=0.5),
}


def actor_id(case_id: str) -> str:
    """The memory actor for one household.

    One actor per case, so one family's history can never be retrieved into
    another family's run. The case id is already opaque (hard rule 9), so it is
    safe as an actor key in a way a household name would not be.
    """
    return case_id


def build_session_manager(
    case_id: str, session_id: str, memory_id: str | None = None
) -> AgentCoreMemorySessionManager | None:
    """Build the orchestrator's session manager, or `None` if unconfigured.

    Returns `None` rather than raising when no memory id is available: a local
    sweep and the fast suite must both run offline, and "no long-term recall this
    run" is a degraded mode, not an error. Memory is an enhancement to outreach
    quality — it is never consulted by the authority gate, so its absence cannot
    change a verdict.
    """
    memory_id = memory_id or os.getenv("GRACE_MEMORY_ID")
    if not memory_id:
        return None
    try:
        config = AgentCoreMemoryConfig(
            memory_id=memory_id,
            session_id=session_id,
            actor_id=actor_id(case_id),
            retrieval_config=RETRIEVAL_NAMESPACES,
        )
        return AgentCoreMemorySessionManager(config, region_name=naming.REGION)
    except Exception:  # noqa: BLE001 — recall is an enhancement, never a gate
        logger.warning("memory session manager unavailable; continuing without recall",
                       exc_info=True)
        return None
```

- [ ] **Step 7: Run the memory tests**

Run: `.venv/bin/python -m pytest tests/test_memory.py -v`
Expected: PASS, 5 tests. Note the last one builds a real graph but makes no Bedrock call — building
is free; invoking is not.

- [ ] **Step 8: Write `infra/provision_memory.py`**

```python
"""Create the AgentCore Memory resource and its namespace strategies.

The `namespaceTemplates` here MUST match `grace.memory.RETRIEVAL_NAMESPACES`.
A mismatch retrieves nothing, silently — there is no error to notice.
"""

from __future__ import annotations

import boto3
from botocore.exceptions import ClientError

from infra import naming


def provision(client=None) -> str:
    """Create the memory if absent; return its id. Idempotent."""
    client = client or boto3.client("bedrock-agentcore-control", region_name=naming.REGION)

    existing = client.list_memories()
    for memory in existing.get("memories", []):
        if memory["id"].startswith(naming.MEMORY):
            return str(memory["id"])

    try:
        response = client.create_memory(
            name=naming.MEMORY,
            description="Per-household facts and preferences across annual recert cycles",
            eventExpiryDuration=90,
            memoryStrategies=[
                {
                    "semanticMemoryStrategy": {
                        "name": "household-facts",
                        # `namespaceTemplates`, NOT `namespaces`. Verified against
                        # the live API model: `namespaces` is documented as "a
                        # legacy parameter, use namespaceTemplates". CLAUDE.md
                        # already warns the retrieval namespace must match what is
                        # set at creation, and a mismatch retrieves nothing
                        # silently — so writing the legacy field while
                        # `grace/memory.py` retrieves against the template form is
                        # exactly the invisible failure to avoid.
                        "namespaceTemplates": ["/facts/{actorId}"],
                    }
                },
                {
                    "userPreferenceMemoryStrategy": {
                        "name": "household-preferences",
                        "namespaceTemplates": ["/preferences/{actorId}"],
                    }
                },
            ],
            tags=naming.TAGS,
        )
    except ClientError as exc:
        if exc.response["Error"]["Code"] not in {"ConflictException", "ValidationException"}:
            raise
        # Another run created it between the list and the create.
        for memory in client.list_memories().get("memories", []):
            if memory["id"].startswith(naming.MEMORY):
                return str(memory["id"])
        raise
    return str(response["memory"]["id"])


if __name__ == "__main__":
    print(f"GRACE_MEMORY_ID={provision()}")
```

- [ ] **Step 9: Provision the memory and confirm the namespaces agree**

```bash
.venv/bin/python -m infra.provision_memory
.venv/bin/python -m infra.provision_memory   # idempotence
```

Expected: the same `GRACE_MEMORY_ID=...` printed twice. Then confirm the two sources agree — the
check that catches finding 3 from the spec:

```bash
.venv/bin/python -c "
import boto3
from grace.memory import RETRIEVAL_NAMESPACES
from infra import naming, provision_memory
mid = provision_memory.provision()
c = boto3.client('bedrock-agentcore-control', region_name=naming.REGION)
m = c.get_memory(memoryId=mid)['memory']
# Read BOTH fields: \`namespaces\` is the legacy spelling and \`namespaceTemplates\`
# is current, and which one a strategy echoes back is a property of the API, not
# of what was sent. Unioning them means this check cannot pass vacuously against
# an empty set just because the service answered in the other spelling.
created = {
    n for s in m.get('strategies', [])
    for key in ('namespaceTemplates', 'namespaces')
    for n in (s.get(key) or [])
}
declared = set(RETRIEVAL_NAMESPACES)
print('created :', sorted(created))
print('declared:', sorted(declared))
assert created, 'no namespaces came back at all — the check would be vacuous'
assert created == declared, 'namespace mismatch retrieves nothing, silently'
print('OK: namespaces agree')
"
```

Expected: the two sets match. If they do not, fix `provision_memory.py` and
`grace/memory.py` together — never one alone.

- [ ] **Step 10: Run the whole suite**

Run: `.venv/bin/python -m pytest`
Expected: PASS — **410 tests**. Report the real number.

- [ ] **Step 11: Commit**

```bash
git add pyproject.toml grace/memory.py infra/provision_memory.py tests/test_memory.py
git commit -m "feat: per-household AgentCore Memory on the orchestrator only"
```

---

## Task 6: IAM — four roles, each narrow, one explicit Deny

**Files:**

- Create: `infra/provision_iam.py`
- Test: `tests/test_infra_iam.py`

**Interfaces:**

- Consumes: `infra.naming`
- Produces: `infra.provision_iam.provision(client=None, account_id: str | None = None) -> dict[str, str]`
  mapping role purpose → role ARN, keys `runtime`, `lambda`, `stepfunctions`, `eventbridge`;
  plus `infra.provision_iam.runtime_policy(account_id: str) -> dict` and
  `infra.provision_iam.DENY_SID: str`

### The one statement that matters most

Appendix D.1: three APIs return a workload access token, and only one verifies anything.
`GetWorkloadAccessTokenForUserId` *"treats the userId as an opaque string without verifying it
against an authenticated end-user identity"* — so an authenticated caseworker could pass any
household id and receive a token scoped to that household. That is precisely the cross-family access
this design forbids.

An explicit `Deny` beats any `Allow`, including one someone attaches later. **This carries even
though Identity is deferred in this plan** — it costs one statement now and closes a standing
exposure. It also rules out the `BedrockAgentCoreFullAccess` managed policy, which the docs warn
grants that action; do not attach it anywhere.

### The Bedrock ARN shapes are verified — do not "tighten" the wildcard

Checked against the live profiles before this task was written:

```text
arn:aws:bedrock:us-east-1:<acct>:inference-profile/us.amazon.nova-pro-v1:0
  fans out to  arn:aws:bedrock:us-east-1::foundation-model/amazon.nova-pro-v1:0
               arn:aws:bedrock:us-west-2::foundation-model/amazon.nova-pro-v1:0   <- cross-region
               arn:aws:bedrock:us-east-2::foundation-model/amazon.nova-pro-v1:0

arn:aws:bedrock:us-east-1:<acct>:inference-profile/global.amazon.nova-2-lite-v1:0
  fans out to  arn:aws:bedrock:::foundation-model/amazon.nova-2-lite-v1:0         <- NO region at all
               arn:aws:bedrock:us-east-1::foundation-model/amazon.nova-2-lite-v1:0
```

Two consequences the policy below depends on:

- **Both sides of the indirection need naming.** Granting only the inference-profile ARN is not
  enough; the call reaches the foundation model behind it.
- **`arn:aws:bedrock:*::foundation-model/...` is deliberate, not lazy.** A `global.` profile's
  fan-out has an *empty* region field and a `us.` profile's includes other regions, so pinning
  `us-east-1` would break both. Verified the wildcard matches the empty, same-region, and
  cross-region forms. Every ARN still names a specific Nova model, so hard rule 1 holds.
- `p.split(".", 1)[-1]` is what turns `global.amazon.nova-2-lite-v1:0` into
  `amazon.nova-2-lite-v1:0` — verified against all three profiles.

- [x] **Step 1: Write the failing IAM policy test**

Create `tests/test_infra_iam.py`:

```python
"""IAM policy shapes, asserted offline.

These build policy documents and check them as data — no AWS calls. A policy is
exactly the kind of thing that is easy to get subtly wrong and hard to notice,
so the wrong-shape cases are worth a test even though provisioning is a script.
"""

from __future__ import annotations

import json

from infra import naming, provision_iam

ACCOUNT = "123456789012"


def test_the_runtime_policy_explicitly_denies_the_unverified_token_path():
    """Appendix D.1. `GetWorkloadAccessTokenForUserId` treats the userId as an
    opaque string with no verification, so an authenticated caseworker could
    pass any household id and get a token scoped to that household.

    An explicit Deny beats any Allow, including a future one — which is why
    this is asserted even though Identity is deferred.
    """
    policy = provision_iam.runtime_policy(ACCOUNT)
    denies = [s for s in policy["Statement"] if s["Effect"] == "Deny"]
    assert denies, "the runtime policy must carry an explicit Deny"
    actions = {a for s in denies for a in _as_list(s["Action"])}
    assert "bedrock-agentcore:GetWorkloadAccessTokenForUserId" in actions


def test_no_policy_grants_a_wildcard_action_on_a_wildcard_resource():
    """`Action: *` on `Resource: *` is the shape that makes every other
    scoping decision here decorative."""
    policy = provision_iam.runtime_policy(ACCOUNT)
    for statement in policy["Statement"]:
        if statement["Effect"] != "Allow":
            continue
        actions = _as_list(statement["Action"])
        resources = _as_list(statement["Resource"])
        assert not ("*" in actions and "*" in resources), statement


def test_bedrock_access_is_scoped_to_the_three_nova_profiles():
    """Hard rule 1: Amazon Nova only. A wildcard on `bedrock:InvokeModel`
    would let a future edit reach a third-party model without tripping the
    model-id test that guards `grace/`."""
    policy = provision_iam.runtime_policy(ACCOUNT)
    resources = [
        r for s in policy["Statement"] if s["Effect"] == "Allow"
        for r in _as_list(s["Resource"])
        if "bedrock" in r and "agentcore" not in r
    ]
    assert resources, "no bedrock resources found in the runtime policy"
    assert all("nova" in r for r in resources), resources


def test_the_managed_full_access_policy_is_never_referenced():
    """The docs warn `BedrockAgentCoreFullAccess` grants the unsafe token
    action and is for development only. Grace must not use it."""
    source = json.dumps(provision_iam.runtime_policy(ACCOUNT))
    assert "BedrockAgentCoreFullAccess" not in source


def test_dynamodb_access_is_scoped_to_the_grace_table():
    policy = provision_iam.runtime_policy(ACCOUNT)
    tables = [
        r for s in policy["Statement"] if s["Effect"] == "Allow"
        for r in _as_list(s["Resource"]) if ":dynamodb:" in r
    ]
    assert tables
    assert all(naming.TABLE in r for r in tables), tables


def _as_list(value):
    return value if isinstance(value, list) else [value]
```

- [x] **Step 2: Run it to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_infra_iam.py -v`
Expected: FAIL — `ImportError: cannot import name 'provision_iam' from 'infra'`.

- [x] **Step 3: Write `infra/provision_iam.py`**

```python
"""The four execution roles. Each scoped to what it actually needs.

The security-relevant content of this file is one `Deny` statement — see
`DENY_SID` and Appendix D.1. Everything else is ordinary least privilege.
"""

from __future__ import annotations

import json

import boto3
from botocore.exceptions import ClientError

from infra import naming

DENY_SID = "DenyUnverifiedUserIdPath"

# The three profiles from grace/models.py. Listed as ARNs rather than a wildcard
# so hard rule 1 (Amazon Nova only) is enforced by IAM as well as by the
# model-id test that walks `grace/`.
_NOVA_PROFILES = (
    "global.amazon.nova-2-lite-v1:0",
    "us.amazon.nova-pro-v1:0",
    "us.amazon.nova-micro-v1:0",
)

_TRUST = {
    "runtime": "bedrock-agentcore.amazonaws.com",
    "lambda": "lambda.amazonaws.com",
    "stepfunctions": "states.amazonaws.com",
    "eventbridge": "events.amazonaws.com",
}


def _trust_policy(service: str, account_id: str) -> dict:
    """Trust policy with a source-account condition.

    The condition is what stops a confused-deputy call from another account
    assuming this role. The ARN-level condition the docs also recommend needs the
    resource ARN, which does not exist before creation — so account scoping is
    applied now and the ARN condition is a documented follow-up in the runbook.
    """
    return {
        "Version": "2012-10-17",
        "Statement": [{
            "Effect": "Allow",
            "Principal": {"Service": service},
            "Action": "sts:AssumeRole",
            "Condition": {"StringEquals": {"aws:SourceAccount": account_id}},
        }],
    }


def runtime_policy(account_id: str) -> dict:
    """What the deployed runtime may do.

    Read the `Deny` first: `GetWorkloadAccessTokenForUserId` performs no
    verification of the user id it is handed, so it would let an authenticated
    caseworker obtain a token scoped to any household (Appendix D.1). An explicit
    Deny beats any Allow, including one attached later by a future task or a
    managed policy — which is why this is here even though Identity is deferred.
    """
    region = naming.REGION
    return {
        "Version": "2012-10-17",
        "Statement": [
            {
                "Sid": DENY_SID,
                "Effect": "Deny",
                "Action": "bedrock-agentcore:GetWorkloadAccessTokenForUserId",
                "Resource": (
                    f"arn:aws:bedrock-agentcore:{region}:{account_id}:"
                    "workload-identity-directory/default"
                ),
            },
            {
                "Sid": "NovaOnly",
                "Effect": "Allow",
                "Action": ["bedrock:InvokeModel", "bedrock:InvokeModelWithResponseStream"],
                "Resource": [
                    f"arn:aws:bedrock:{region}:{account_id}:inference-profile/{p}"
                    for p in _NOVA_PROFILES
                ] + [
                    # The inference profile fans out to foundation models; both
                    # sides of that indirection need naming, and both stay Nova.
                    f"arn:aws:bedrock:*::foundation-model/{p.split('.', 1)[-1]}"
                    for p in _NOVA_PROFILES
                ],
            },
            {
                "Sid": "LedgerTableOnly",
                "Effect": "Allow",
                "Action": [
                    "dynamodb:PutItem", "dynamodb:GetItem", "dynamodb:Query",
                    "dynamodb:UpdateItem",
                ],
                "Resource": [
                    f"arn:aws:dynamodb:{region}:{account_id}:table/{naming.TABLE}",
                    f"arn:aws:dynamodb:{region}:{account_id}:table/{naming.TABLE}/index/*",
                ],
            },
            {
                "Sid": "HouseholdMemory",
                "Effect": "Allow",
                "Action": [
                    "bedrock-agentcore:CreateEvent", "bedrock-agentcore:ListEvents",
                    "bedrock-agentcore:GetEvent", "bedrock-agentcore:RetrieveMemoryRecords",
                    "bedrock-agentcore:ListMemoryRecords",
                ],
                "Resource": f"arn:aws:bedrock-agentcore:{region}:{account_id}:memory/*",
            },
            {
                "Sid": "OwnLogs",
                "Effect": "Allow",
                "Action": ["logs:CreateLogStream", "logs:PutLogEvents",
                           "logs:DescribeLogStreams"],
                "Resource": f"arn:aws:logs:{region}:{account_id}:log-group:/aws/*",
            },
        ],
    }


def _lambda_policy(account_id: str) -> dict:
    region = naming.REGION
    return {
        "Version": "2012-10-17",
        "Statement": [
            {
                "Sid": "InvokeTheGraceRuntimeOnly",
                "Effect": "Allow",
                "Action": "bedrock-agentcore:InvokeAgentRuntime",
                "Resource": [
                    f"arn:aws:bedrock-agentcore:{region}:{account_id}:runtime/{naming.RUNTIME}*",
                    f"arn:aws:bedrock-agentcore:{region}:{account_id}:runtime/{naming.RUNTIME}*/*",
                ],
            },
            {
                "Sid": "OwnLogs",
                "Effect": "Allow",
                "Action": ["logs:CreateLogGroup", "logs:CreateLogStream", "logs:PutLogEvents"],
                "Resource": f"arn:aws:logs:{region}:{account_id}:*",
            },
        ],
    }


def _stepfunctions_policy(account_id: str) -> dict:
    region = naming.REGION
    return {
        "Version": "2012-10-17",
        "Statement": [
            {
                "Sid": "InvokeTheCaseLambdaOnly",
                "Effect": "Allow",
                "Action": "lambda:InvokeFunction",
                "Resource": f"arn:aws:lambda:{region}:{account_id}:function:{naming.LAMBDA}",
            },
            {
                # The Catch branch writes the escalation row. Without this, a
                # failed case would fail *silently* at the point whose whole
                # purpose is not losing it.
                "Sid": "WriteEscalationRows",
                "Effect": "Allow",
                "Action": "dynamodb:PutItem",
                "Resource": f"arn:aws:dynamodb:{region}:{account_id}:table/{naming.TABLE}",
            },
        ],
    }


def _eventbridge_policy(account_id: str) -> dict:
    return {
        "Version": "2012-10-17",
        "Statement": [{
            "Sid": "StartTheSweepOnly",
            "Effect": "Allow",
            "Action": "states:StartExecution",
            "Resource": (
                f"arn:aws:states:{naming.REGION}:{account_id}:"
                f"stateMachine:{naming.STATE_MACHINE}"
            ),
        }],
    }


_POLICIES = {
    "runtime": runtime_policy,
    "lambda": _lambda_policy,
    "stepfunctions": _stepfunctions_policy,
    "eventbridge": _eventbridge_policy,
}


def provision(client=None, account_id: str | None = None) -> dict[str, str]:
    """Create or update the four roles; return purpose → ARN. Idempotent."""
    client = client or boto3.client("iam")
    if account_id is None:
        account_id = boto3.client("sts").get_caller_identity()["Account"]

    arns: dict[str, str] = {}
    for purpose, build in _POLICIES.items():
        role_name = f"grace-{purpose}-role"
        policy_name = f"grace-{purpose}-policy"
        try:
            client.create_role(
                RoleName=role_name,
                AssumeRolePolicyDocument=json.dumps(
                    _trust_policy(_TRUST[purpose], account_id)
                ),
                Description=f"Grace {purpose} execution role",
                Tags=[{"Key": k, "Value": v} for k, v in naming.TAGS.items()],
            )
        except ClientError as exc:
            if exc.response["Error"]["Code"] != "EntityAlreadyExists":
                raise
            # Re-running must converge on the intended trust policy rather than
            # leaving whatever a previous version wrote.
            client.update_assume_role_policy(
                RoleName=role_name,
                PolicyDocument=json.dumps(_trust_policy(_TRUST[purpose], account_id)),
            )
        client.put_role_policy(
            RoleName=role_name,
            PolicyName=policy_name,
            PolicyDocument=json.dumps(build(account_id)),
        )
        arns[purpose] = client.get_role(RoleName=role_name)["Role"]["Arn"]
    return arns


if __name__ == "__main__":
    for purpose, arn in provision().items():
        print(f"{purpose}: {arn}")
```

- [x] **Step 4: Run the IAM tests**

Run: `.venv/bin/python -m pytest tests/test_infra_iam.py -v`
Expected: PASS, 6 tests.

- [x] **Step 5: Create the roles and verify the Deny landed**

```bash
.venv/bin/python -m infra.provision_iam
.venv/bin/python -m infra.provision_iam   # idempotence

aws iam get-role-policy --role-name grace-runtime-role \
  --policy-name grace-runtime-policy \
  --query 'PolicyDocument.Statement[?Effect==`Deny`]'
```

Expected: both runs print four ARNs, and the query returns the
`GetWorkloadAccessTokenForUserId` Deny. If the Deny is absent, stop — that is the one statement this
task exists for.

- [x] **Step 6: Confirm the managed policy is attached nowhere**

```bash
for r in grace-runtime-role grace-lambda-role grace-stepfunctions-role grace-eventbridge-role; do
  echo "$r: $(aws iam list-attached-role-policies --role-name $r \
    --query 'AttachedPolicies[].PolicyName' --output text)"
done
```

Expected: empty for all four — inline policies only, and specifically no
`BedrockAgentCoreFullAccess`, which the docs warn grants the denied action.

- [x] **Step 7: Run the whole suite**

Run: `.venv/bin/python -m pytest`
Expected: PASS — **416 tests**. Report the real number.

- [x] **Step 8: Commit**

```bash
git add infra/provision_iam.py tests/test_infra_iam.py
git commit -m "feat: four scoped IAM roles and an explicit deny on the unverified token path"
```

---

## Task 7: Deploy to AgentCore Runtime

The highest-risk task in this plan: the first whose failure mode is an AWS error rather than a
failing test. Task 0's preflight must be green first.

**The AgentCore contract was verified empirically before this task was written** — by scaffolding a
throwaway template agent with `agentcore create --framework Strands --build Container` and reading
what it generated. Everything below reflects that reference implementation, not documentation.

**Files:**

- Create: `Dockerfile`, `.dockerignore`, `runtime_app.py` (repo root)
- Modify: `docs/runbook-deploy.md` — append the working deploy sequence
- Test: `tests/test_runtime_app.py`

**Interfaces:**

- Consumes: `grace.entrypoint.process_case`, `grace.observability.{REDACTION_TOKEN, redaction_is_configured}`
- Produces: a `READY` runtime, its ARN in the runbook, and `runtime_app.invoke(payload, context) -> dict`

### The verified Runtime contract

Five facts, each confirmed against the generated reference agent. Do not substitute assumptions.

1. **The entrypoint is `BedrockAgentCoreApp`, not a bare handler function.**

   ```python
   from bedrock_agentcore.runtime import BedrockAgentCoreApp
   app = BedrockAgentCoreApp()

   @app.entrypoint
   def invoke(payload, context): ...

   if __name__ == "__main__":
       app.run()
   ```

   It is a Starlette app; `run(port=8080, host=None)`. Its own docstring says *"Invocation payloads
   are passed to the registered function unchanged. Applications should validate input before
   forwarding it to an agent framework"* — which is exactly what `process_case` already does.

   The template's entrypoint is `async` and yields streaming events because it is a chat agent.
   **Grace's is a plain `def` returning a dict**, because a sweep is a batch job with one answer.
   Both are supported; a sync function returning a dict is the simpler correct form here.

2. **Port 8080 for HTTP mode**, per the Runtime service contract (8000 MCP, 9000 A2A). `EXPOSE` it.

3. **`bedrock-agentcore` provides the runtime app** — the same dependency Task 5 already added for
   Memory. No new dependency for this task.

4. **The container is built in CodeBuild on ARM64, not locally.** So Podman is needed only for the
   local smoke test in Step 6, not for the deploy itself. A `Dockerfile` must exist in the agent's
   `codeLocation`.

5. **`agentcore deploy` deploys through CDK**, and CDK is already bootstrapped in this account
   (Task 0). `--dry-run` and `--diff` exist and are worth using first.

### Two template defaults Grace deliberately does not copy

- **`aws-opentelemetry-distro`** is in the template's dependencies. CLAUDE.md forbids it: it is for
  agents hosted *outside* AgentCore Runtime, and Runtime instruments itself. Grace's
  `grace/observability.py` already handles this correctly by gating on
  `AGENT_OBSERVABILITY_ENABLED`. Do not add the package, and do not use the template's
  `CMD ["opentelemetry-instrument", ...]`.
- **`runtimeVersion: PYTHON_3_14`** is what `agentcore add agent` records by default. Grace targets
  **3.12**; the Dockerfile pins it, and since the image is what actually runs, the manifest field
  does not override the base image. Note it and move on.

- [ ] **Step 1: Write the failing handler test**

Create `tests/test_runtime_app.py`:

```python
"""The container's entrypoint. Thin on purpose.

All logic lives in `grace.entrypoint`; this asserts the wiring and the one
environment invariant that must hold in the deployed process.
"""

from __future__ import annotations

import runtime_app


def test_the_entrypoint_delegates_to_process_case(monkeypatch):
    seen = {}

    def fake_process_case(payload):
        seen.update(payload)
        return {"status": "acted", "case_id": payload["case_id"], "filed": True}

    monkeypatch.setattr(runtime_app, "process_case", fake_process_case)
    out = runtime_app.invoke({"case_id": "c-001", "today": "2026-10-01"}, None)
    assert out["status"] == "acted"
    assert seen["case_id"] == "c-001"


def test_the_entrypoint_never_raises_out_of_the_container(monkeypatch):
    """An unhandled exception inside Runtime is an opaque 500 to the caller.
    Step Functions can branch on `{"status": "error"}`; it cannot branch on a
    stack trace it never receives. Fail closed, and stay reportable."""

    def boom(payload):
        raise RuntimeError("something deep failed")

    monkeypatch.setattr(runtime_app, "process_case", boom)
    out = runtime_app.invoke({"case_id": "c-001"}, None)
    assert out["status"] == "error"
    assert "something deep failed" in out["detail"]


def test_a_non_dict_payload_is_refused_rather_than_forwarded(monkeypatch):
    """`BedrockAgentCoreApp` passes payloads through **unchanged** — its own
    docstring says the application must validate before forwarding. A string
    payload would otherwise reach `.get()` and raise an AttributeError deep in
    `process_case`."""
    out = runtime_app.invoke("not a dict", None)
    assert out["status"] == "error"


def test_the_app_is_a_bedrock_agentcore_app():
    """The wiring itself: a plain function that Runtime never calls would look
    identical to a working agent until the first invocation returns nothing."""
    from bedrock_agentcore.runtime import BedrockAgentCoreApp

    assert isinstance(runtime_app.app, BedrockAgentCoreApp)


def test_the_redaction_guard_rejects_a_missing_token():
    """Hard rule 8, checked where it matters — in the deployed process, not in
    `.env.example`. Absence of `gen_ai_unredacted_attributes=` disables span
    redaction entirely and exports the full household record to CloudWatch."""
    from grace.observability import redaction_is_configured

    assert not redaction_is_configured(
        {"OTEL_SEMCONV_STABILITY_OPT_IN": "gen_ai_latest_experimental"}
    )
    assert redaction_is_configured(
        {"OTEL_SEMCONV_STABILITY_OPT_IN":
         "gen_ai_latest_experimental,gen_ai_unredacted_attributes="}
    )
```

- [ ] **Step 2: Run it to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_runtime_app.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'runtime_app'`.

- [ ] **Step 3: Write `runtime_app.py`**

```python
"""AgentCore Runtime entrypoint for Grace.

Deliberately thin: everything of substance is in `grace.entrypoint`, which is
unit-tested offline. This module adapts a Runtime invocation into that call and
guarantees two things about the deployed process.

**It never raises.** An unhandled exception inside Runtime surfaces as an opaque
500. Step Functions can branch on `{"status": "error"}`; it cannot branch on a
stack trace it never receives.

**It refuses to serve without span redaction.** Hard rule 8: absence of the
`gen_ai_unredacted_attributes=` token disables redaction entirely and exports
every prompt and tool result — the full household record — to CloudWatch. This is
one of the few places where failing closed means refusing to start, because the
alternative is a silent, continuous PII leak that nothing downstream would catch.

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
        f"contain 'gen_ai_unredacted_attributes=' (expected {REDACTION_TOKEN!r}). "
        "Refusing to start, because without it the full household record exports "
        "to CloudWatch."
    )

app = BedrockAgentCoreApp()

# No-op on Runtime, which sets AGENT_OBSERVABILITY_ENABLED and owns the tracer
# provider. Called once at import so a local `python -m runtime_app` still
# exports traces.
setup_telemetry()


@app.entrypoint
def invoke(payload, context=None) -> dict:
    """Process one case. Never raises."""
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
```

- [ ] **Step 4: Make the redaction token available to the whole suite**

`runtime_app` refuses to import without it, so add an autouse session fixture to
`tests/conftest.py`:

```python
@pytest.fixture(autouse=True, scope="session")
def _span_redaction_configured():
    """`runtime_app` refuses to import without the redaction token (hard rule
    8). Set it for the suite so importing that module in a test does not depend
    on the caller remembering."""
    os.environ.setdefault(
        "OTEL_SEMCONV_STABILITY_OPT_IN",
        "gen_ai_latest_experimental,gen_ai_unredacted_attributes=",
    )
```

Add `import os` and `import pytest` to `conftest.py` if absent. **Do not remove or alter any
existing fixture there.**

Run: `.venv/bin/python -m pytest tests/test_runtime_app.py -v`
Expected: PASS, 5 tests.

- [ ] **Step 5: Write the `Dockerfile` and `.dockerignore`**

Modelled on the CLI's own reference Dockerfile, minus the two defaults Grace rejects.

`Dockerfile`:

```dockerfile
# From public.ecr.aws, not Docker Hub: the CLI's own template uses the ECR
# mirror to avoid Docker Hub rate limits in CodeBuild. Python 3.12 to match the
# local toolchain — `agentcore add agent` records runtimeVersion PYTHON_3_14 by
# default, but the image is what actually runs.
FROM public.ecr.aws/docker/library/python:3.12-slim

RUN pip install --no-cache-dir uv

WORKDIR /app

ENV UV_SYSTEM_PYTHON=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_NO_PROGRESS=1 \
    PYTHONUNBUFFERED=1

# Dependencies before source, so a code change does not reinstall the world.
COPY pyproject.toml ./
RUN uv pip install --system --no-cache .

# `infra/` is required, not optional: grace/cases/dynamo_store.py and
# grace/memory.py both import infra.naming. Omitting it fails at container
# start, not at build.
COPY grace/ ./grace/
COPY infra/ ./infra/
COPY fixtures/ ./fixtures/
COPY runtime_app.py ./

# Hard rule 8. The trailing `=` is load-bearing: the value lists what to leave
# UNREDACTED, so an empty value means "redact everything". Without the token,
# redaction is off entirely and the full household record exports to CloudWatch.
# `runtime_app` refuses to start if this is missing, so a bad edit here fails
# loudly at container start rather than leaking silently.
ENV OTEL_SEMCONV_STABILITY_OPT_IN=gen_ai_latest_experimental,gen_ai_unredacted_attributes=
# Groups Grace's agents under one name in the CloudWatch GenAI dashboard; the
# default `strands-agents` is indistinguishable from any other Strands app.
ENV OTEL_SERVICE_NAME=grace
ENV GRACE_STORE=dynamodb

RUN useradd -m -u 1000 bedrock_agentcore
USER bedrock_agentcore

# AgentCore Runtime service contract: 8080 HTTP, 8000 MCP, 9000 A2A.
EXPOSE 8080

# NOT `opentelemetry-instrument` — that is the CLI template's default and
# requires aws-opentelemetry-distro, which CLAUDE.md forbids because Runtime
# instruments itself.
CMD ["python", "-m", "runtime_app"]
```

`.dockerignore`:

```text
.venv/
.git/
.pytest_cache/
__pycache__/
*.pyc
docs/
evals/
tests/
agentcore/
```

- [ ] **Step 6: Build and smoke-test locally with Podman**

```bash
export DOCKER_HOST="$(podman machine inspect \
  --format '{{.ConnectionInfo.PodmanSocket.Path}}' podman-machine-default)"
podman build --platform linux/arm64 -t grace-local-test .
podman run --rm grace-local-test python -c "import runtime_app; print('imports OK')"
```

Expected: `imports OK`. The `ENV` line in the Dockerfile satisfies the redaction guard.

Then confirm the guard actually refuses when the token is absent:

```bash
podman run --rm -e OTEL_SEMCONV_STABILITY_OPT_IN=gen_ai_latest_experimental \
  grace-local-test python -c "import runtime_app" 2>&1 | tail -3
```

Expected: a `RuntimeError` naming `gen_ai_unredacted_attributes=`. **A container that starts here is
a bug** — it means hard rule 8's guard is not wired.

- [ ] **Step 7: Register the agent and deploy**

Grace is **BYO code**, not a scaffolded template — `--type create` would generate a fresh agent and
ignore `grace/` entirely. Confirm each flag against `--help` first; these were verified on 0.28.1:

```bash
agentcore create --project-name grace --no-agent --defaults
agentcore add agent --name grace --type byo --build Container --language Python \
  --framework Strands --model-provider Bedrock \
  --code-location . --entrypoint runtime_app.py --protocol HTTP
agentcore deploy --diff        # inspect first
agentcore deploy -y --verbose  # then deploy
```

`create` writes an `agentcore/` directory containing a TypeScript CDK app and `agentcore.json` (a
declarative manifest with `managedBy: CDK`). Commit it — it is deployment state, not a build
artifact. Add `agentcore/cdk/node_modules/` and `agentcore/.env.local` to `.gitignore`.

Record the commands that actually worked in `docs/runbook-deploy.md`, including any flag that
differed. The next person reads that file, not this plan.

- [ ] **Step 8: Confirm the runtime reached READY**

```bash
aws bedrock-agentcore-control list-agent-runtimes --region us-east-1 \
  --query 'agentRuntimes[?starts_with(agentRuntimeName, `grace`)]'
```

Expected: one entry, `"status": "READY"`. On `CREATE_FAILED`, read the CloudWatch log group Runtime
created; the usual causes are a file missing from the image (see the `infra/` note in Step 5) or a
dependency that does not resolve on arm64.

- [ ] **Step 9: Invoke the deployed runtime on one case**

```bash
.venv/bin/python -c "
import boto3, json, uuid
from infra import naming
ctl = boto3.client('bedrock-agentcore-control', region_name=naming.REGION)
arn = next(r['agentRuntimeArn'] for r in ctl.list_agent_runtimes()['agentRuntimes']
           if r['agentRuntimeName'].startswith('grace'))
c = boto3.client('bedrock-agentcore', region_name=naming.REGION)
session = f'grace-c-010-{uuid.uuid4()}'
assert len(session) >= 33, len(session)   # Runtime requires 33+ characters
r = c.invoke_agent_runtime(
    agentRuntimeArn=arn, runtimeSessionId=session,
    payload=json.dumps({'case_id': 'c-010', 'today': '2026-10-01'}).encode(),
)
body = json.loads(r['response'].read())
print(body)
assert body['status'] == 'escalated', body
assert body.get('filed') is not True
print('OK: c-010 escalated on the deployed runtime, nothing filed')
"
```

Expected: `status: escalated`. `c-010` is missing `proof_of_residency`, so `acted` here means the gate
is not running in the deployed image — stop and investigate.

- [ ] **Step 10: Confirm the ledger row landed in DynamoDB**

```bash
aws dynamodb query --table-name grace-cases --region us-east-1 \
  --key-condition-expression 'pk = :pk' \
  --expression-attribute-values '{":pk":{"S":"CASE#c-010"}}' \
  --query 'Items[].{sk:sk.S,kind:kind.S,trace:d_trace_id.S}' --output table
```

Expected: `LEDGER#` rows plus at least one `ESCALATION#` row. **The `trace` column should show a
32-hex value, not empty** — that is Task 9 of Plan 1's correlation working where a tracer is actually
configured (locally it is `None`).

- [ ] **Step 11: Run the whole suite**

Run: `.venv/bin/python -m pytest`
Expected: PASS — **421 tests**. Report the real number.

- [ ] **Step 12: Commit**

```bash
git add Dockerfile .dockerignore runtime_app.py tests/test_runtime_app.py \
        tests/conftest.py docs/runbook-deploy.md .gitignore agentcore/
git commit -m "feat: containerize and deploy the harness to AgentCore Runtime"
```

---

## Task 8: Lambda, Step Functions, EventBridge

The sweep loop moves out of Python. Grace's premise is that it runs unattended in the background, so
this task is what makes that literally true rather than a description.

**Files:**

- Create: `infra/lambda_src/handler.py`, `infra/provision_lambda.py`,
  `infra/provision_stepfunctions.py`, `infra/provision_eventbridge.py`
- Test: `tests/test_lambda_handler.py`, `tests/test_state_machine.py`

**Interfaces:**

- Consumes: `infra.naming`, `infra.provision_iam.provision`
- Produces:
  - `infra.lambda_src.handler.lambda_handler(event: dict, context: object) -> dict`
  - `infra.provision_lambda.provision(...) -> str` (function ARN)
  - `infra.provision_stepfunctions.definition(account_id: str, lambda_arn: str) -> dict`
  - `infra.provision_stepfunctions.provision(...) -> str` (state machine ARN)
  - `infra.provision_eventbridge.provision(...) -> str` (rule ARN)

### The Catch branch is the fail-closed rule as infrastructure

A Lambda that times out, or a runtime that dies, produces no verdict. "No verdict" must become "a
human looks at it," never "nothing happened." Task 7 of Plan 1 established the precedent: a graph
node timeout is fail-fast, raises out of the call, and produced a sweep *error* with no escalation
row — the family silently fell off the list. Same principle, one layer up.

`maxConcurrency: 3` is considered, not a default. Twelve concurrent cases each open a graph
invocation, and the two swarm-routed cases alone cost ~18–19 Bedrock invocations each; twelve at once
invites the throttling the retry policy then has to absorb.

**The definition below is pre-verified.** It was validated against the real
`stepfunctions:validate_state_machine_definition` API before this task was written and returned
`result: OK` with zero diagnostics — including the `States.Format` intrinsics, the
`$$.State.EnteredTime` context reference, and the `dynamodb:putItem` parameters nested inside the
Map's `ItemProcessor`. If your version stops validating, you changed something: re-run the validator
rather than guessing, since it reports the exact path of the offending field.

```bash
.venv/bin/python -c "
import boto3, json
from infra import provision_stepfunctions as p
sf = boto3.client('stepfunctions', region_name='us-east-1')
r = sf.validate_state_machine_definition(
    definition=json.dumps(p.definition('<AWS_ACCOUNT_ID>', 'arn:aws:lambda:us-east-1:<AWS_ACCOUNT_ID>:function:grace-invoke-case')),
    type='STANDARD')
print(r['result'])
for d in r.get('diagnostics', []): print(d['severity'], d['code'], d.get('location',''), d['message'])
"
```

- [ ] **Step 1: Write the failing Lambda handler test**

Create `tests/test_lambda_handler.py`:

```python
"""The Lambda is a thin adapter: one case in, one outcome out.

It must not contain classification logic. The gate's verdict comes from the
runtime; a Lambda that second-guessed it would be a second decision point with no
gate behind it.
"""

from __future__ import annotations

import json

from infra.lambda_src import handler as lambda_handler_module


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
        client=client, runtime_arn="arn:aws:bedrock-agentcore:us-east-1:1:runtime/grace",
    )
    assert out["status"] == "escalated"
    assert out["case_id"] == "c-011"
    payload = json.loads(client.calls[0]["payload"])
    assert payload["case_id"] == "c-011"


def test_the_session_id_is_at_least_33_characters():
    """A Runtime constraint: `runtimeSessionId` must be 33+ characters, and a
    shorter one is rejected at invoke time rather than at deploy."""
    client = FakeRuntimeClient({"status": "acted", "case_id": "c-001"})
    lambda_handler_module.lambda_handler(
        {"case_id": "c-001"}, None, client=client,
        runtime_arn="arn:aws:bedrock-agentcore:us-east-1:1:runtime/grace",
    )
    assert len(client.calls[0]["runtimeSessionId"]) >= 33


def test_each_case_gets_its_own_session():
    """One case per session, so `AuthorityGate._seen` can never span two
    households — the per-instance isolation Task 6 established, made
    structural."""
    client = FakeRuntimeClient({"status": "acted", "case_id": "c-001"})
    for case_id in ("c-001", "c-002"):
        lambda_handler_module.lambda_handler(
            {"case_id": case_id}, None, client=client,
            runtime_arn="arn:aws:bedrock-agentcore:us-east-1:1:runtime/grace",
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
        {"case_id": "c-012"}, None, client=Boom(),
        runtime_arn="arn:aws:bedrock-agentcore:us-east-1:1:runtime/grace",
    )
    assert out["status"] == "error"
    assert out["case_id"] == "c-012"
```

- [ ] **Step 2: Run it to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_lambda_handler.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'infra.lambda_src'`.

- [ ] **Step 3: Write `infra/lambda_src/handler.py`**

Create `infra/lambda_src/__init__.py` (empty) and `infra/lambda_src/handler.py`:

```python
"""One case → one Runtime invocation → one outcome.

Deliberately outside the `grace` package: this is packaged into a zip and must
import with only boto3 available, which the Lambda runtime provides. It contains
no classification logic — the gate's verdict comes from the runtime, and a Lambda
that second-guessed it would be a second decision point with no gate behind it.
"""

from __future__ import annotations

import json
import os
import uuid

import boto3

_REGION = os.getenv("AWS_REGION", "us-east-1")
_RUNTIME_ARN_ENV = "GRACE_RUNTIME_ARN"
_DEFAULT_TODAY = "2026-10-01"


def lambda_handler(event: dict, context: object, client=None, runtime_arn: str | None = None) -> dict:
    """Invoke the deployed runtime for exactly one case.

    `client` and `runtime_arn` are injectable so this is testable offline; in
    Lambda both come from the environment.
    """
    case_id = str(event.get("case_id", "")).strip()
    today = str(event.get("today") or _DEFAULT_TODAY)
    client = client or boto3.client("bedrock-agentcore", region_name=_REGION)
    runtime_arn = runtime_arn or os.environ[_RUNTIME_ARN_ENV]

    # A fresh session per case. `runtimeSessionId` must be 33+ characters — a
    # shorter one is rejected at invoke time — and a distinct session per case is
    # what keeps `AuthorityGate._seen` from ever spanning two households.
    session_id = f"grace-{case_id}-{uuid.uuid4()}"

    try:
        response = client.invoke_agent_runtime(
            agentRuntimeArn=runtime_arn,
            runtimeSessionId=session_id,
            payload=json.dumps({"case_id": case_id, "today": today}).encode(),
        )
        return dict(json.loads(response["response"].read()))
    except Exception as exc:  # noqa: BLE001 — name the case, always
        # Step Functions' Catch would handle a raise, but a structured error
        # names the family so the escalation row can identify it.
        return {"status": "error", "case_id": case_id, "detail": str(exc)}
```

- [ ] **Step 4: Run the handler tests**

Run: `.venv/bin/python -m pytest tests/test_lambda_handler.py -v`
Expected: PASS, 4 tests.

- [ ] **Step 5: Write the failing state-machine test**

Create `tests/test_state_machine.py`:

```python
"""The state machine definition, asserted as data.

Cheap, and it catches the two things most likely to be wrong: a Catch branch that
does not write an escalation row, and a concurrency setting that invites the
throttling its own retry policy then absorbs.
"""

from __future__ import annotations

from infra import naming, provision_stepfunctions

ACCOUNT = "123456789012"
LAMBDA_ARN = f"arn:aws:lambda:us-east-1:{ACCOUNT}:function:{naming.LAMBDA}"


def _definition():
    return provision_stepfunctions.definition(ACCOUNT, LAMBDA_ARN)


def test_the_map_state_bounds_concurrency():
    """Twelve concurrent cases each open a graph invocation, and the two
    swarm-routed cases cost ~18-19 Bedrock invocations each. Unbounded
    concurrency invites the throttling the retry policy then has to absorb."""
    m = _next_map(_definition())
    assert m["MaxConcurrency"] == 3


def test_every_case_branch_has_a_catch_that_writes_an_escalation():
    """The fail-closed rule as infrastructure. A Lambda that times out
    produces no verdict, and 'no verdict' must become 'a human looks at it',
    never 'nothing happened'."""
    m = _next_map(_definition())
    task = next(s for s in m["ItemProcessor"]["States"].values() if s["Type"] == "Task")
    assert task.get("Catch"), "a case branch with no Catch loses the family silently"
    targets = {c["Next"] for c in task["Catch"]}
    states = m["ItemProcessor"]["States"]
    for target in targets:
        # The Catch target must actually write a row, not merely succeed.
        assert "dynamodb" in states[target].get("Resource", ""), states[target]


def test_the_retry_policy_covers_throttling():
    """Bedrock throttling is the expected transient failure at this
    concurrency, and it must not spend the Catch branch."""
    m = _next_map(_definition())
    task = next(s for s in m["ItemProcessor"]["States"].values() if s["Type"] == "Task")
    errors = {e for r in task.get("Retry", []) for e in r["ErrorEquals"]}
    assert any("Throttl" in e or "TooManyRequests" in e for e in errors), errors


def test_the_escalation_row_carries_the_pending_status():
    """It must land on the escalation-queue GSI, or the dashboard cannot
    find it."""
    import json

    source = json.dumps(_definition())
    assert naming.PENDING in source


def _next_map(definition):
    return next(s for s in definition["States"].values() if s["Type"] == "Map")
```

- [ ] **Step 6: Run it to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_state_machine.py -v`
Expected: FAIL — `ImportError: cannot import name 'provision_stepfunctions' from 'infra'`.

- [ ] **Step 7: Write `infra/provision_lambda.py`**

```python
"""Package and create the `grace-invoke-case` function. Idempotent."""

from __future__ import annotations

import io
import time
import zipfile
from pathlib import Path

import boto3
from botocore.exceptions import ClientError

from infra import naming, provision_iam

_SOURCE = Path(__file__).parent / "lambda_src" / "handler.py"


def _zip_bytes() -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("handler.py", _SOURCE.read_text())
    return buffer.getvalue()


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
            Timeout=900,
            MemorySize=256,
            Environment=environment,
            Tags=naming.TAGS,
        )
    except ClientError as exc:
        if exc.response["Error"]["Code"] != "ResourceConflictException":
            raise
        client.update_function_code(FunctionName=naming.LAMBDA, ZipFile=code)
        # A create-then-update sequence needs the code update to settle before
        # the configuration update is accepted.
        for _ in range(30):
            state = client.get_function_configuration(FunctionName=naming.LAMBDA)
            if state.get("LastUpdateStatus") != "InProgress":
                break
            time.sleep(2)
        client.update_function_configuration(
            FunctionName=naming.LAMBDA, Role=role_arn, Timeout=900,
            Environment=environment,
        )
        response = client.get_function_configuration(FunctionName=naming.LAMBDA)
    return str(response["FunctionArn"])
```

- [ ] **Step 8: Write `infra/provision_stepfunctions.py`**

```python
"""The `grace-sweep` state machine: a Map over the twelve cases.

The Catch branch is the fail-closed rule expressed as infrastructure — see the
note above this task in the plan.
"""

from __future__ import annotations

import json

import boto3
from botocore.exceptions import ClientError

from infra import naming, provision_iam

# The twelve fixture cases. Listed rather than discovered because the state
# machine is provisioned from outside the Grace package, and because the demo's
# claim is about these twelve specifically.
CASE_IDS = [f"c-{n:03d}" for n in range(1, 13)]


def definition(account_id: str, lambda_arn: str) -> dict:
    """Build the state machine definition."""
    table_arn_name = naming.TABLE
    return {
        "Comment": "Grace daily sweep: one runtime invocation per household",
        "StartAt": "SweepCases",
        "States": {
            "SweepCases": {
                "Type": "Map",
                "ItemsPath": "$.case_ids",
                # Bounded on purpose: see the plan's note. Twelve at once
                # invites the throttling the Retry below then has to absorb.
                "MaxConcurrency": 3,
                "ItemSelector": {
                    "case_id.$": "$$.Map.Item.Value",
                    "today.$": "$.today",
                },
                "ItemProcessor": {
                    "ProcessorConfig": {"Mode": "INLINE"},
                    "StartAt": "InvokeCase",
                    "States": {
                        "InvokeCase": {
                            "Type": "Task",
                            "Resource": "arn:aws:states:::lambda:invoke",
                            "Parameters": {
                                "FunctionName": lambda_arn,
                                "Payload.$": "$",
                            },
                            "OutputPath": "$.Payload",
                            "Retry": [{
                                "ErrorEquals": [
                                    "Lambda.TooManyRequestsException",
                                    "Lambda.ServiceException",
                                    "ThrottlingException",
                                    "States.TaskFailed",
                                ],
                                "IntervalSeconds": 5,
                                "MaxAttempts": 2,
                                "BackoffRate": 2.0,
                            }],
                            "Catch": [{
                                "ErrorEquals": ["States.ALL"],
                                "ResultPath": "$.error",
                                "Next": "RecordEscalation",
                            }],
                            "End": True,
                        },
                        "RecordEscalation": {
                            # No verdict is not the same as nothing happened.
                            # A family whose case failed must still reach a human.
                            "Type": "Task",
                            "Resource": "arn:aws:states:::dynamodb:putItem",
                            "Parameters": {
                                "TableName": table_arn_name,
                                "Item": {
                                    "pk": {"S.$": "States.Format('CASE#{}', $.case_id)"},
                                    "sk": {"S.$": "States.Format('ESCALATION#{}', $$.State.EnteredTime)"},
                                    "case_id": {"S.$": "$.case_id"},
                                    "status": {"S": naming.PENDING},
                                    "escalated_at": {"S.$": "$$.State.EnteredTime"},
                                    "reason": {"S": "The automated run failed before reaching a verdict. A caseworker must review this case."},
                                    "question": {"S": "Why did this case fail, and does the household still qualify?"},
                                    "deadline": {"S": ""},
                                },
                            },
                            "ResultPath": None,
                            "Next": "ReportFailure",
                        },
                        "ReportFailure": {
                            "Type": "Pass",
                            "Parameters": {
                                "status": "error",
                                "case_id.$": "$.case_id",
                                "detail": "run failed; escalation recorded",
                            },
                            "End": True,
                        },
                    },
                },
                "End": True,
            }
        },
    }


def provision(lambda_arn: str, client=None, role_arn: str | None = None,
              account_id: str | None = None) -> str:
    """Create or update the state machine; return its ARN."""
    client = client or boto3.client("stepfunctions", region_name=naming.REGION)
    if account_id is None:
        account_id = boto3.client("sts").get_caller_identity()["Account"]
    if role_arn is None:
        role_arn = provision_iam.provision()["stepfunctions"]

    body = json.dumps(definition(account_id, lambda_arn))
    arn = f"arn:aws:states:{naming.REGION}:{account_id}:stateMachine:{naming.STATE_MACHINE}"
    try:
        response = client.create_state_machine(
            name=naming.STATE_MACHINE, definition=body, roleArn=role_arn,
            tags=[{"key": k, "value": v} for k, v in naming.TAGS.items()],
        )
        return str(response["stateMachineArn"])
    except ClientError as exc:
        if exc.response["Error"]["Code"] != "StateMachineAlreadyExists":
            raise
        client.update_state_machine(stateMachineArn=arn, definition=body, roleArn=role_arn)
        return arn
```

- [ ] **Step 9: Write `infra/provision_eventbridge.py`**

```python
"""Daily schedule. What makes 'runs unattended in the background' literal."""

from __future__ import annotations

import json

import boto3

from infra import naming, provision_iam, provision_stepfunctions

# 09:00 UTC. The demo triggers manually, so the exact hour is not load-bearing.
SCHEDULE = "cron(0 9 * * ? *)"


def provision(state_machine_arn: str, client=None, role_arn: str | None = None) -> str:
    """Create or update the daily rule; return its ARN."""
    client = client or boto3.client("events", region_name=naming.REGION)
    if role_arn is None:
        role_arn = provision_iam.provision()["eventbridge"]

    rule = client.put_rule(
        Name=naming.SCHEDULE_RULE,
        ScheduleExpression=SCHEDULE,
        State="ENABLED",
        Description="Grace daily recertification sweep",
        Tags=[{"Key": k, "Value": v} for k, v in naming.TAGS.items()],
    )
    client.put_targets(
        Rule=naming.SCHEDULE_RULE,
        Targets=[{
            "Id": "grace-sweep",
            "Arn": state_machine_arn,
            "RoleArn": role_arn,
            # The pinned date travels with the event: a `date.today()` anywhere
            # in this system turns the 9/3 demo into 8/4 from 2026-10-31.
            "Input": json.dumps({
                "case_ids": provision_stepfunctions.CASE_IDS,
                "today": "2026-10-01",
            }),
        }],
    )
    return str(rule["RuleArn"])
```

- [ ] **Step 10: Run the state-machine tests**

Run: `.venv/bin/python -m pytest tests/test_state_machine.py -v`
Expected: PASS, 4 tests.

- [ ] **Step 11: Provision all three and run the sweep for real**

```bash
.venv/bin/python -c "
import boto3, json
from infra import naming, provision_lambda, provision_stepfunctions, provision_eventbridge
ctl = boto3.client('bedrock-agentcore-control', region_name=naming.REGION)
runtime_arn = next(r['agentRuntimeArn'] for r in ctl.list_agent_runtimes()['agentRuntimes']
                   if r['agentRuntimeName'].startswith('grace'))
fn = provision_lambda.provision(runtime_arn); print('lambda:', fn)
sm = provision_stepfunctions.provision(fn); print('state machine:', sm)
rule = provision_eventbridge.provision(sm); print('rule:', rule)
"
```

Then start one execution and wait:

```bash
.venv/bin/python -c "
import boto3, json, time
from infra import naming, provision_stepfunctions
sf = boto3.client('stepfunctions', region_name=naming.REGION)
acct = boto3.client('sts').get_caller_identity()['Account']
arn = f'arn:aws:states:{naming.REGION}:{acct}:stateMachine:{naming.STATE_MACHINE}'
ex = sf.start_execution(stateMachineArn=arn, input=json.dumps(
    {'case_ids': provision_stepfunctions.CASE_IDS, 'today': '2026-10-01'}))['executionArn']
while True:
    d = sf.describe_execution(executionArn=ex)
    if d['status'] != 'RUNNING':
        break
    time.sleep(15)
print('status:', d['status'])
out = json.loads(d.get('output') or '[]')
from collections import Counter
print(Counter(o.get('status') for o in out))
"
```

Expected: `SUCCEEDED`, and a counter reading **`{'acted': 9, 'escalated': 3}`**. That is the demo's
central claim, executed against deployed infrastructure. If the counts differ, do not adjust the
counter — read the escalation rows and find out which case moved and why. A clean case escalating
means the gate got stricter; one of `c-010`/`c-011`/`c-012` acting means it got looser. Either is a
bug worth stopping for.

- [ ] **Step 12: Run the whole suite**

Run: `.venv/bin/python -m pytest`
Expected: PASS — **427 tests**. Report the real number.

- [ ] **Step 13: Commit**

```bash
git add infra/lambda_src/ infra/provision_lambda.py infra/provision_stepfunctions.py \
        infra/provision_eventbridge.py tests/test_lambda_handler.py tests/test_state_machine.py
git commit -m "feat: scheduled Step Functions sweep, one runtime session per household"
```

---

## Task 9: The alarm that catches the failure metrics cannot see

**Files:**

- Create: `infra/provision_alarm.py`, `infra/provision_all.py`, `infra/teardown.py`
- Test: `tests/test_infra_alarm.py`

**Interfaces:**

- Consumes: `infra.naming`, every other `provision_*` module
- Produces: `infra.provision_alarm.provision(...) -> str` (alarm ARN),
  `infra.provision_all.main() -> dict[str, str]`, `infra.teardown.main() -> None`

### Why the alarm is on escalation count, not error rate

The failure this system exists to prevent is **acting when it should have escalated**. That produces
no error, no throttle, and no elevated latency. It looks exactly like success — which is why an
error-rate alarm would not have caught a single one of the defects found in Plan 1.

The fixture set is fixed at twelve cases, three of which must escalate. A sweep that escalates fewer
than three is a gate that got looser. Standard `SystemErrors` / `Throttles` / p99 latency alarms are
worth having as hygiene, and they are *not* what this task is about.

- [ ] **Step 1: Write the failing alarm test**

Create `tests/test_infra_alarm.py`:

```python
"""The alarm's shape, asserted as data.

The comparison operator and the treatment of missing data are both easy to get
backwards, and both failures are silent — an alarm that never fires looks
identical to a system that never breaks.
"""

from __future__ import annotations

from infra import naming, provision_alarm


def test_the_alarm_fires_when_escalations_fall_below_three():
    """Fewer than three escalations means the gate got looser. Acting when
    Grace should have escalated produces no error, no throttle, and no
    latency spike, so this is the only alarm that can catch it."""
    spec = provision_alarm.alarm_spec()
    assert spec["Threshold"] == 3
    assert spec["ComparisonOperator"] == "LessThanThreshold"


def test_missing_data_is_treated_as_breaching():
    """A sweep that did not run at all published no metric. Treating that as
    'not breaching' would make a silent, total failure look healthy — which is
    the same class of bug as the alarm being backwards."""
    spec = provision_alarm.alarm_spec()
    assert spec["TreatMissingData"] == "breaching"


def test_the_alarm_is_named_for_grace():
    assert provision_alarm.alarm_spec()["AlarmName"] == naming.ALARM


def test_one_datapoint_is_enough_to_alarm():
    """The sweep runs once a day. Requiring two datapoints would delay the
    signal by 24 hours."""
    spec = provision_alarm.alarm_spec()
    assert spec["EvaluationPeriods"] == 1
```

- [ ] **Step 2: Run it to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_infra_alarm.py -v`
Expected: FAIL — `ImportError: cannot import name 'provision_alarm' from 'infra'`.

- [ ] **Step 3: Write `infra/provision_alarm.py`**

```python
"""One alarm, on the invariant rather than on errors.

Read the plan's note above this task before changing the threshold: the failure
mode this catches produces no error, no throttle, and no latency spike, so it is
invisible to every conventional alarm.

The metric is published by a metric filter over the state machine's own logs,
counting escalated outcomes. A metric filter rather than a custom `PutMetricData`
call from inside Grace, because the count must come from what the system actually
*reported*, not from a number Grace chose to publish about itself.
"""

from __future__ import annotations

import boto3

from infra import naming

_NAMESPACE = "Grace"
_METRIC = "EscalatedCases"
_LOG_GROUP = f"/aws/vendedlogs/states/{naming.STATE_MACHINE}-Logs"


def alarm_spec() -> dict:
    """The alarm definition, as data so it is testable without AWS."""
    return {
        "AlarmName": naming.ALARM,
        "AlarmDescription": (
            "Fewer than three cases escalated in a sweep. The fixture set is 12 "
            "cases, 3 of which must escalate (c-010 missing document, c-011 "
            "material income change, c-012 source conflict). A lower count means "
            "the authority gate got looser — a failure that produces no error, no "
            "throttle, and no latency spike, and therefore cannot be caught by any "
            "conventional alarm."
        ),
        "Namespace": _NAMESPACE,
        "MetricName": _METRIC,
        "Statistic": "Sum",
        "Period": 86400,
        "EvaluationPeriods": 1,
        "Threshold": 3,
        "ComparisonOperator": "LessThanThreshold",
        # A sweep that never ran published no metric. Treating that as healthy
        # would hide a total failure — the same class of bug as the comparison
        # being backwards.
        "TreatMissingData": "breaching",
    }


def provision(logs_client=None, cw_client=None) -> str:
    """Create the log group, the metric filter, and the alarm. Idempotent."""
    logs_client = logs_client or boto3.client("logs", region_name=naming.REGION)
    cw_client = cw_client or boto3.client("cloudwatch", region_name=naming.REGION)

    try:
        logs_client.create_log_group(logGroupName=_LOG_GROUP)
    except logs_client.exceptions.ResourceAlreadyExistsException:
        pass

    logs_client.put_metric_filter(
        logGroupName=_LOG_GROUP,
        filterName="grace-escalated-cases",
        filterPattern='{ $.status = "escalated" }',
        metricTransformations=[{
            "metricName": _METRIC,
            "metricNamespace": _NAMESPACE,
            "metricValue": "1",
            "defaultValue": 0.0,
        }],
    )

    cw_client.put_metric_alarm(**alarm_spec())
    account = boto3.client("sts").get_caller_identity()["Account"]
    return f"arn:aws:cloudwatch:{naming.REGION}:{account}:alarm:{naming.ALARM}"
```

- [ ] **Step 4: Run the alarm tests**

Run: `.venv/bin/python -m pytest tests/test_infra_alarm.py -v`
Expected: PASS, 4 tests.

- [ ] **Step 5: Write `infra/provision_all.py`**

```python
"""Provision everything, in dependency order. Idempotent end to end.

The order matters: IAM before anything that needs a role, the table before the
runtime that writes to it, the runtime before the Lambda that invokes it, the
Lambda before the state machine, the state machine before the schedule.

The runtime itself is NOT created here — `agentcore deploy` owns that, because it
builds and pushes a container image. This script reads the deployed runtime's ARN
and wires everything else around it.
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


def _runtime_arn() -> str:
    client = boto3.client("bedrock-agentcore-control", region_name=naming.REGION)
    for runtime in client.list_agent_runtimes().get("agentRuntimes", []):
        if runtime["agentRuntimeName"].startswith(naming.RUNTIME):
            if runtime.get("status") != "READY":
                raise RuntimeError(
                    f"runtime {runtime['agentRuntimeName']} is "
                    f"{runtime.get('status')}, not READY — deploy it first "
                    "with `agentcore deploy`"
                )
            return str(runtime["agentRuntimeArn"])
    raise RuntimeError(
        "no Grace runtime found. Run `agentcore deploy` (Task 7) before this."
    )


def main() -> dict[str, str]:
    created: dict[str, str] = {}
    created["table"] = provision_dynamodb.provision()
    created["memory_id"] = provision_memory.provision()
    roles = provision_iam.provision()
    created.update({f"role_{k}": v for k, v in roles.items()})
    runtime = _runtime_arn()
    created["runtime"] = runtime
    created["lambda"] = provision_lambda.provision(runtime, role_arn=roles["lambda"])
    created["state_machine"] = provision_stepfunctions.provision(
        created["lambda"], role_arn=roles["stepfunctions"]
    )
    created["schedule"] = provision_eventbridge.provision(
        created["state_machine"], role_arn=roles["eventbridge"]
    )
    created["alarm"] = provision_alarm.provision()
    return created


if __name__ == "__main__":
    for key, value in main().items():
        print(f"{key}: {value}")
```

- [ ] **Step 6: Write `infra/teardown.py`**

```python
"""Delete what `provision_all` created. Grace resources only.

Every deletion is filtered by the `grace-` prefix. This account holds unrelated
projects' resources (`theagentorg_*`, `rosettacloud_*`), and a teardown that
matched on anything looser would delete someone else's work.

The DynamoDB table is NOT deleted by default: it holds the audit trail, and
`--include-table` must be passed explicitly.
"""

from __future__ import annotations

import argparse

import boto3

from infra import naming


def main(include_table: bool = False) -> None:
    region = naming.REGION

    events = boto3.client("events", region_name=region)
    try:
        events.remove_targets(Rule=naming.SCHEDULE_RULE, Ids=["grace-sweep"])
        events.delete_rule(Name=naming.SCHEDULE_RULE)
        print(f"deleted rule {naming.SCHEDULE_RULE}")
    except events.exceptions.ResourceNotFoundException:
        pass

    sf = boto3.client("stepfunctions", region_name=region)
    account = boto3.client("sts").get_caller_identity()["Account"]
    try:
        sf.delete_state_machine(
            stateMachineArn=(
                f"arn:aws:states:{region}:{account}:stateMachine:{naming.STATE_MACHINE}"
            )
        )
        print(f"deleted state machine {naming.STATE_MACHINE}")
    except Exception:  # noqa: BLE001 — already gone is success
        pass

    lam = boto3.client("lambda", region_name=region)
    try:
        lam.delete_function(FunctionName=naming.LAMBDA)
        print(f"deleted function {naming.LAMBDA}")
    except lam.exceptions.ResourceNotFoundException:
        pass

    cw = boto3.client("cloudwatch", region_name=region)
    cw.delete_alarms(AlarmNames=[naming.ALARM])
    print(f"deleted alarm {naming.ALARM}")

    iam = boto3.client("iam")
    for purpose in ("runtime", "lambda", "stepfunctions", "eventbridge"):
        role = f"grace-{purpose}-role"
        try:
            iam.delete_role_policy(RoleName=role, PolicyName=f"grace-{purpose}-policy")
        except iam.exceptions.NoSuchEntityException:
            pass
        try:
            iam.delete_role(RoleName=role)
            print(f"deleted role {role}")
        except iam.exceptions.NoSuchEntityException:
            pass

    if include_table:
        ddb = boto3.client("dynamodb", region_name=region)
        try:
            ddb.delete_table(TableName=naming.TABLE)
            print(f"deleted table {naming.TABLE} (the audit trail is gone)")
        except ddb.exceptions.ResourceNotFoundException:
            pass
    else:
        print(f"kept table {naming.TABLE} — pass --include-table to delete the ledger")

    print(
        "NOTE: the AgentCore runtime and memory are not deleted here. Use "
        "`agentcore destroy` / `delete-memory` deliberately."
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(prog="teardown")
    parser.add_argument("--include-table", action="store_true",
                        help="also delete grace-cases (destroys the audit trail)")
    args = parser.parse_args()
    main(include_table=args.include_table)
```

- [ ] **Step 7: Run the whole suite**

Run: `.venv/bin/python -m pytest`
Expected: PASS — **431 tests**. Report the real number.

- [ ] **Step 8: Provision everything and confirm idempotence**

```bash
.venv/bin/python -m infra.provision_all
.venv/bin/python -m infra.provision_all   # must succeed identically
```

Expected: the same ARNs printed twice.

- [ ] **Step 9: Commit**

```bash
git add infra/provision_alarm.py infra/provision_all.py infra/teardown.py \
        tests/test_infra_alarm.py
git commit -m "feat: alarm on escalation count, plus one-command provisioning and teardown"
```

---

## Task 10: Deployed verification and the honest README

The last task, and the one the submission is judged on. Nothing new is built; what exists is proven
and described accurately.

**Files:**

- Modify: `README.md`, `CLAUDE.md`, `docs/runbook-deploy.md`
- Create: `docs/deployed-verification.md`

**Interfaces:**

- Consumes: everything
- Produces: recorded evidence for the three claims in §7 of the spec

- [ ] **Step 1: Confirm Plan 1's suite is still untouched in spirit**

```bash
git diff --stat 0e9de29 -- grace/authority.py grace/steering.py grace/graph.py grace/swarm.py
```

Expected: **empty output.** The whole premise of this plan is that the decision path did not change.
If any of those four files differ from the Plan 1 commit, stop and explain why before continuing —
this is the plan's headline claim and it must be true, not approximately true.

- [ ] **Step 2: Run the trajectory evals against the deployed store**

```bash
GRACE_STORE=dynamodb .venv/bin/python -m pytest evals/ -v 2>&1 | tail -20
```

Expected: **23 passed.** These cost real Bedrock inference (~65 model invocations, five graph runs).
A failure in `test_an_escalating_case_is_never_filed` is a genuine safety regression and blocks
submission; a failure in `test_a_clean_case_is_filed` or
`test_an_escalating_case_does_something_rather_than_nothing` is liveness-shaped — re-run once before
concluding anything, since Plan 1 recorded one timeout in five runs under real Bedrock latency.

- [ ] **Step 3: Prove the demo's central claim with the Transaction Search query**

```bash
aws logs start-query --region us-east-1 \
  --log-group-names "aws/spans" \
  --start-time $(( $(date +%s) - 7200 )) --end-time $(date +%s) \
  --query-string 'fields @timestamp, attributes.grace.case_id | filter attributes.grace.gate_decision = "escalate" | stats count() by attributes.grace.case_id'
```

Then fetch the results with `aws logs get-query-results --query-id <id>`.

Expected: exactly **three** distinct case ids — `c-010`, `c-011`, `c-012`. This is the demo's central
claim executed rather than narrated. Record the query and its output in
`docs/deployed-verification.md`.

If the query returns nothing, the span attributes are not reaching CloudWatch. Check
`OTEL_SERVICE_NAME=grace` and that `trace_attributes` are set on the agents — but **do not** move
this assertion to the ledger to make it pass. The ledger already proves what executed; this query is
specifically about the traces, and Appendix E is explicit that an eval assertion never migrates from
the ledger to a span.

- [ ] **Step 4: Confirm no household identity reached a span**

```bash
aws logs start-query --region us-east-1 --log-group-names "aws/spans" \
  --start-time $(( $(date +%s) - 7200 )) --end-time $(date +%s) \
  --query-string 'fields @message | filter @message like /\+1555/ or @message like /Reyes|Nguyen|Okafor/ | limit 5'
```

Expected: **zero results.** Hard rules 8 and 9 together. A hit here is a PII leak into CloudWatch and
blocks submission outright — check that `runtime_app`'s redaction guard is actually running in the
deployed image (Task 7 Step 6 proves the guard works locally; this proves it is in force remotely).

Substitute the real fixture surnames from `fixtures/households.yaml` into the query rather than
guessing them.

- [ ] **Step 5: Write `docs/deployed-verification.md`**

Record, with actual command output pasted in:

1. The deployed sweep's counts (`{'acted': 9, 'escalated': 3}`) and the execution ARN.
2. The three escalation rows from DynamoDB, with their reasons.
3. The Transaction Search query from Step 3 and its three case ids.
4. The zero-result PII query from Step 4.
5. The eval run's `23 passed`.
6. Every resource ARN `provision_all` created.

This file is what a judge reads when they want to know whether the claims are real. Paste output;
do not paraphrase it.

- [ ] **Step 6: Update `README.md` — what shipped, what did not, and why**

Add a deployment section that is accurate about scope. Specifically:

- **Say three AgentCore surfaces, not five.** Runtime, Memory, and the harness shipped. Gateway and
  Identity are deferred, with the reasons from §8 of the spec. Claiming five would be the one thing
  that turns a strong entry into a dishonest one, and a judge who checks will check this.
- Say that SMS is sandboxed (`MaxLimit: 1`, zero origination numbers) and that `TranscriptChannel` is
  the deliberate always-works path — the demo never depends on SMS delivery.
- State the observability claim as Appendix E.8 frames it: the alarm is on **escalation count below
  3**, not on error rate, because acting when Grace should have escalated produces no error, no
  throttle, and no latency spike. That is a more interesting claim than a dashboard screenshot.
- Keep the newly-created-work disclosure: patterns and API knowledge were reused as knowledge from
  prior projects; no files were copied in.

- [ ] **Step 7: Update `CLAUDE.md`**

Add a "Plan 2 is complete" state block in the same style as Plan 1's, plus a
**"What Plan 2 established — follow these"** section carrying at minimum:

- `AgentCoreMemorySessionManager` is in `bedrock-agentcore`, not `strands-agents`; 2 marginal
  packages.
- `PersistenceMode` is `FULL`/`NONE` only, so "write to memory selectively" is not a config option —
  it is a scoping decision.
- The deployed path never resumes an interrupt, and that is *stronger* than the local CLI, because a
  truthy resume response approves the blocked tool.
- Ledger writes convert `float` to `Decimal`, and `bool` must be checked before `int` because
  `isinstance(True, int)` is True.
- The ledger sort key needs a sequence number, not just a timestamp.
- Both stores are tested by one parametrized test body; a separate test file is how they drift.
- `runtime_app` refuses to start without the span-redaction token.

- [ ] **Step 8: Run the fast suite one final time**

Run: `.venv/bin/python -m pytest`
Expected: PASS — **431 tests**, and Plan 1's original 360 among them unchanged.

- [ ] **Step 9: Tick every checkbox in this plan**

Go back through this document and mark each completed step. An unticked box in a finished plan is
indistinguishable from work that was skipped — Plan 1 ended with two such boxes, and reconstructing
whether the work had happened cost real time.

- [ ] **Step 10: Commit**

```bash
git add README.md CLAUDE.md docs/runbook-deploy.md docs/deployed-verification.md \
        docs/superpowers/plans/2026-09-03-grace-agentcore.md
git commit -m "docs: deployed verification evidence and an honest scope statement"
```

---

## Self-Review

**Spec coverage.** Every section of `2026-09-03-grace-agentcore-design.md` maps to a task:

| Spec section | Task |
|---|---|
| §1.1 scope decisions | Bounded by Global Constraints; Gateway/Identity absent by construction |
| §2 architecture | Tasks 7 (Runtime), 8 (SFN/EventBridge/Lambda), 2 (DynamoDB), 5 (Memory) |
| §2.1 new Grace code | Task 2 (`dynamo_store`, `store_factory`), 3 (`observability`), 4 (`entrypoint`), 5 (`memory`) |
| §3 preflight | Task 0, all six checks |
| §3.1 Memory not in strands-agents | Task 5, Steps 1–2 |
| §3.2 `PersistenceMode` cannot express selectivity | Task 5, Step 2 asserts it; `grace/memory.py` docstring records the consequence |
| §3.3 hard rule 2 constrains attachment | Task 5, Step 4's recursive test |
| §4 schema | Task 1 (keys), Task 2 (store) |
| §4.1 error posture | Task 2, `dynamo_store` docstring + `test_an_unknown_case_raises_key_error` |
| §4.2 fixtures authoritative | Task 2's "why the constructor still takes `cases`" |
| §5 entrypoint | Task 4 |
| §5.1 no resume | Task 4, `test_an_interrupt_is_never_resumed` |
| §5.2 escalation question | Task 4, Step 1's helper promotion + `test_the_gates_typed_reason_beats_a_generic_run_status` |
| §5.3 runtime constraints | Task 7 (redaction guard, arm64), Task 8 (33-char session) |
| §5.4 IAM not JWT | Task 6; README in Task 10 |
| §6.1 state machine | Task 8 |
| §6.2 the one alarm | Task 9 |
| §6.3 IAM roles | Task 6 |
| §7 verification, all three gates | Task 10 Steps 1–4 |
| §8 out of scope | Task 10 Step 6 |
| §9 risks | Task 0 (Docker, CLI drift), Task 8 (Bedrock latency via Retry/Catch) |
| §10 open items | Item 2 resolved in Task 5; items 1 and 3 are non-blocking |

**Placeholder scan.** No "TBD", no "add error handling", no "similar to Task N". Every code step
carries the actual code. Task 7 Step 7 deliberately says "use the flags you recorded in Task 0"
rather than inventing CLI flags — that is a verified-at-runtime value, not a placeholder, and
inventing one would be worse.

**Type consistency.** Names used across tasks, checked against their definitions:
`build_store` (T2→T4), `to_dynamo` (T2), `write_escalation` (T2→T4), `gate_reason` /
`renewal_filed` / `outreach_sent` / `deliberation_note` (promoted in T4 Step 1, used in T4 Step 5),
`setup_telemetry` / `redaction_is_configured` / `REDACTION_TOKEN` (T3→T4, T7),
`RETRIEVAL_NAMESPACES` / `build_session_manager` / `actor_id` (T5),
`runtime_policy` / `DENY_SID` (T6), `definition` / `CASE_IDS` (T8), `alarm_spec` (T9),
`naming.{TABLE,PENDING,ESCALATION_GSI,case_pk,ledger_sk,escalation_sk}` (T1, used throughout).
`infra.naming` is imported by `grace/cases/dynamo_store.py` and `grace/memory.py`, which is why
Task 7's Dockerfile copies `infra/` — noted there explicitly.

**Test-count arithmetic.** 360 baseline → 365 (T1) → 391 (T2) → 396 (T3) → 405 (T4) → 410 (T5) →
416 (T6) → 419 (T7) → 427 (T8) → 431 (T9). Every task says "report the real number," because every
estimate in Plan 1 proved stale once written.
