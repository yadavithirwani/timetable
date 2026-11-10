"""Fast, plain-English sanity checks run *before* the solver.

Most real-world failures are data problems (a teacher given 52 periods, a class
with 50 periods in a 48-slot week). These are cheap to detect by counting and much
easier for a non-technical user to act on than a solver status code.
"""
from __future__ import annotations

from collections import defaultdict
from typing import Any, Dict, List

from backend.optimizer.lessons import normalise


def check(school_in: Dict[str, Any], rules: Dict[str, Any]) -> Dict[str, List[str]]:
    s = normalise(school_in, rules)
    days, P = s["days"], s["periods_per_day"]
    D, slots = len(days), len(days) * P
    subj = {x["id"]: x["name"] for x in s["subjects"]}
    tname = {t["id"]: t["name"] for t in s["teachers"]}
    cname = {c["id"]: c["name"] for c in s["classes"]}
    tr = rules.get("teacher_rules", {})
    max_day = tr.get("max_periods_per_day", P)
    club = tr.get("clubbed_classes", {})
    clubbable = set(club.get("allowed_subjects", [])) if club.get("enabled") else set()

    errors, warnings = [], []
    known_classes = set(cname)
    known_teachers = set(tname)

    class_load = defaultdict(int)
    teacher_load = defaultdict(int)
    teacher_p1 = defaultdict(list)
    lab_load = defaultdict(int)
    for l in s["lessons"]:
        label = f"{subj.get(l['subject_id'], l['subject_id'])} for " + \
                " + ".join(cname.get(c, c) for c in l["class_ids"])
        if l["subject_id"] not in subj:
            errors.append(f"{label}: unknown subject id {l['subject_id']}")
        for c in l["class_ids"]:
            if c not in known_classes:
                errors.append(f"{label}: unknown class id {c}")
            class_load[c] += l["periods_per_week"]
        if not l["teacher_ids"]:
            errors.append(f"{label}: no teacher assigned")
        for t in l["teacher_ids"]:
            if t not in known_teachers:
                errors.append(f"{label}: unknown teacher id {t}")
            if subj.get(l["subject_id"]) not in clubbable:
                teacher_load[t] += l["periods_per_week"]
            if l.get("fixed_period"):
                teacher_p1[t].append((label, l["periods_per_week"]))
        cap = l.get("max_per_day", 2)
        if l["periods_per_week"] > D * cap:
            errors.append(f"{label} needs {l['periods_per_week']} periods but can have at most {cap} a day "
                          f"({D * cap} a week). Lower the quota or allow more per day.")
        if l.get("fixed_period") and l["periods_per_week"] > D:
            errors.append(f"{label} is fixed to period {l['fixed_period']} but needs {l['periods_per_week']} "
                          f"periods - there are only {D} days.")
        if l.get("lab"):
            lab_load[l["lab"]] += l["periods_per_week"]

    for c, n in class_load.items():
        if n > slots:
            errors.append(f"Class {cname.get(c, c)} has {n} periods of lessons but the week only has {slots}. "
                          f"Reduce some quotas.")
        elif n < slots:
            warnings.append(f"Class {cname.get(c, c)} has {n} of {slots} periods filled - "
                            f"{slots - n} will be free periods.")

    tby = {x["id"]: x for x in s["teachers"]}

    def off(t, day=None, period=None):
        return [u for u in tby.get(t, {}).get("unavailable_slots", [])
                if (day is None or u["day"] == day) and (period is None or u["period"] == period)]

    for t, n in teacher_load.items():
        # per day: at most max_day periods, and never in a slot they're unavailable
        cap = sum(min(max_day, P - len(off(t, d))) for d in s["days"])
        if n > cap:
            errors.append(f"{tname.get(t, t)} is given {n} periods a week but can teach at most {cap}.")
        elif n > 0.9 * cap:
            warnings.append(f"{tname.get(t, t)} is almost fully booked ({n}/{cap} periods) - "
                            f"little room for the solver to move things.")

    for t, items in teacher_p1.items():
        need = sum(n for _, n in items)
        free_p1 = D - len(off(t, period=1))
        if need > free_p1 and need <= D:
            errors.append(f"{tname.get(t, t)} must be in period 1 for {need} lessons a week "
                          f"({', '.join(x for x, _ in items)}) but is unavailable in period 1 on "
                          f"{', '.join(u['day'] for u in off(t, period=1))}.")
        if need > D:
            errors.append(f"{tname.get(t, t)} must be in period 1 for {need} lessons a week "
                          f"({', '.join(x for x, _ in items)}) but there are only {D} first periods.")

    for lab, n in lab_load.items():
        cap = s.get("labs", {}).get(lab, 0) * slots
        if n > cap:
            errors.append(f"{lab}: {n} practical periods needed but only {cap} lab-periods exist.")

    return {"errors": errors, "warnings": warnings}
