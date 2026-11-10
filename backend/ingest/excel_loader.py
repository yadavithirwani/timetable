"""
Turn the school's "Contracts Classes" Excel export into a lesson-based school model.

Key modelling decisions (each one fixes a bug in the original parser):

1. One Excel row == one *lesson*. A lesson has a subject, one or more classes and
   one or more teachers. Every listed teacher is busy for every period of that
   lesson (co-teaching / split groups such as DANCE/YOGA). The old parser treated
   "A,B" as "A *or* B", which produced timetables that left co-teachers double-booked.

2. Electives that run across sections (e.g. VPA for 9 A + 9 B with the same six
   teachers) are merged into one *joint* lesson so all sections sit it at the same
   time. Rows are merged when grade, subject and the exact teacher set match and
   there is more than one teacher.

3. Period counts are not in the Excel. They come from config/period_quotas.json.
   Any class whose lessons don't add up to the full week gets the gap filled with
   class-teacher periods (configurable) instead of a fictional "SELF STUDY" teacher
   with infinite capacity.

4. Classes that only have a CLASS TEACHER row (Nursery, Pre-Primary) are
   self-contained: their class teacher takes every period and they never interact
   with the rest of the school, so they are not sent to the solver.
"""
from __future__ import annotations

import json
import os
import re
from collections import defaultdict
from typing import Any, Dict, List, Optional, Tuple

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
DEFAULT_EXCEL = os.path.join(ROOT, "config", "SHIKHA_CLASSWISE.xlsx")
DEFAULT_QUOTAS = os.path.join(ROOT, "config", "period_quotas.json")
DEFAULT_OUTPUT = os.path.join(ROOT, "config", "my_school_config.json")

CLASS_TEACHER = "CLASS TEACHER"
CT_ACTIVITY = "CT ACTIVITY"


def _slug(text: str) -> str:
    return re.sub(r"[^A-Z0-9]+", "", text.upper())


def normalise_class_name(raw: str) -> Optional[Tuple[str, int, str]]:
    """'.9A' -> ('9 A', 9, 'A'); 'NURSERY B' -> ('NURSERY B', -1, 'B')."""
    s = str(raw).strip().lstrip(".").strip().upper()
    if not s or s == "NAN":
        return None
    s = re.sub(r"\s+", " ", s)
    m = re.fullmatch(r"(\d{1,2})\s*([A-Z])", s)
    if m:
        return f"{int(m.group(1))} {m.group(2)}", int(m.group(1)), m.group(2)
    m = re.fullmatch(r"(NURSERY|PRE PRIMARY|LKG|UKG|PREP)\s*([A-Z])", s)
    if m:
        grade = -1 if m.group(1) in ("NURSERY", "LKG") else 0
        return f"{m.group(1)} {m.group(2)}", grade, m.group(2)
    return None


def read_contract_rows(excel_path: str) -> List[Dict[str, Any]]:
    """Return [{class, teachers[], subject}] from the class-wise contract export."""
    import pandas as pd  # imported lazily so the solver does not need pandas

    df = pd.read_excel(excel_path, sheet_name=0)
    cols = {c.lower().strip(): c for c in df.columns}
    for need in ("class", "teacher", "subject"):
        if need not in cols:
            raise ValueError(f"Excel is missing a '{need}' column (found {list(df.columns)})")
    df = df.rename(columns={cols["class"]: "Class", cols["teacher"]: "Teacher", cols["subject"]: "Subject"})
    df["Class"] = df["Class"].ffill()
    df = df.dropna(subset=["Teacher", "Subject"])

    rows = []
    for _, r in df.iterrows():
        teachers = [re.sub(r"\s+", " ", t).strip().upper() for t in str(r["Teacher"]).split(",")]
        teachers = [t for t in teachers if t]
        subject = re.sub(r"\s+", " ", str(r["Subject"])).strip().upper()
        if teachers and subject:
            rows.append({"class": str(r["Class"]), "teachers": teachers, "subject": subject})
    return rows


class QuotaBook:
    def __init__(self, cfg: Dict[str, Any]):
        self.cfg = cfg
        self.grade_to_band = {}
        for band, spec in cfg.get("bands", {}).items():
            for g in spec["grades"]:
                self.grade_to_band[g] = band

    def lookup(self, class_name: str, grade: int, subject: str) -> Optional[int]:
        over = self.cfg.get("class_overrides", {}).get(class_name, {})
        if subject in over:
            return over[subject]
        band = self.grade_to_band.get(grade)
        bq = self.cfg.get("band_quotas", {}).get(band, {})
        if subject in bq:
            return bq[subject]
        return self.cfg.get("default", {}).get(subject)


def build_school(rows: List[Dict[str, Any]], quota_cfg: Dict[str, Any]) -> Dict[str, Any]:
    days = quota_cfg["calendar"]["days"]
    ppd = quota_cfg["calendar"]["periods_per_day"]
    slots = len(days) * ppd
    quotas = QuotaBook(quota_cfg)
    max_per_day = quota_cfg.get("max_periods_per_day_per_lesson", 2)
    ct_first = quota_cfg.get("class_teacher_first_period", True)
    lab_of = {}
    for lab, spec in quota_cfg.get("labs", {}).items():
        for s in spec["subjects"]:
            lab_of[s] = lab

    warnings: List[str] = []
    classes: Dict[str, Dict] = {}
    teachers: Dict[str, Dict] = {}
    subjects: Dict[str, Dict] = {}

    def teacher_id(name):
        tid = f"t_{_slug(name)}"
        teachers.setdefault(tid, {"id": tid, "name": name, "qualified_subjects": [],
                                  "max_periods_per_day": ppd, "unavailable_slots": []})
        return tid

    def subject_id(name):
        sid = f"sub_{_slug(name)}"
        subjects.setdefault(sid, {"id": sid, "name": name, "periods_per_week": 0,
                                  "requires_lab": name in lab_of})
        return sid

    # --- 1. normalise rows ------------------------------------------------
    per_class_rows: Dict[str, List[Dict]] = defaultdict(list)
    for r in rows:
        norm = normalise_class_name(r["class"])
        if not norm:
            warnings.append(f"Skipped row with unreadable class name '{r['class']}'")
            continue
        cname, grade, section = norm
        cid = f"c_{_slug(cname)}"
        classes.setdefault(cid, {"id": cid, "name": cname, "grade": grade, "section": section,
                                 "subject_ids": [], "self_contained": False, "class_teacher_id": None})
        per_class_rows[cid].append({**r, "class_id": cid})

    # --- 2. build lessons, merging joint electives -------------------------
    lessons: List[Dict] = []
    joint_index: Dict[Tuple, Dict] = {}
    for cid, crow in per_class_rows.items():
        c = classes[cid]
        occurrence = defaultdict(int)
        only_ct = all(r["subject"] == CLASS_TEACHER for r in crow)
        if only_ct:
            c["self_contained"] = True
            c["class_teacher_id"] = teacher_id(crow[0]["teachers"][0])
            continue
        for r in crow:
            subj = r["subject"]
            sid = subject_id(subj)
            tids = sorted({teacher_id(t) for t in r["teachers"]})
            for t in tids:
                if sid not in teachers[t]["qualified_subjects"]:
                    teachers[t]["qualified_subjects"].append(sid)
            if sid not in c["subject_ids"]:
                c["subject_ids"].append(sid)
            if subj == CLASS_TEACHER:
                c["class_teacher_id"] = tids[0]
            q = quotas.lookup(c["name"], c["grade"], subj)
            if q is None:
                warnings.append(f"No period quota for '{subj}' in class {c['name']} - assumed 1/week. "
                                f"Add it to config/period_quotas.json.")
                q = 1
            key_base = (c["grade"], subj, tuple(tids))
            occurrence[key_base] += 1
            key = key_base + (occurrence[key_base],)
            if len(tids) > 1 and key in joint_index:
                joint_index[key]["class_ids"].append(cid)
                continue
            lesson = {
                "id": f"L{len(lessons) + 1:04d}",
                "subject_id": sid,
                "class_ids": [cid],
                "teacher_ids": tids,
                "periods_per_week": q,
                "max_per_day": max_per_day,
                "fixed_period": 1 if subj == CLASS_TEACHER and ct_first else None,
                "lab": lab_of.get(subj),
            }
            lessons.append(lesson)
            if len(tids) > 1:
                joint_index[key] = lesson

    # --- 2b. teacher availability (part-timers, fixed days off), keyed by teacher name -------
    by_name = {t["name"].strip().upper(): t for t in teachers.values()}
    for name, slots_off in (quota_cfg.get("teacher_unavailable") or {}).items():
        t = by_name.get(str(name).strip().upper())
        if not t:
            warnings.append(f"Availability set for '{name}', who has no lessons in this file - ignored.")
            continue
        seen = set()
        for u in slots_off:
            key = (u.get("day"), u.get("period"))
            if key[0] in days and isinstance(key[1], int) and 1 <= key[1] <= ppd and key not in seen:
                seen.add(key)
                t["unavailable_slots"].append({"day": key[0], "period": key[1]})

    # --- 3. fill gaps with class-teacher periods ---------------------------
    load = defaultdict(int)
    for l in lessons:
        for cid in l["class_ids"]:
            load[cid] += l["periods_per_week"]
    tload = defaultdict(int)
    for l in lessons:
        for t in l["teacher_ids"]:
            tload[t] += l["periods_per_week"]

    if quota_cfg.get("fill_remaining_with_class_teacher", True):
        act_sid = subject_id(CT_ACTIVITY)
        for cid, c in classes.items():
            if c["self_contained"]:
                continue
            gap = slots - load[cid]
            ct = c["class_teacher_id"]
            if gap <= 0 or not ct:
                continue
            spare = slots - len(teachers[ct]["unavailable_slots"]) - tload[ct]
            fill = min(gap, max(0, spare))
            if fill < gap:
                warnings.append(f"{c['name']}: {gap} unassigned periods but class teacher "
                                f"{teachers[ct]['name']} only has {spare} spare - {gap - fill} will stay free.")
            if fill > 0:
                lessons.append({"id": f"L{len(lessons) + 1:04d}", "subject_id": act_sid, "class_ids": [cid],
                                "teacher_ids": [ct], "periods_per_week": fill, "max_per_day": max_per_day,
                                "fixed_period": None, "lab": None})
                load[cid] += fill
                tload[ct] += fill
                if act_sid not in c["subject_ids"]:
                    c["subject_ids"].append(act_sid)

    # --- 4. rooms: one homeroom per class + configured labs ------------------
    rooms = [{"id": f"r_{cid}", "name": f"Room {c['name']}", "capacity": 50, "is_lab": False}
             for cid, c in classes.items()]
    for lab, spec in quota_cfg.get("labs", {}).items():
        for i in range(1, spec["count"] + 1):
            rooms.append({"id": f"r_{_slug(lab)}_{i}", "name": f"{lab.title()} {i}", "capacity": 45,
                          "is_lab": True, "lab_type": lab})

    return {
        "version": 2,
        "days": days,
        "periods_per_day": ppd,
        "subjects": list(subjects.values()),
        "teachers": list(teachers.values()),
        "classes": list(classes.values()),
        "rooms": rooms,
        "lessons": lessons,
        "labs": {k: v["count"] for k, v in quota_cfg.get("labs", {}).items()},
        "import_warnings": warnings,
    }


def load_school_from_excel(excel_path: str = DEFAULT_EXCEL, quotas_path: str = DEFAULT_QUOTAS) -> Dict[str, Any]:
    with open(quotas_path) as f:
        quota_cfg = json.load(f)
    return build_school(read_contract_rows(excel_path), quota_cfg)


def main(argv=None):
    import argparse

    ap = argparse.ArgumentParser(description="Convert the class-wise Excel export into my_school_config.json")
    ap.add_argument("--excel", default=DEFAULT_EXCEL)
    ap.add_argument("--quotas", default=DEFAULT_QUOTAS)
    ap.add_argument("--out", default=DEFAULT_OUTPUT)
    args = ap.parse_args(argv)
    school = load_school_from_excel(args.excel, args.quotas)
    with open(args.out, "w") as f:
        json.dump(school, f, indent=2)
    print(f"{len(school['classes'])} classes, {len(school['teachers'])} teachers, "
          f"{len(school['lessons'])} lessons -> {args.out}")
    for w in school["import_warnings"]:
        print("  warning:", w)


if __name__ == "__main__":
    main()
