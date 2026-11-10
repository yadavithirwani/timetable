import copy

import pytest

from backend.optimizer.engine import load_rules
from backend.testing.synthetic import generate


@pytest.fixture(scope="session")
def rules():
    r = load_rules()
    r.setdefault("solver", {})["time_limit_seconds"] = 10
    return r


@pytest.fixture(scope="session")
def _small_school():
    return generate(grades=4, sections=2, seed=7, teacher_cap=36)


@pytest.fixture
def small_school(_small_school):
    """An 8-class school with a known-valid planted timetable (fresh copy per test)."""
    return copy.deepcopy(_small_school)
