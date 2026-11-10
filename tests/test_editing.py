"""Edit engine: small changes to an existing timetable, re-solved with minimum movement and verified."""
import pytest

from backend import editing as E
from backend.optimizer.verify import verify


@pytest.fixture
def base(small_school):
    tt = {"success": True, "assignments": small_school["planted_solution"]}
    return small_school, tt


def _subject_at(s, asg, cname, day, period):
    cid = next(c["id"] for c in s["classes"] if c["name"] == cname)
    l = E.lesson_at(s, asg, cid, day, period)
    return l and next(x["name"] for x in s["subjects"] if x["id"] == l["subject_id"])


def _plain_slots(s, asg, cname):
    """Two slots of a class holding different, single-teacher, non-lab, non-fixed lessons."""
    cid = next(c["id"] for c in s["classes"] if c["name"] == cname)
    lessons = {l["id"]: l for l in s["lessons"]}
    ok = [(a["day"], a["period"], a["lesson_id"]) for a in asg if a["class_id"] == cid
          and not lessons[a["lesson_id"]].get("lab") and not lessons[a["lesson_id"]].get("fixed_period")
          and len(lessons[a["lesson_id"]]["class_ids"]) == 1]
    a = ok[0]
    b = next(x for x in ok if x[2] != a[2] and x[0] != a[0])
    return a[:2], b[:2]


def test_swap_moves_little_and_stays_valid(base, rules):
    s, tt = base
    (d1, p1), (d2, p2) = _plain_slots(s, tt["assignments"], "2 A")
    before = (_subject_at(s, tt["assignments"], "2 A", d1, p1), _subject_at(s, tt["assignments"], "2 A", d2, p2))
    r = E.propose(s, rules, tt, [{"type": "swap", "class": "2a", "day1": d1, "period1": p1,
                                  "day2": d2, "period2": p2}], time_limit=15)
    assert r["ok"], r
    new = r["timetable"]["assignments"]
    assert (_subject_at(s, new, "2 A", d2, p2), _subject_at(s, new, "2 A", d1, p1)) == before
    assert verify(s, new, rules)["valid"]
    # a swap should touch a handful of periods, not reshuffle the school
    assert 2 <= r["diff"]["moved_periods"] <= 12, r["diff"]["changes"]


def test_move_with_source_slot(base, rules):
    s, tt = base
    (d1, p1), (d2, p2) = _plain_slots(s, tt["assignments"], "3 B")
    subj = _subject_at(s, tt["assignments"], "3 B", d1, p1)
    r = E.propose(s, rules, tt, [{"type": "move", "class": "3 B", "subject": subj.lower(), "from_day": d1,
                                  "from_period": p1, "to_day": d2, "to_period": p2}], time_limit=15)
    assert r["ok"], r
    new = r["timetable"]["assignments"]
    assert _subject_at(s, new, "3 B", d2, p2) == subj
    assert _subject_at(s, new, "3 B", d1, p1) != subj


def test_avoid_and_teacher_unavailable(base, rules):
    s, tt = base
    asg = tt["assignments"]
    (d1, p1), _ = _plain_slots(s, asg, "1 B")
    subj = _subject_at(s, asg, "1 B", d1, p1)
    cts = {c["class_teacher_id"] for c in s["classes"]}
    teacher = next(t for t in s["teachers"] if t["id"] not in cts
                   and any(a["teacher_id"] == t["id"] and a["day"] == "Tuesday" and a["period"] in (3, 4) for a in asg))
    r = E.propose(s, rules, tt, [
        {"type": "avoid", "class": "1 B", "day": d1, "period": p1},
        {"type": "teacher_unavailable", "teacher": teacher["name"].lower(), "day": "tue", "periods": [3, 4]},
    ], time_limit=15)
    assert r["ok"], r
    new = r["timetable"]["assignments"]
    assert _subject_at(s, new, "1 B", d1, p1) != subj
    assert not any(a["teacher_id"] == teacher["id"] and a["day"] == "Tuesday" and a["period"] in (3, 4) for a in new)


def test_impossible_request_is_explained(base, rules):
    s, tt = base
    r = E.propose(s, rules, tt, [{"type": "move", "class": "1 A", "subject": "CLASS TEACHER",
                                  "to_day": "Monday", "to_period": 5}], time_limit=15)
    assert not r["ok"] and r["reasons"]
    assert any("CLASS TEACHER" in x for x in r["reasons"])


def test_already_true_is_a_no_op(base, rules):
    s, tt = base
    (d1, p1), _ = _plain_slots(s, tt["assignments"], "4 A")
    subj = _subject_at(s, tt["assignments"], "4 A", d1, p1)
    r = E.propose(s, rules, tt, [{"type": "move", "class": "4 A", "subject": subj, "to_day": d1, "to_period": p1}],
                  time_limit=10)
    assert r["ok"] and r["diff"]["moved_periods"] == 0


@pytest.mark.parametrize("req,msg", [
    ({"type": "swap", "class": "9 Z", "day1": "Mon", "period1": 2, "day2": "Tue", "period2": 3}, "don't know a class"),
    ({"type": "move", "class": "1 A", "subject": "MATHS", "to_day": "Funday", "to_period": 2}, "isn't a school day"),
    ({"type": "move", "class": "1 A", "subject": "MATHS", "to_day": "Mon", "to_period": 11}, "doesn't exist"),
    ({"type": "teleport", "class": "1 A"}, "Unknown change type"),
    ({"type": "move", "class": "1 A", "subject": "GEOGRAPHY", "to_day": "Mon", "to_period": 2}, "don't know a subject"),
])
def test_bad_requests_get_readable_errors(base, rules, req, msg):
    s, tt = base
    r = E.propose(s, rules, tt, [req], time_limit=5)
    assert not r["ok"] and msg in r["message"], r


def test_name_resolution(small_school):
    s = small_school
    assert E.resolve_class(s, "class 2b")["name"] == "2 B"
    assert E.resolve_class(s, "2-B")["name"] == "2 B"
    assert E.resolve_teacher(s, "teacher 003")["name"] == "TEACHER 003"
    assert E.resolve_day(s, "thurs") == "Thursday" and E.resolve_day(s, "Sat") == "Saturday"
    assert E.resolve_subject(s, "phy edu")["name"] == "PHY EDU"
    assert E.resolve_subject(s, "MATHZ")["name"] == "MATHS"   # typo tolerated; the preview shows it
    with pytest.raises(E.EditError):
        E.resolve_class(s, "2")   # 2 A or 2 B


def test_cover_plan_uses_free_teachers(base):
    s, tt = base
    asg = tt["assignments"]
    absent = asg[5]["teacher_id"]
    plan = E.substitutes(s, asg, absent, asg[5]["day"])
    assert plan
    tid = {t["name"]: t["id"] for t in s["teachers"]}
    for p in plan:
        sub = p["suggested"]
        assert sub and tid[sub["teacher"]] != absent
        assert not any(a["teacher_id"] == tid[sub["teacher"]] and a["day"] == asg[5]["day"]
                       and a["period"] == p["period"] for a in asg)


def test_standing_edits_survive_new_requests(base, rules):
    s, tt = base
    (d1, p1), (d2, p2) = _plain_slots(s, tt["assignments"], "2 A")
    first = E.propose(s, rules, tt, [{"type": "swap", "class": "2 A", "day1": d1, "period1": p1,
                                      "day2": d2, "period2": p2}], time_limit=15)
    assert first["ok"]
    moved_subj = _subject_at(s, first["timetable"]["assignments"], "2 A", d2, p2)
    tt2 = first["timetable"]
    (e1, q1), (e2, q2) = _plain_slots(s, tt2["assignments"], "3 A")
    second = E.propose(s, rules, tt2, [{"type": "swap", "class": "3 A", "day1": e1, "period1": q1,
                                        "day2": e2, "period2": q2}], standing=first["compiled"], time_limit=15)
    assert second["ok"], second
    assert _subject_at(s, second["timetable"]["assignments"], "2 A", d2, p2) == moved_subj
