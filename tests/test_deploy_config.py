"""Grace deploys to any table in a supported region.

The table name and region used to be constants, so deploying Grace into another
account meant editing source. They now come from the environment, with the
same polarity as `GRACE_STORE`: absence means the default, a blank value
refuses. A region outside the two Grace's model ids work in refuses at import,
so a wrong deploy fails at container start rather than inside the first case.
"""

from __future__ import annotations

import importlib
import json

import pytest

import grace.models as models
from infra import naming, provision_iam


@pytest.fixture(autouse=True)
def _restore_modules(monkeypatch):
    """Reloads below rebind module globals; put the defaults back afterwards so
    every other test sees the environment `conftest.py` set up."""
    yield
    monkeypatch.undo()
    importlib.reload(naming)
    importlib.reload(models)


def test_the_table_name_comes_from_the_environment(monkeypatch):
    monkeypatch.setenv("GRACE_TABLE_NAME", "acme-renewals")
    assert importlib.reload(naming).TABLE == "acme-renewals"


def test_the_table_name_is_trimmed(monkeypatch):
    monkeypatch.setenv("GRACE_TABLE_NAME", " acme-renewals ")
    assert importlib.reload(naming).TABLE == "acme-renewals"


def test_an_absent_table_name_keeps_the_default(monkeypatch):
    monkeypatch.delenv("GRACE_TABLE_NAME", raising=False)
    assert importlib.reload(naming).TABLE == "grace-cases"


def test_a_blank_table_name_refuses(monkeypatch):
    monkeypatch.setenv("GRACE_TABLE_NAME", "  ")
    with pytest.raises(ValueError, match="GRACE_TABLE_NAME"):
        importlib.reload(naming)


def test_an_unsupported_region_refuses_to_start():
    with pytest.raises(ValueError, match="us-east-1.*us-west-2"):
        naming.require_supported_region("eu-central-1")
    with pytest.raises(ValueError, match="us-east-1.*us-west-2"):
        naming.require_supported_region("")
    assert naming.require_supported_region("us-west-2") == "us-west-2"
    assert naming.require_supported_region("us-east-1") == "us-east-1"


def test_the_region_check_defaults_to_the_configured_region(monkeypatch):
    monkeypatch.setenv("AWS_REGION", "us-west-2")
    assert importlib.reload(naming).require_supported_region() == "us-west-2"
    monkeypatch.setenv("AWS_REGION", "ap-south-1")
    with pytest.raises(ValueError):
        importlib.reload(naming).require_supported_region()


def test_the_model_region_follows_aws_region(monkeypatch):
    monkeypatch.setenv("AWS_REGION", "us-west-2")
    assert importlib.reload(models).REGION == "us-west-2"
    monkeypatch.delenv("AWS_REGION", raising=False)
    assert importlib.reload(models).REGION == "us-east-1"


def test_the_runtime_policy_takes_a_region_and_table():
    text = json.dumps(provision_iam.runtime_policy("123456789012", region="us-west-2", table="acme-renewals"))
    assert "arn:aws:dynamodb:us-west-2:123456789012:table/acme-renewals" in text
    assert "arn:aws:dynamodb:us-west-2:123456789012:table/acme-renewals/index/*" in text
    assert "arn:aws:bedrock:us-west-2:123456789012:inference-profile/us.amazon.nova-pro-v1:0" in text
    assert "arn:aws:bedrock-agentcore:us-west-2:123456789012:memory/*" in text
    assert "us-east-1" not in text                       # no region left behind
    assert "GetWorkloadAccessTokenForUserId" in text      # the explicit Deny stays


def test_the_runtime_policy_defaults_are_unchanged():
    assert provision_iam.runtime_policy("123456789012") == provision_iam.runtime_policy(
        "123456789012", region=naming.REGION, table=naming.TABLE
    )
