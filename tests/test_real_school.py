"""The test that matters: the actual school's Excel, end to end."""
import os
from collections import defaultdict

import pytest

EXCEL = os.path.join(os.path.dirname(__file__), "..", "config", "SHIKHA_CLASSWISE.xlsx")
pytestmark = [pytest.mark.slow,
              pytest.mark.skipif(not os.path.exists(EXCEL), reason="real school Excel not present")]


@pytest.fixture(scope="module")
def solved():
    from backend.ingest.excel_loader import load_school_from_excel
    from backend.optimizer.engine import generate_timetable, load_rules
    school = load_school_from_excel(EXCEL)
    return school, generate_timetable(school, rules=load_rules(), time_limit=30)


def test_real_school_solves_and_verifies(solved):
    _, res = solved
    assert res["success"], res["violations"][:10]
    assert res["metadata"]["verified"]
    q = res["metadata"]["quality"]
    assert q["free_class_periods"] == 0


def test_no_fictional_teachers(solved):
    school, res = solved
    names = {t["id"]: t["name"] for t in school["teachers"]}
    assert not any("SELF STUDY" in names.get(a["teacher_id"], "") for a in res["assignments"])


def test_cross_section_electives_are_synchronised(solved):
    school, res = solved
    cname = {c["id"]: c["name"] for c in school["classes"]}
    slots = defaultdict(set)
    for a in res["assignments"]:
        slots[a["lesson_id"], cname[a["class_id"]]].add((a["day"], a["period"]))
    for l in school["lessons"]:
        if len(l["class_ids"]) > 1:
            seen = {frozenset(slots[l["id"], cname[c]]) for c in l["class_ids"]}
            assert len(seen) == 1, l


def test_every_class_starts_with_class_teacher(solved):
    school, res = solved
    ct = {c["id"]: c["class_teacher_id"] for c in school["classes"]}
    first = defaultdict(set)
    for a in res["assignments"]:
        if a["period"] == 1:
            first[a["class_id"], a["day"]].add(a["teacher_id"])
    for (c, d), ts in first.items():
        assert ct[c] in ts, (c, d, ts)
