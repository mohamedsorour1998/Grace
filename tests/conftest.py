import os
from datetime import date

import pytest

from grace.cases.store import InMemoryCaseStore, load_fixture_cases

# `runtime_app` refuses to import without the span-redaction token (hard rule 8),
# so the suite must have it set before any test module is imported.
#
# **Set here at conftest import time, NOT in a fixture.** The plan's draft used
# an autouse session-scoped fixture, which cannot work: pytest imports test
# modules during *collection*, and no fixture — of any scope — has run by then.
# Measured with a throwaway conftest/test pair: `import runtime_app` raised
# during collection and the run aborted with a collection error, not a failure.
# A conftest is imported before the test modules in its directory, so a
# module-level assignment is early enough.
#
# `setdefault` so a shell that has already exported a value keeps it — including
# a deliberately *bad* value, which is what makes it possible to check the guard
# refuses. `tests/test_runtime_app.py` re-executes the module's source with the
# token stripped rather than relying on the ambient environment.
os.environ.setdefault(
    "OTEL_SEMCONV_STABILITY_OPT_IN",
    "gen_ai_latest_experimental,gen_ai_unredacted_attributes=",
)

# All fixture windows are anchored around this date so tests are
# deterministic regardless of when they run.
TODAY = date(2026, 10, 1)


@pytest.fixture
def today() -> date:
    return TODAY


@pytest.fixture
def fixture_cases():
    return load_fixture_cases()


@pytest.fixture
def store(fixture_cases) -> InMemoryCaseStore:
    return InMemoryCaseStore(fixture_cases)
