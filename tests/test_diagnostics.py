"""Bad data must fail fast with a message a non-programmer can act on."""
from backend.optimizer.engine import generate_timetable


def test_overloaded_teacher(small_school, rules):
    t = small_school["lessons"][5]["teacher_ids"][0]
    small_school["lessons"].append({"id": "LX", "subject_id": small_school["lessons"][5]["subject_id"],
                                    "class_ids": [small_school["classes"][0]["id"]], "teacher_ids": [t],
                                    "periods_per_week": 45, "max_per_day": 8, "fixed_period": None, "lab": None})
    res = generate_timetable(small_school, rules=rules)
    assert res["status"] == "INVALID_INPUT"
    name = next(x["name"] for x in small_school["teachers"] if x["id"] == t)
    assert any(name in v and "can teach at most" in v for v in res["violations"])


def test_class_over_full(small_school, rules):
    l = next(l for l in small_school["lessons"] if not l["fixed_period"] and len(l["class_ids"]) == 1)
    l["periods_per_week"] += 1
    res = generate_timetable(small_school, rules=rules)
    assert res["status"] == "INVALID_INPUT"
    assert any("the week only has 48" in v for v in res["violations"])


def test_quota_exceeds_daily_cap(small_school, rules):
    l = next(l for l in small_school["lessons"] if not l["fixed_period"])
    l["periods_per_week"] = 13
    res = generate_timetable(small_school, rules=rules)
    assert any("at most 2 a day" in v for v in res["violations"])


def test_class_teacher_of_two_classes(small_school, rules):
    a, b = small_school["classes"][:2]
    ct_b = next(l for l in small_school["lessons"] if l["fixed_period"] == 1 and l["class_ids"] == [b["id"]])
    ct_b["teacher_ids"] = [a["class_teacher_id"]]
    res = generate_timetable(small_school, rules=rules)
    assert res["status"] == "INVALID_INPUT"
    assert any("must be in period 1 for 12 lessons" in v for v in res["violations"])


def test_unknown_teacher(small_school, rules):
    small_school["lessons"][3]["teacher_ids"] = ["t_NOBODY"]
    res = generate_timetable(small_school, rules=rules)
    assert any("unknown teacher" in v for v in res["violations"])
