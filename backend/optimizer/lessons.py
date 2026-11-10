"""Normalise any supported school format into a list of lessons.

A *lesson* is the unit the solver places on the grid:
    {id, subject_id, class_ids[], teacher_ids[], periods_per_week,
     max_per_day, fixed_period, lab}
Every teacher and every class of a lesson is busy for each period it is placed in.

Supported inputs:
  * v2 ("lessons" key) - produced by backend/ingest/excel_loader.py
  * v1 legacy - classes carry "subject_ids" / "subject_assignments" and
    teachers carry "qualified_subjects" (backend/data/sample_school.json)
"""
from __future__ import annotations

import copy
from collections import defaultdict
from typing import Any, Dict, List


def _legacy_to_lessons(school: Dict[str, Any], rules: Dict[str, Any]) -> List[Dict]:
    subjects = {s["id"]: s for s in school["subjects"]}
    max_per_day = rules.get("class_rules", {}).get("max_periods_per_subject_per_day", 2)
    load = defaultdict(int)
    lessons = []
    for c in school["classes"]:
        if c.get("subject_assignments"):
            pairs = [(a["subject_id"], a["teacher_ids"]) for a in c["subject_assignments"]]
        else:
            pairs = []
            for sid in c.get("subject_ids", []):
                qualified = [t["id"] for t in school["teachers"] if sid in t.get("qualified_subjects", [])]
                if not qualified:
                    pairs.append((sid, []))
                    continue
                # v1 data lets *any* qualified teacher take the class. Fix the choice up-front,
                # balancing load, so the solver works on concrete lessons.
                best = min(qualified, key=lambda t: load[t])
                pairs.append((sid, [best]))
        for sid, tids in pairs:
            ppw = subjects[sid]["periods_per_week"]
            for t in tids:
                load[t] += ppw
            lessons.append({
                "id": f"L{len(lessons) + 1:04d}", "subject_id": sid, "class_ids": [c["id"]],
                "teacher_ids": list(tids), "periods_per_week": ppw, "max_per_day": max_per_day,
                "fixed_period": None, "lab": "LAB" if subjects[sid].get("requires_lab") else None,
            })
    return lessons


def normalise(school: Dict[str, Any], rules: Dict[str, Any]) -> Dict[str, Any]:
    """Return a deep copy of `school` guaranteed to have `lessons` and `labs`."""
    s = copy.deepcopy(school)
    if not s.get("lessons"):
        s["lessons"] = _legacy_to_lessons(s, rules)
        if "labs" not in s:
            s["labs"] = {"LAB": sum(1 for r in s.get("rooms", []) if r.get("is_lab"))}
        for r in s.get("rooms", []):
            if r.get("is_lab"):
                r.setdefault("lab_type", "LAB")
        # Legacy CT-first-period rule applies to a subject by name.
        ct_name = rules.get("class_rules", {}).get("first_period_reserved")
        if ct_name:
            ct_ids = {x["id"] for x in s["subjects"] if x["name"] == ct_name}
            for l in s["lessons"]:
                if l["subject_id"] in ct_ids:
                    l["fixed_period"] = 1
    s.setdefault("labs", {})
    return s


def solvable_classes(school: Dict[str, Any]) -> List[Dict]:
    return [c for c in school["classes"] if not c.get("self_contained")]
