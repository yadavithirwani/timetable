"""Mutation tests for the verifier.

The verifier is what lets us trust a timetable, so we test it the way you'd test a
smoke alarm: start from a known-good timetable (the planted one), break it in one
specific way, and check the alarm goes off with the right message.
"""
import copy
from collections import defaultdict

import pytest

from backend.optimizer.verify import verify


def _ok(school, rows, rules):
    return verify(school, rows, rules)


def test_planted_solution_is_valid(small_school, rules):
    res = _ok(small_school, small_school["planted_solution"], rules)
    assert res["valid"], res["violations"]
    assert res["quality"]["free_class_periods"] == 0


def _first(rows, pred):
    return next(i for i, a in enumerate(rows) if pred(a))


def test_detects_missing_period(small_school, rules):
    rows = copy.deepcopy(small_school["planted_solution"])
    victim = rows[_first(rows, lambda a: True)]
    rows = [a for a in rows if not (a["lesson_id"] == victim["lesson_id"] and a["day"] == victim["day"]
                                    and a["period"] == victim["period"])]
    res = _ok(small_school, rows, rules)
    assert not res["valid"]
    assert any("needs" in v for v in res["violations"])


def test_detects_teacher_double_booking(small_school, rules):
    rows = copy.deepcopy(small_school["planted_solution"])
    # put teacher of row 0 into another class's lesson at the same slot
    a = rows[0]
    j = _first(rows, lambda b: b["day"] == a["day"] and b["period"] == a["period"]
               and b["class_id"] != a["class_id"] and b["teacher_id"] != a["teacher_id"])
    rows[j]["teacher_id"] = a["teacher_id"]
    res = _ok(small_school, rows, rules)
    assert any("double-booked" in v for v in res["violations"])


def test_detects_missing_co_teacher(small_school, rules):
    joint = next(l for l in small_school["lessons"] if len(l["teacher_ids"]) > 1)
    rows = copy.deepcopy(small_school["planted_solution"])
    i = _first(rows, lambda a: a["lesson_id"] == joint["id"] and a["teacher_id"] == joint["teacher_ids"][1])
    del rows[i]
    res = _ok(small_school, rows, rules)
    assert any("classes/teachers present" in v for v in res["violations"])


def test_detects_joint_lesson_out_of_sync(small_school, rules):
    """Moving one section of a joint elective to another slot must be caught."""
    joint = next(l for l in small_school["lessons"] if len(l["class_ids"]) > 1)
    rows = copy.deepcopy(small_school["planted_solution"])
    c2 = joint["class_ids"][1]
    moved = [a for a in rows if a["lesson_id"] == joint["id"] and a["class_id"] == c2]
    target = moved[0]
    for a in moved:
        if a["day"] == target["day"] and a["period"] == target["period"]:
            a["period"] = 1 if target["period"] != 1 else 2
    res = _ok(small_school, rows, rules)
    assert not res["valid"]


def test_detects_class_teacher_not_in_period_one(small_school, rules):
    ct = next(l for l in small_school["lessons"] if l["fixed_period"] == 1)
    rows = copy.deepcopy(small_school["planted_solution"])
    for a in rows:
        if a["lesson_id"] == ct["id"] and a["day"] == small_school["days"][0]:
            a["period"] = 2
    res = _ok(small_school, rows, rules)
    assert any("must be in period 1" in v for v in res["violations"])
    assert any("lessons on" in v for v in res["violations"])  # also clashes with P2's lesson


def test_detects_three_of_a_subject_in_a_day(small_school, rules):
    rows = copy.deepcopy(small_school["planted_solution"])
    # find a class-day and force one lesson to occupy 3 slots by relabelling two others
    by_cd = defaultdict(list)
    for a in rows:
        by_cd[a["class_id"], a["day"]].append(a)
    cd, lst = next((k, v) for k, v in by_cd.items() if len({a["lesson_id"] for a in v}) > 3)
    simple = {l["id"] for l in small_school["lessons"]
              if len(l["class_ids"]) == 1 and len(l["teacher_ids"]) == 1 and not l["fixed_period"]}
    single = [a for a in lst if a["lesson_id"] in simple]
    lid = single[0]["lesson_id"]
    for a in single[1:3]:
        a["lesson_id"] = lid
    res = _ok(small_school, rows, rules)
    assert any("max 2" in v for v in res["violations"])


def test_detects_broken_lab_double_period(small_school, rules):
    pr = next(l for l in small_school["lessons"] if l.get("lab"))
    rows = copy.deepcopy(small_school["planted_solution"])
    mine = sorted([a for a in rows if a["lesson_id"] == pr["id"]], key=lambda a: a["period"])
    # move the second half of the double two periods away from the first
    k = mine[0]["period"]
    mine[-1]["period"] = k + 2 if k + 2 <= small_school["periods_per_day"] else k - 2
    res = _ok(small_school, rows, rules)
    assert any("double period" in v for v in res["violations"])


def test_detects_unavailable_teacher(small_school, rules):
    a = small_school["planted_solution"][0]
    for t in small_school["teachers"]:
        if t["id"] == a["teacher_id"]:
            t["unavailable_slots"] = [{"day": a["day"], "period": a["period"]}]
    res = _ok(small_school, small_school["planted_solution"], rules)
    assert any("unavailable" in v for v in res["violations"])


@pytest.mark.parametrize("field,value", [("day", "Sunday"), ("period", 99)])
def test_detects_off_grid(small_school, rules, field, value):
    rows = copy.deepcopy(small_school["planted_solution"])
    rows[0][field] = value
    assert not _ok(small_school, rows, rules)["valid"]
