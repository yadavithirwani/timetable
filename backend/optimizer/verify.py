"""Independent checker for a generated timetable.

It never looks at the CP-SAT model: it takes the school data and the flat list of
assignments and re-checks every rule from scratch. If the solver (or a future edit
to it) ever produces something wrong, this is what catches it. The old version
reported "OPTIMAL" while 41% of all periods were a fictional SELF STUDY teacher and
co-teachers were double-booked; a check like this would have flagged it at once.

Also computes quality metrics (teacher idle gaps, same-subject doubles, ...).
"""
from __future__ import annotations

from collections import Counter, defaultdict
from typing import Any, Dict, List

from backend.optimizer.lessons import normalise


def verify(school_in: Dict[str, Any], assignments: List[Dict], rules: Dict[str, Any],
           locked: List[Dict] = None, forbidden: List[Dict] = None) -> Dict[str, Any]:
    s = normalise(school_in, rules)
    days, P = s["days"], s["periods_per_day"]
    subj = {x["id"]: x["name"] for x in s["subjects"]}
    tname = {t["id"]: t["name"] for t in s["teachers"]}
    cname = {c["id"]: c["name"] for c in s["classes"]}
    lessons = {l["id"]: l for l in s["lessons"]}
    cr = rules.get("class_rules", {})
    tr = rules.get("teacher_rules", {})
    v: List[str] = []

    # --- basic shape --------------------------------------------------------
    for a in assignments:
        if a["day"] not in days or not (1 <= a["period"] <= P):
            v.append(f"Assignment outside the grid: {a}")

    # slots each lesson occupies, seen from every (class, teacher) row
    slots_by_lesson = defaultdict(set)
    rows_by_lesson_slot = defaultdict(set)
    for a in assignments:
        if a["lesson_id"].startswith("HOME_"):
            continue
        slots_by_lesson[a["lesson_id"]].add((a["day"], a["period"]))
        rows_by_lesson_slot[a["lesson_id"], a["day"], a["period"]].add((a["class_id"], a["teacher_id"]))

    # --- every lesson placed exactly periods_per_week times, with ALL its classes and teachers
    for lid, l in lessons.items():
        n = len(slots_by_lesson.get(lid, ()))
        label = f"{subj[l['subject_id']]} for {' + '.join(cname.get(c, c) for c in l['class_ids'])}"
        if n != l["periods_per_week"]:
            v.append(f"{label}: scheduled {n} times, needs {l['periods_per_week']}")
        want = {(c, t) for c in l["class_ids"] for t in (l["teacher_ids"] or [""])}
        for (d, p) in slots_by_lesson.get(lid, ()):
            got = rows_by_lesson_slot[lid, d, p]
            if got != want:
                v.append(f"{label} on {d} P{p}: classes/teachers present {sorted(got)} != {sorted(want)}")
        if l.get("fixed_period"):
            bad = [x for x in slots_by_lesson.get(lid, ()) if x[1] != l["fixed_period"]]
            if bad:
                v.append(f"{label} must be in period {l['fixed_period']}, found at {sorted(bad)}")
        per_day = Counter(d for d, _ in slots_by_lesson.get(lid, ()))
        cap = l.get("max_per_day", 2)
        for d, k in per_day.items():
            if k > cap:
                v.append(f"{label}: {k} periods on {d} (max {cap})")
        if cr.get("lab_practicals_as_double_period") and l.get("lab") and l["periods_per_week"] % 2 == 0:
            for d in per_day:
                ps = sorted(p for dd, p in slots_by_lesson[lid] if dd == d)
                if len(ps) != 2 or ps[1] - ps[0] != 1:
                    v.append(f"{label}: practical on {d} is not a double period ({ps})")

    # --- class clashes: at most one distinct lesson per class slot
    class_slot = defaultdict(set)
    for a in assignments:
        class_slot[a["class_id"], a["day"], a["period"]].add(a["lesson_id"])
    for (c, d, p), ls in class_slot.items():
        if len(ls) > 1:
            v.append(f"Class {cname.get(c, c)} has {len(ls)} lessons on {d} P{p}")

    # --- teacher clashes (respecting optional clubbing rule)
    club = tr.get("clubbed_classes", {})
    clubbable = set(club.get("allowed_subjects", [])) if club.get("enabled") else set()
    k = club.get("max_concurrent", 2)
    teacher_slot = defaultdict(set)
    for a in assignments:
        if a["teacher_id"]:
            teacher_slot[a["teacher_id"], a["day"], a["period"]].add((a["lesson_id"], a["subject_id"]))
    for (t, d, p), ls in teacher_slot.items():
        std = [x for x in ls if subj.get(x[1]) not in clubbable]
        clb = [x for x in ls if subj.get(x[1]) in clubbable]
        if (std and len(ls) > 1) or len(clb) > k:
            v.append(f"Teacher {tname.get(t, t)} double-booked on {d} P{p}: "
                     f"{sorted(subj.get(x[1], x[1]) for x in ls)}")

    # --- teacher daily maximum and unavailability
    tday = Counter()
    for (t, d, p) in teacher_slot:
        tday[t, d] += 1
    max_day = tr.get("max_periods_per_day", P)
    for (t, d), n in tday.items():
        if n > max_day and not any(c.get("class_teacher_id") == t and c.get("self_contained") for c in s["classes"]):
            v.append(f"Teacher {tname.get(t, t)} has {n} periods on {d} (max {max_day})")
    for t in s["teachers"]:
        for u in t.get("unavailable_slots", []):
            if (t["id"], u["day"], u["period"]) in teacher_slot:
                v.append(f"Teacher {t['name']} scheduled while unavailable on {u['day']} P{u['period']}")

    # --- rooms: no room hosts two different lessons
    room_slot = defaultdict(set)
    for a in assignments:
        if a["room_id"]:
            room_slot[a["room_id"], a["day"], a["period"]].add(a["lesson_id"])
    for (r, d, p), ls in room_slot.items():
        if len(ls) > 1:
            v.append(f"Room {r} double-booked on {d} P{p}")

    # --- locks honoured
    for lk in locked or []:
        if not any(a["class_id"] == lk.get("class_id") and a["subject_id"] == lk.get("subject_id")
                   and a["day"] == lk.get("day") and a["period"] == lk.get("period") for a in assignments):
            v.append(f"Requested placement not honoured: {lk}")

    for fb in forbidden or []:
        for a in assignments:
            if a["class_id"] == fb.get("class_id") and a["day"] == fb.get("day") and a["period"] == fb.get("period") \
                    and (not fb.get("subject_id") or a["subject_id"] == fb["subject_id"]) \
                    and (not fb.get("lesson_id") or a["lesson_id"] == fb["lesson_id"]):
                v.append(f"Requested exclusion not honoured: {fb}")
                break

    return {"valid": not v, "violations": v, "quality": quality(s, assignments, rules)}


def quality(s: Dict[str, Any], assignments: List[Dict], rules: Dict[str, Any]) -> Dict[str, Any]:
    days, P = s["days"], s["periods_per_day"]
    subj = {x["id"]: x["name"] for x in s["subjects"]}
    solvable = [c["id"] for c in s["classes"] if not c.get("self_contained")]

    occupied = {(a["class_id"], a["day"], a["period"]) for a in assignments}
    free = sum(1 for c in solvable for d in days for p in range(1, P + 1) if (c, d, p) not in occupied)

    # teacher idle gaps between first and last period of the day
    tslots = defaultdict(set)
    for a in assignments:
        if a["teacher_id"] and not a["lesson_id"].startswith("HOME_"):
            tslots[a["teacher_id"], a["day"]].add(a["period"])
    gaps = sum((max(ps) - min(ps) + 1) - len(ps) for ps in tslots.values())
    max_run = rules.get("teacher_rules", {}).get("max_consecutive_periods", P)
    longest_run = long_run_days = 0
    for ps in tslots.values():
        run = best = 0
        for p in range(1, P + 1):
            run = run + 1 if p in ps else 0
            best = max(best, run)
        longest_run = max(longest_run, best)
        long_run_days += best > max_run

    # same lesson twice in a day (excluding practical double periods)
    per_lesson_day = Counter()
    seen = set()
    for a in assignments:
        key = (a["lesson_id"], a["day"], a["period"])
        if key in seen or a["lesson_id"].startswith("HOME_"):
            continue
        seen.add(key)
        per_lesson_day[a["lesson_id"], a["day"]] += 1
    lab_lessons = {l["id"] for l in s["lessons"] if l.get("lab")}
    doubles = sum(1 for (lid, d), n in per_lesson_day.items() if n > 1 and lid not in lab_lessons)

    heavy = set(rules.get("class_rules", {}).get("heavy_subjects", []))
    late_after = rules.get("class_rules", {}).get("heavy_subjects_not_after_period", P)
    late = len({(a["lesson_id"], a["day"], a["period"]) for a in assignments
                if subj.get(a["subject_id"]) in heavy and a["period"] > late_after})

    loads = Counter()
    for (t, d), ps in tslots.items():
        loads[t] += len(ps)
    return {
        "free_class_periods": free,
        "teacher_idle_gaps": gaps,
        "longest_teacher_run": longest_run,
        "teacher_days_over_max_run": long_run_days,
        "same_subject_twice_a_day": doubles,
        "heavy_subject_periods_late": late,
        "busiest_teacher_week": max(loads.values()) if loads else 0,
    }
