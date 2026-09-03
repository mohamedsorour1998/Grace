"""The alarm's shape, the filter pattern that feeds it, and what teardown deletes.

Three separate concerns, all asserted offline, because all three fail *silently*:

- The comparison operator and the treatment of missing data are easy to get
  backwards, and an alarm that never fires looks identical to a system that never
  breaks.
- A metric filter that matches nothing publishes no datapoints, which
  `TreatMissingData: breaching` then reports as a permanent breach — a false
  alarm indistinguishable from a real one. The plan's draft pattern matched
  **zero** of the real sweep's events.
- A teardown that matches something looser than `grace-` deletes another
  project's work, and this account holds three unrelated projects.
"""

from __future__ import annotations

import json

import pytest
from botocore.exceptions import ClientError

from infra import naming, provision_alarm, provision_all, provision_iam, teardown

ACCOUNT = "123456789012"


# ---------------------------------------------------------------------------
# Fakes. Deliberately recording rather than asserting, so a test can inspect
# every call that *would* have been made against AWS without making one.
# ---------------------------------------------------------------------------


class FakeClient:
    """Records every call; answers from a canned response table.

    No `.exceptions` attribute on purpose: the modules under test check
    `ClientError` response codes instead of `client.exceptions.Foo`, which is
    what makes them testable offline at all. A fake carrying `.exceptions` would
    let that discipline rot without any test noticing.
    """

    def __init__(self, service: str, responses: dict | None = None,
                 raises: dict | None = None):
        self.service = service
        self.calls: list[tuple[str, dict]] = []
        self._responses = responses or {}
        self._raises = raises or {}

    def __getattr__(self, operation: str):
        def call(**kwargs):
            self.calls.append((operation, kwargs))
            if operation in self._raises:
                raise self._raises[operation]
            response = self._responses.get(operation, {})
            return response(**kwargs) if callable(response) else response

        return call

    def ops(self) -> list[str]:
        return [op for op, _ in self.calls]

    def args(self, operation: str) -> list[dict]:
        return [kwargs for op, kwargs in self.calls if op == operation]


def _client_error(code: str, operation: str = "Op") -> ClientError:
    return ClientError({"Error": {"Code": code, "Message": code}}, operation)


def _healthy_logs_client() -> FakeClient:
    """A logs client whose read-back agrees with what was written."""
    return FakeClient(
        "logs",
        responses={
            "describe_metric_filters": lambda **kw: {
                "metricFilters": [{
                    "filterName": provision_alarm.FILTER_NAME,
                    "filterPattern": provision_alarm.FILTER_PATTERN,
                    "metricTransformations": [{
                        "metricName": provision_alarm.METRIC_NAME,
                        "metricNamespace": provision_alarm.NAMESPACE,
                    }],
                }]
            },
        },
    )


def _cw_client(**overrides) -> FakeClient:
    """A cloudwatch client whose read-back echoes the real spec.

    Built *from* `alarm_spec()` rather than from a hand-written literal, so a
    field added to the spec is echoed automatically and a wrong-read-back test
    below differs from a healthy one in exactly the one field it overrides —
    otherwise a `RuntimeError` raised for a missing field would look identical
    to one raised for the defect the test is about.
    """
    alarm = dict(provision_alarm.alarm_spec())
    alarm.update(overrides)
    return FakeClient(
        "cloudwatch",
        responses={"describe_alarms": lambda **kw: {"MetricAlarms": [alarm]}},
    )


def _healthy_cw_client() -> FakeClient:
    return _cw_client()


# ---------------------------------------------------------------------------
# The alarm spec — the four assertions the plan asks for
# ---------------------------------------------------------------------------


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


def test_the_alarm_sums_rather_than_averages():
    """`Average` of a per-event metric is not a count. Three escalations out of
    twelve cases average well below 1, so an averaging alarm would breach on a
    correct sweep and be turned off as noise."""
    assert provision_alarm.alarm_spec()["Statistic"] == "Sum"


# ---------------------------------------------------------------------------
# The filter pattern. Measured against the real sweep's log events, not guessed.
# ---------------------------------------------------------------------------


def test_the_filter_pattern_anchors_on_the_event_type():
    """**Measured, not reasoned.** Against the real 12-case sweep's 160 log
    events, via `logs:test_metric_filter`:

        { $.status = "escalated" }                    0   <- the plan's draft
        { $.details.output = "*escalated*" }         14
        the pattern below                             3   correct

    Without the `$.type` anchor the same three outcomes are counted once per
    event type each case passes through, so `Threshold: 3` would be compared
    against 14 and the alarm would be permanently quiet — useless in exactly
    the way that looks fine.
    """
    pattern = provision_alarm.FILTER_PATTERN
    assert "$.type" in pattern, pattern
    assert "TaskStateExited" in pattern, pattern


def test_the_filter_pattern_reaches_into_the_embedded_json_payload():
    """There is **no top-level `status` field**. A Step Functions log event's
    keys are `type`, `details`, `execution_arn`, `id`, `previous_event_id`,
    `redrive_count`, `event_timestamp` — the outcome payload sits at
    `$.details.output` as an embedded JSON *string*, so the match has to be a
    substring against the serialized form."""
    pattern = provision_alarm.FILTER_PATTERN
    assert "$.details.output" in pattern, pattern
    assert '$.status' not in pattern, "the plan's draft field; it does not exist"
    # The serialized shape, escaped for the filter grammar. Matching bare
    # `*escalated*` counts the word wherever it appears — including in a
    # `reason` string — and matched 14 events rather than 3.
    assert '\\"status\\":\\"escalated\\"' in pattern, pattern


def test_the_filter_pattern_matches_a_real_escalated_event_and_nothing_else():
    """The pattern checked against the real event shapes captured from the
    deployed sweep, as a local sanity check on the grammar this file asserts
    against — the authoritative count came from `logs:test_metric_filter`.

    Three shapes, because the two rejections are the actual defects: a
    `PassStateExited` carrying the identical payload is why the `$.type` anchor
    exists, and an `acted` outcome is what the alarm must not count.
    """
    escalated = {
        "type": "TaskStateExited",
        "details": {"name": "InvokeCase",
                    "output": json.dumps({"status": "escalated", "case_id": "c-010"},
                                         separators=(",", ":"))},
    }
    same_payload_wrong_type = dict(escalated, type="PassStateExited")
    acted = {
        "type": "TaskStateExited",
        "details": {"name": "InvokeCase",
                    "output": json.dumps({"status": "acted", "case_id": "c-001"},
                                         separators=(",", ":"))},
    }

    assert provision_alarm.matches(escalated)
    assert not provision_alarm.matches(same_payload_wrong_type)
    assert not provision_alarm.matches(acted)


def test_the_filter_and_the_alarm_watch_the_same_metric():
    """The one agreement that fails silently in the worst direction. An alarm on
    a namespace nothing publishes to sits on missing data forever, and
    `TreatMissingData: breaching` renders that as a permanent breach — a false
    alarm indistinguishable from a real one."""
    spec = provision_alarm.alarm_spec()
    assert spec["Namespace"] == provision_alarm.NAMESPACE
    assert spec["MetricName"] == provision_alarm.METRIC_NAME


def test_the_filter_is_built_over_the_state_machines_own_log_group():
    """`provision_stepfunctions` creates this log group and points Step
    Functions logging at it; this task filters over it. Both read the name from
    `naming`, so they cannot disagree."""
    assert provision_alarm.LOG_GROUP == naming.SFN_LOG_GROUP


def test_a_non_matching_event_still_publishes_a_zero():
    """`defaultValue: 0` is what distinguishes 'the sweep ran and escalated
    nothing' from 'the sweep never ran'. Both breach — but only one of them is
    a gate that got looser, and the operator needs to be able to tell."""
    transformations = provision_alarm.metric_transformations()
    assert transformations[0]["defaultValue"] == 0.0
    assert transformations[0]["metricValue"] == "1"


# ---------------------------------------------------------------------------
# `provision` — idempotence, and the read-back that decides success
# ---------------------------------------------------------------------------


def test_provision_creates_the_filter_and_the_alarm():
    logs, cw = _healthy_logs_client(), _healthy_cw_client()
    arn = provision_alarm.provision(logs_client=logs, cw_client=cw, account_id=ACCOUNT)

    assert "put_metric_filter" in logs.ops()
    assert "put_metric_alarm" in cw.ops()
    assert arn == (
        f"arn:aws:cloudwatch:{naming.REGION}:{ACCOUNT}:alarm:{naming.ALARM}"
    )


def test_provision_is_idempotent_when_the_log_group_already_exists():
    """The log group is created by `provision_stepfunctions` first, so on every
    real run after the first this is the path taken. A provisioning script that
    cannot be re-run is useless when a deploy fails halfway."""
    logs = _healthy_logs_client()
    logs._raises["create_log_group"] = _client_error(
        "ResourceAlreadyExistsException", "CreateLogGroup"
    )
    arn = provision_alarm.provision(
        logs_client=logs, cw_client=_healthy_cw_client(), account_id=ACCOUNT
    )
    assert arn.endswith(naming.ALARM)
    assert "put_metric_filter" in logs.ops()


def test_provision_reraises_a_log_group_error_that_is_not_already_exists():
    """`AccessDeniedException` is not 'already there'. Swallowing every error
    from `create_log_group` would report success with no filter and no alarm —
    the control-looks-present-and-is-absent failure this codebase keeps
    finding."""
    logs = _healthy_logs_client()
    logs._raises["create_log_group"] = _client_error("AccessDeniedException",
                                                     "CreateLogGroup")
    with pytest.raises(ClientError):
        provision_alarm.provision(logs_client=logs, cw_client=_healthy_cw_client(),
                                  account_id=ACCOUNT)


def test_provision_refuses_when_the_filter_reads_back_wrong():
    """'The API call returned' and 'the filter is what we asked for' are
    different claims, and only the second one matters — the same distinction
    `provision_dynamodb` makes about point-in-time recovery. A filter left over
    from the plan's draft pattern would match zero events while
    `put_metric_filter` reported success."""
    logs = FakeClient("logs", responses={
        "describe_metric_filters": {"metricFilters": [{
            "filterName": provision_alarm.FILTER_NAME,
            # The plan's draft. Matched 0 of 3 against real events.
            "filterPattern": '{ $.status = "escalated" }',
            "metricTransformations": [{
                "metricName": provision_alarm.METRIC_NAME,
                "metricNamespace": provision_alarm.NAMESPACE,
            }],
        }]},
    })
    with pytest.raises(RuntimeError, match="filter"):
        provision_alarm.provision(logs_client=logs, cw_client=_healthy_cw_client(),
                                  account_id=ACCOUNT)


def test_provision_refuses_when_the_filter_is_absent_after_writing_it():
    logs = FakeClient("logs", responses={"describe_metric_filters": {"metricFilters": []}})
    with pytest.raises(RuntimeError, match="filter"):
        provision_alarm.provision(logs_client=logs, cw_client=_healthy_cw_client(),
                                  account_id=ACCOUNT)


def test_provision_refuses_when_the_alarm_reads_back_backwards():
    """The specific silent failure this whole task exists to prevent. An alarm
    left at `GreaterThanThreshold` by an earlier version never fires, and
    nothing anywhere reports that — it looks exactly like a system that never
    breaks."""
    cw = _cw_client(ComparisonOperator="GreaterThanThreshold")
    with pytest.raises(RuntimeError, match="alarm"):
        provision_alarm.provision(logs_client=_healthy_logs_client(), cw_client=cw,
                                  account_id=ACCOUNT)


def test_provision_refuses_when_missing_data_reads_back_as_not_breaching():
    cw = _cw_client(TreatMissingData="notBreaching")
    with pytest.raises(RuntimeError, match="alarm"):
        provision_alarm.provision(logs_client=_healthy_logs_client(), cw_client=cw,
                                  account_id=ACCOUNT)


def test_provision_refuses_when_the_alarm_watches_a_metric_nothing_publishes():
    """An alarm on the wrong namespace sits on missing data forever, and
    `breaching` renders that as a permanent breach the operator learns to
    ignore."""
    cw = _cw_client(Namespace="SomeoneElsesNamespace")
    with pytest.raises(RuntimeError, match="alarm"):
        provision_alarm.provision(logs_client=_healthy_logs_client(), cw_client=cw,
                                  account_id=ACCOUNT)


def test_provision_refuses_when_the_alarm_is_absent_after_writing_it():
    cw = FakeClient("cloudwatch", responses={"describe_alarms": {"MetricAlarms": []}})
    with pytest.raises(RuntimeError, match="alarm"):
        provision_alarm.provision(logs_client=_healthy_logs_client(), cw_client=cw,
                                  account_id=ACCOUNT)


def test_the_readback_checks_every_field_the_spec_declares():
    """A read-back that only checks the fields someone remembered is how a
    silently-wrong alarm survives. Derived from `alarm_spec()` so a field added
    there is verified automatically — the same discovery-from-source discipline
    as the ledger-writer and model-id guards."""
    checked = provision_alarm.VERIFIED_ALARM_FIELDS
    spec = provision_alarm.alarm_spec()
    # Everything except the two free-text/shape fields that AWS may normalize.
    assert checked == frozenset(spec) - {"AlarmName", "AlarmDescription",
                                         "Statistic", "Period", "ActionsEnabled"}
    assert "ComparisonOperator" in checked
    assert "TreatMissingData" in checked
    assert "Threshold" in checked


# ---------------------------------------------------------------------------
# `provision_all` — the runtime lookup, and what it must not create
# ---------------------------------------------------------------------------


class FakeRuntimeControl:
    """A `bedrock-agentcore-control` fake that **paginates**.

    Sixteen runtimes in this account, ten to a page, and Grace is on page two —
    measured, not supposed. A fake that answers in one page would let a
    single-page lookup pass forever.
    """

    def __init__(self, runtimes: list[dict], page_size: int = 10):
        self.runtimes = runtimes
        self.page_size = page_size
        self.pages_served = 0

    def list_agent_runtimes(self, **kwargs):
        token = kwargs.get("nextToken")
        start = int(token) if token else 0
        page = self.runtimes[start:start + self.page_size]
        self.pages_served += 1
        response = {"agentRuntimes": page}
        if start + self.page_size < len(self.runtimes):
            response["nextToken"] = str(start + self.page_size)
        return response


def _other_projects(count: int) -> list[dict]:
    """The real neighbours: this account holds `theagentorg_*`,
    `rosettaclaw_*`, and `rosettacloud_*` runtimes."""
    return [
        {"agentRuntimeName": f"theagentorg_agent_{n}", "status": "READY",
         "agentRuntimeArn": f"arn:aws:bedrock-agentcore:us-east-1:1:runtime/other-{n}"}
        for n in range(count)
    ]


GRACE_RUNTIME = {
    "agentRuntimeName": "grace_grace",
    "status": "READY",
    "agentRuntimeArn": (
        "arn:aws:bedrock-agentcore:us-east-1:339712964409:runtime/grace_grace-oTyyvo8stE"
    ),
}


def test_the_runtime_lookup_reads_past_the_first_page():
    """**The plan's draft called `list_agent_runtimes()` once.** Measured against
    the real account: 16 runtimes, 10 to a page, and `grace_grace` is on page
    **two** — so the draft would have raised "no Grace runtime found" against a
    runtime that is deployed and READY, and the operator's fix would have been to
    redeploy something that was already there.
    """
    control = FakeRuntimeControl(_other_projects(10) + [GRACE_RUNTIME])
    arn = provision_all.runtime_arn(control)
    assert arn == GRACE_RUNTIME["agentRuntimeArn"]
    assert control.pages_served == 2, "a single-page read cannot have found it"


def test_the_runtime_lookup_refuses_a_runtime_that_is_not_ready():
    """A CREATING runtime has an ARN, so returning it would wire a Lambda and a
    state machine to something that cannot serve a case."""
    control = FakeRuntimeControl([dict(GRACE_RUNTIME, status="CREATING")])
    with pytest.raises(RuntimeError, match="READY"):
        provision_all.runtime_arn(control)


def test_the_runtime_lookup_raises_when_no_grace_runtime_exists():
    """`agentcore deploy` owns runtime creation, so the honest failure is to say
    so rather than to create one here."""
    control = FakeRuntimeControl(_other_projects(3))
    with pytest.raises(RuntimeError, match="agentcore deploy"):
        provision_all.runtime_arn(control)


def test_the_runtime_lookup_never_matches_another_projects_runtime():
    """`startswith` on a bare prefix is how a lookup picks up a neighbour. This
    account really does hold `rosettacloud_education_memory` next to
    `..._v2`, which is the same collision one resource type over."""
    control = FakeRuntimeControl([
        {"agentRuntimeName": "graceful_other_thing", "status": "READY",
         "agentRuntimeArn": "arn:aws:bedrock-agentcore:us-east-1:1:runtime/nope"},
    ])
    with pytest.raises(RuntimeError, match="agentcore deploy"):
        provision_all.runtime_arn(control)


def test_provision_all_does_not_create_the_agentcore_runtime():
    """`agentcore deploy` builds and pushes a container image; this script only
    wires things around the result. A `create_agent_runtime` call here would
    make two owners for one resource."""
    source = (provision_all.__file__)
    text = open(source).read()
    assert "create_agent_runtime" not in text
    assert "update_agent_runtime" not in text


# ---------------------------------------------------------------------------
# `teardown` — what it deletes, and everything it must not
# ---------------------------------------------------------------------------

# The unrelated projects that really share this account. A teardown that matched
# anything looser than `grace-` deletes someone else's work, and unlike every
# other defect in this codebase that one is not recoverable by re-running a
# script.
NEIGHBOURS = (
    "theagentorg-shared-agentcore-runtime-role",
    "theagentorg_sre",
    "rosettacloud-education-agent",
    "rosettaclaw_paper",
    "bughunt-main",
    # The near-misses that a prefix check must still refuse: a `grace` that is
    # not `grace-`, and a name that merely contains it.
    "graceful-degradation-lambda",
    "not-grace-cases",
    "grace",
)


def _teardown_clients() -> dict[str, FakeClient]:
    return {
        "events": FakeClient("events", responses={
            # The Id `provision_eventbridge` really sets.
            "list_targets_by_rule": {"Targets": [{"Id": "grace-sweep"}]},
        }),
        "stepfunctions": FakeClient("stepfunctions"),
        "lambda": FakeClient("lambda"),
        "cloudwatch": FakeClient("cloudwatch"),
        "logs": FakeClient("logs"),
        "iam": FakeClient("iam", responses={
            "list_role_policies": {"PolicyNames": []},
        }),
        "dynamodb": FakeClient("dynamodb"),
    }


def _all_named_resources(clients: dict[str, FakeClient]) -> list[str]:
    """Every resource name teardown asked a service to delete.

    Collected from the recorded call arguments rather than from a list of the
    parameters someone remembered, so a new deletion added later is covered
    automatically — the discovery-from-source discipline the ledger-writer and
    model-id guards use.
    """
    keys = ("Rule", "Name", "FunctionName", "TableName", "RoleName",
            "PolicyName", "logGroupName", "filterName")
    names: list[str] = []
    for client in clients.values():
        for operation, kwargs in client.calls:
            if not operation.startswith(("delete", "remove")):
                continue
            for key in keys:
                if isinstance(kwargs.get(key), str):
                    names.append(kwargs[key])
            names.extend(kwargs.get("AlarmNames", []))
            names.extend(kwargs.get("Ids", []))
            # An ARN carries the name in its last segment.
            for key in ("stateMachineArn", "Arn"):
                if isinstance(kwargs.get(key), str):
                    names.append(kwargs[key].rsplit(":", 1)[-1])
    return names


def test_teardown_only_ever_names_grace_resources():
    """The single most important assertion in this file. This account holds
    `theagentorg-*`, `rosettacloud-*`, `rosettaclaw_*`, and `bughunt-main`; a
    teardown that matched anything looser deletes another project's work, and
    that is the one failure here no re-run recovers from."""
    clients = _teardown_clients()
    teardown.main(clients=clients, account_id=ACCOUNT, include_table=True)

    named = _all_named_resources(clients)
    assert named, "a teardown that names nothing is not a teardown"
    for name in named:
        assert teardown.is_grace_resource(name), f"teardown named {name!r}"


def test_the_grace_matcher_refuses_every_neighbour_in_this_account():
    """Asserted against the real names, including the near-misses. `grace` alone
    and `graceful-degradation-lambda` both pass a naive `startswith("grace")`."""
    for name in NEIGHBOURS:
        assert not teardown.is_grace_resource(name), name


def test_the_grace_matcher_accepts_the_resources_this_plan_creates():
    for name in (naming.TABLE, naming.LAMBDA, naming.STATE_MACHINE,
                 naming.SCHEDULE_RULE, naming.ALARM,
                 provision_alarm.FILTER_NAME, naming.SFN_LOG_GROUP,
                 *(provision_iam.role_name(p) for p in provision_iam.POLICY_BUILDERS),
                 *(provision_iam.policy_name(p) for p in provision_iam.POLICY_BUILDERS)):
        assert teardown.is_grace_resource(name), name


def test_teardown_keeps_the_table_unless_asked_explicitly():
    """The table is the audit trail for every autonomous benefits decision. A
    teardown that removes it by default makes an ordinary cleanup destroy the
    evidence that Grace escalated the three cases it should have."""
    clients = _teardown_clients()
    teardown.main(clients=clients, account_id=ACCOUNT)
    assert clients["dynamodb"].ops() == [], clients["dynamodb"].calls


def test_teardown_deletes_the_table_only_with_include_table():
    clients = _teardown_clients()
    teardown.main(clients=clients, account_id=ACCOUNT, include_table=True)
    assert clients["dynamodb"].args("delete_table") == [{"TableName": naming.TABLE}]


def test_teardown_does_not_delete_the_runtime_or_the_memory():
    """Both are owned by `agentcore deploy` and a deliberate CLI call. Deleting
    Memory here would discard every household's cross-cycle facts as a side
    effect of tearing down a state machine.

    Asserted against the *operations*, not against the word
    `bedrock-agentcore` — that string legitimately appears in a log-group path,
    and a substring check on it failed against correct code, which is a test
    asserting the wrong thing rather than a defect.
    """
    text = open(teardown.__file__).read()
    for forbidden in ("delete_agent_runtime", "delete_memory",
                      "bedrock-agentcore-control"):
        assert forbidden not in text, forbidden


def test_teardown_deletes_the_targets_before_the_rule():
    """EventBridge refuses to delete a rule that still has targets, so the order
    is load-bearing rather than stylistic."""
    clients = _teardown_clients()
    teardown.main(clients=clients, account_id=ACCOUNT)
    ops = clients["events"].ops()
    assert ops.index("remove_targets") < ops.index("delete_rule"), ops


def test_teardown_discovers_the_target_ids_rather_than_predicting_them():
    """`provision_eventbridge` sets `Id: "grace-sweep"`, which equals
    `naming.STATE_MACHINE` only by coincidence. Restating that coincidence in
    teardown would leave the target behind — and therefore an undeletable rule —
    the moment either name changed independently. So the Ids are read from the
    rule.
    """
    clients = _teardown_clients()
    clients["events"] = FakeClient("events", responses={
        "list_targets_by_rule": {"Targets": [{"Id": "grace-sweep-renamed"}]},
    })
    teardown.main(clients=clients, account_id=ACCOUNT)
    assert clients["events"].args("remove_targets") == [
        {"Rule": naming.SCHEDULE_RULE, "Ids": ["grace-sweep-renamed"]}
    ]


def test_teardown_skips_remove_targets_when_the_rule_has_none():
    """`remove_targets` with an empty `Ids` list is a validation error, so an
    unconditional call would turn 'nothing to remove' into a failed teardown."""
    clients = _teardown_clients()
    clients["events"] = FakeClient("events", responses={
        "list_targets_by_rule": {"Targets": []},
    })
    teardown.main(clients=clients, account_id=ACCOUNT)
    assert "remove_targets" not in clients["events"].ops()
    assert "delete_rule" in clients["events"].ops()


def test_teardown_deletes_the_role_policy_before_the_role():
    """IAM refuses to delete a role that still has an inline policy attached."""
    clients = _teardown_clients()
    clients["iam"] = FakeClient("iam", responses={
        "list_role_policies": {"PolicyNames": ["grace-lambda-policy"]},
    })
    teardown.main(clients=clients, account_id=ACCOUNT)
    ops = clients["iam"].ops()
    assert "delete_role_policy" in ops and "delete_role" in ops, ops
    assert ops.index("delete_role_policy") < ops.index("delete_role"), ops


def test_teardown_deletes_every_role_this_plan_creates():
    """Derived from `POLICY_BUILDERS`, so a fifth role added there is torn down
    too rather than orphaned — an orphaned role keeps its permissions."""
    clients = _teardown_clients()
    teardown.main(clients=clients, account_id=ACCOUNT)
    deleted = {kw["RoleName"] for kw in clients["iam"].args("delete_role")}
    assert deleted == {provision_iam.role_name(p) for p in provision_iam.POLICY_BUILDERS}


def test_teardown_removes_the_metric_filter_it_created():
    """Left behind, the filter keeps publishing to a namespace whose alarm is
    gone — and the next `provision_all` would find a filter it did not write."""
    clients = _teardown_clients()
    teardown.main(clients=clients, account_id=ACCOUNT)
    assert clients["logs"].args("delete_metric_filter") == [{
        "logGroupName": naming.SFN_LOG_GROUP,
        "filterName": provision_alarm.FILTER_NAME,
    }]


def test_teardown_survives_every_resource_already_being_gone():
    """Idempotent in the other direction: running it twice, or against a
    half-provisioned account, must not raise. 'Already gone' is success."""
    clients = _teardown_clients()
    for name, client in clients.items():
        for operation in ("remove_targets", "delete_rule", "delete_state_machine",
                          "delete_function", "delete_alarms", "delete_metric_filter",
                          "delete_role_policy", "delete_role", "delete_table",
                          "list_role_policies"):
            client._raises[operation] = _client_error("ResourceNotFoundException",
                                                      operation)
    teardown.main(clients=clients, account_id=ACCOUNT, include_table=True)


def test_teardown_reraises_an_access_denied():
    """'Already gone' is success; 'you may not do that' is not. Swallowing every
    exception would report a clean teardown with the resources still present and
    still costing money."""
    clients = _teardown_clients()
    clients["lambda"]._raises["delete_function"] = _client_error(
        "AccessDeniedException", "DeleteFunction"
    )
    with pytest.raises(ClientError):
        teardown.main(clients=clients, account_id=ACCOUNT)


def test_the_guard_refuses_a_non_grace_name_outright():
    """`is_grace_resource` returning False must *raise*, not skip. A teardown
    that silently declined to delete something looks identical to one that
    deleted it, and if this ever fires the bug is in the caller."""
    with pytest.raises(RuntimeError, match="refusing to delete"):
        teardown._guard("theagentorg_sre")


def test_the_guard_is_actually_wired_into_the_deletion_path():
    """**The assertion that the guard exists is not the assertion that it
    fires.** Verified by sabotage: making `_guard` a no-op left all the other
    teardown tests passing, because every name teardown *currently* builds is
    legitimately Grace's — so nothing exercised the guard at all. That is the
    Task 8 vacuity lesson exactly.

    This drives the real failure instead: a naming constant pointed at another
    project's resource. Whether by a bad edit to `naming.py` or by a new
    deletion added without a `_guard` call, the outcome that matters is that
    teardown refuses *before* issuing the delete — so the assertion is both that
    it raised and that nothing was deleted.
    """
    clients = _teardown_clients()
    original = naming.LAMBDA
    naming.LAMBDA = "theagentorg-shared-agentcore-runtime-role"
    try:
        with pytest.raises(RuntimeError, match="refusing to delete"):
            teardown.main(clients=clients, account_id=ACCOUNT)
    finally:
        naming.LAMBDA = original

    assert clients["lambda"].args("delete_function") == [], \
        "teardown issued a delete for another project's resource"


def test_every_delete_teardown_issues_passed_through_the_guard():
    """A new deletion added later without a `_guard` call is the way this
    protection rots. Rather than trusting a reviewer to notice, this drives the
    whole function with a guard that records what it was asked to approve, and
    asserts every deleted name went through it.
    """
    approved: list[str] = []
    real_guard = teardown._guard

    def recording_guard(name: str) -> str:
        approved.append(name)
        return real_guard(name)

    clients = _teardown_clients()
    teardown._guard = recording_guard
    try:
        teardown.main(clients=clients, account_id=ACCOUNT, include_table=True)
    finally:
        teardown._guard = real_guard

    assert approved, "the guard was never called at all"
    for name in _all_named_resources(clients):
        assert name in approved, f"{name!r} was deleted without passing the guard"
