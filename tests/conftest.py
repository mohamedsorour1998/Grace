from datetime import date

import pytest

from grace.cases.store import InMemoryCaseStore, load_fixture_cases

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
