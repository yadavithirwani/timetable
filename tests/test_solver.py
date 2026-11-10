"""End-to-end solver tests on planted (guaranteed-solvable) schools."""
import pytest

from backend.optimizer.engine import generate_timetable
from backend.testing.synthetic import generate


def _assert_good(res):
    assert res["success"], (res["status"], res["violations"][:5])
    assert res["metadata"]["verified"]
    assert res["metadata"]["quality"]["free_class_periods"] == 0


@pytest.mark.parametrize("seed", range(5))
def test_small_planted_schools(seed, rules):
    school = generate(grades=3, sections=2, seed=seed, teacher_cap=36)
    _assert_good(generate_timetable(school, rules=rules, time_limit=5))


@pytest.mark.parametrize("seed", range(3))
def test_medium_planted_schools(seed, rules):
    school = generate(grades=6, sections=4, seed=seed, teacher_cap=42)
    _assert_good(generate_timetable(school, rules=rules, time_limit=10))


@pytest.mark.slow
def test_real_school_sized_planted(rules):
    """48 classes, ~90 teachers, busiest teachers at 44 periods - same scale as the real school."""
    school = generate(grades=12, sections=4, seed=0, teacher_cap=44)
    _assert_good(generate_timetable(school, rules=rules, time_limit=30))


def test_lock_is_honoured(small_school, rules):
    lesson = next(l for l in small_school["lessons"] if not l["fixed_period"] and len(l["class_ids"]) == 1)
    lock = {"class_id": lesson["class_ids"][0], "subject_id": lesson["subject_id"],
            "teacher_id": lesson["teacher_ids"][0], "day": "Wednesday", "period": 3}
    res = generate_timetable(small_school, locked_assignments=[lock], rules=rules, time_limit=5)
    _assert_good(res)
    assert any(a["class_id"] == lock["class_id"] and a["subject_id"] == lock["subject_id"]
               and a["day"] == "Wednesday" and a["period"] == 3 for a in res["assignments"])


def test_impossible_locks_are_explained(small_school, rules):
    """Two different subjects locked into the same class slot can't both happen."""
    cid = small_school["classes"][0]["id"]
    ls = [l for l in small_school["lessons"] if l["class_ids"] == [cid] and not l["fixed_period"]][:2]
    locks = [{"class_id": cid, "subject_id": l["subject_id"], "day": "Monday", "period": 4} for l in ls]
    res = generate_timetable(small_school, locked_assignments=locks, rules=rules, time_limit=5)
    assert not res["success"]
    assert any("Requested" in v for v in res["violations"]), res["violations"]


def test_hidden_infeasibility_is_explained(small_school, rules):
    """Passes every counting check, but the class teacher can't be in period 1 on Monday."""
    c = small_school["classes"][0]
    for t in small_school["teachers"]:
        if t["id"] == c["class_teacher_id"]:
            t["unavailable_slots"] = [{"day": "Monday", "period": 1}]
    res = generate_timetable(small_school, rules=rules, time_limit=5)
    assert res["status"] == "INFEASIBLE"
    expl = " | ".join(res["explanation"])
    assert "unavailable on Monday period 1" in expl
    assert "period 1" in expl


def test_legacy_sample_school_still_works(rules):
    import json
    import os
    path = os.path.join(os.path.dirname(__file__), "..", "backend", "data", "sample_school.json")
    with open(path) as f:
        school = json.load(f)
    res = generate_timetable(school, rules=rules, time_limit=10)
    assert res["success"], res["violations"][:5]
    assert res["metadata"]["verified"]
