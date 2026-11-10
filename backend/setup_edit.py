"""Setup changes requested in plain words (used by the chat assistant).

These change the school's *data* - periods per week, labs, teacher availability, the
week - rather than one timetable. Like timetable edits they are only previewed: the
new setup is built and checked in memory, and saved only when the user clicks Apply
(which, like saving the Review page, means the timetable has to be generated again).

    {"type": "set_periods", "subject": "MATHS", "periods": 7, "grades": "6-8"}   # whole grade group
    {"type": "set_periods", "subject": "MATHS", "periods": 7, "class": "6 A"}    # one class only
    {"type": "teacher_availability", "teacher": "R RAO", "day": "Sat", "periods": [1, 2], "available": false}
    {"type": "lab", "lab": "COMPUTER LAB", "rooms": 2, "add_subjects": [...], "remove_subjects": [...]}
    {"type": "week", "days": ["Monday", ...], "periods_per_day": 8}
"""
from __future__ import annotations

import copy
import re
from typing import Any, Dict, List, Optional, Tuple

from backend.editing import EditError, _resolve
from backend.ingest.excel_loader import QuotaBook, build_school, normalise_class_name
from backend.optimizer import diagnostics

ALL_DAYS = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]


def _items(names):
    return [{"id": n, "name": n} for n in sorted(set(names))]


class Ctx:
    def __init__(self, rows: List[Dict], quotas: Dict):
        self.rows, self.q = rows, quotas
        self.subjects = _items(r["subject"] for r in rows)
        self.teachers = _items(t for r in rows for t in r["teachers"])
        self.classes = _items(n[0] for n in (normalise_class_name(r["class"]) for r in rows) if n)
        self.book = QuotaBook(quotas)

    def subject(self, q):
        return _resolve("subject", q, self.subjects)["name"]

    def teacher(self, q):
        return _resolve("teacher", q, self.teachers)["name"]

    def klass(self, q):
        return _resolve("class", q, self.classes)["name"]

    def day(self, q, days=None):
        days = days or self.q["calendar"]["days"]
        k = re.sub(r"[^a-z]", "", str(q).lower())
        for d in days:
            if d.lower() == k or (len(k) >= 2 and d.lower().startswith(k[:3])):
                return d
        raise EditError(f"'{q}' isn't a school day ({', '.join(days)}).")

    def band(self, q) -> str:
        bands = self.q.get("bands", {})
        s = str(q).strip().lower()
        if s in bands:
            return s
        if any(w in s for w in ("nursery", "pre", "kg")) and "pre" in bands:
            return "pre"
        nums = [int(x) for x in re.findall(r"\d+", s)]
        if len(nums) == 1:
            hit = [b for b, spec in bands.items() if nums[0] in spec["grades"]]
            if hit:
                return hit[0]
        if len(nums) == 2:
            want = set(range(min(nums), max(nums) + 1))
            hit = [b for b, spec in bands.items() if set(spec["grades"]) == want]
            if hit:
                return hit[0]
        raise EditError(f"'{q}' isn't one of the grade groups: {', '.join(bands)}. "
                        f"(To change a single class, name the class instead.)")

    def period_value(self, cls: Optional[str], band: Optional[str], subject: str):
        if cls:
            n = normalise_class_name(cls)
            return self.book.lookup(n[0], n[1], subject)
        v = self.q.get("band_quotas", {}).get(band, {}).get(subject)
        return v if v is not None else self.q.get("default", {}).get(subject)


def apply_changes(rows: List[Dict], quotas: Dict, changes: List[Dict]) -> Tuple[Dict, List[str]]:
    q = copy.deepcopy(quotas)
    described: List[str] = []
    for ch in changes:
        ctx = Ctx(rows, q)
        kind = ch.get("type")
        if kind == "set_periods":
            subject = ctx.subject(ch.get("subject"))
            try:
                n = int(ch.get("periods"))
            except (TypeError, ValueError):
                raise EditError("How many periods a week? Give a number.")
            slots = len(q["calendar"]["days"]) * q["calendar"]["periods_per_day"]
            if not 0 <= n <= slots:
                raise EditError(f"{n} periods is outside 0-{slots}.")
            if ch.get("class"):
                cls = ctx.klass(ch["class"])
                before = ctx.period_value(cls, None, subject)
                q.setdefault("class_overrides", {}).setdefault(cls, {})[subject] = n
                described.append(f"{subject} for {cls} only: {before} → {n} period{"" if n == 1 else "s"} a week")
            elif ch.get("grades"):
                band = ctx.band(ch["grades"])
                before = ctx.period_value(None, band, subject)
                q.setdefault("band_quotas", {}).setdefault(band, {})[subject] = n
                keep = sorted(c for c, o in (q.get("class_overrides") or {}).items()
                              if subject in o and (normalise_class_name(c) or [0, None])[1] in q["bands"][band]["grades"])
                described.append(f"{subject} for grades {band}: {before} → {n} period{"" if n == 1 else "s"} a week" +
                                 (f" ({', '.join(keep)} keep{'s' if len(keep) == 1 else ''} its own value)" if keep else ""))
            else:
                raise EditError(f"For which grades or class should {subject} change?")
        elif kind == "teacher_availability":
            name = ctx.teacher(ch.get("teacher"))
            day = ctx.day(ch.get("day"))
            P = q["calendar"]["periods_per_day"]
            periods = ch.get("periods") or list(range(1, P + 1))
            periods = sorted({int(p) for p in periods if 1 <= int(p) <= P})
            off = q.setdefault("teacher_unavailable", {})
            cur = {(u["day"], u["period"]) for u in off.get(name, [])}
            keys = {(day, p) for p in periods}
            available = bool(ch.get("available", False))
            cur = cur - keys if available else cur | keys
            if cur:
                days = q["calendar"]["days"]
                off[name] = [{"day": d, "period": p} for d, p in sorted(cur, key=lambda x: (days.index(x[0]) if x[0] in days else 99, x[1]))]
            else:
                off.pop(name, None)
            when = "all day" if len(periods) == P else "period " + ", ".join(map(str, periods))
            described.append(f"{name} {'available' if available else 'not available'} on {day} {when}, every week")
        elif kind == "lab":
            labs = q.setdefault("labs", {})
            raw = str(ch.get("lab") or "").strip()
            if not raw:
                raise EditError("Which lab?")
            existing = [{"id": k, "name": k} for k in labs]
            try:
                name = _resolve("lab", raw, existing)["name"] if existing else raw.upper()
            except EditError:
                name = raw.upper()
            if ch.get("remove_lab"):
                if name not in labs:
                    raise EditError(f"There is no lab called {raw}.")
                del labs[name]
                described.append(f"Remove {name}")
                continue
            new = name not in labs
            spec = labs.setdefault(name, {"count": 1, "subjects": []})
            parts = ["Add " + name] if new else []
            if ch.get("rooms") is not None:
                r = int(ch["rooms"])
                if not 0 <= r <= 50:
                    raise EditError("Number of lab rooms must be 0-50.")
                if not new:
                    parts.append(f"{name}: {spec['count']} → {r} rooms")
                spec["count"] = r
            for s in ch.get("add_subjects") or []:
                s = ctx.subject(s)
                for other, o in labs.items():
                    if other != name and s in o.get("subjects", []):
                        o["subjects"].remove(s)
                        parts.append(f"{s} moves from {other}")
                if s not in spec["subjects"]:
                    spec["subjects"].append(s)
                    parts.append(f"{s} uses {name}")
            for s in ch.get("remove_subjects") or []:
                s = ctx.subject(s)
                if s in spec["subjects"]:
                    spec["subjects"].remove(s)
                    parts.append(f"{s} no longer uses {name}")
            if not parts:
                raise EditError(f"What should change about {name}?")
            described.append("; ".join(parts))
        elif kind == "week":
            if ch.get("days"):
                days = [ctx.day(d, ALL_DAYS) for d in ch["days"]]
                days = [d for d in ALL_DAYS if d in days]
                described.append(f"School days: {', '.join(q['calendar']['days'])} → {', '.join(days)}")
                q["calendar"]["days"] = days
            if ch.get("periods_per_day"):
                p = int(ch["periods_per_day"])
                if not 1 <= p <= 14:
                    raise EditError("Periods per day must be 1-14.")
                described.append(f"Periods per day: {q['calendar']['periods_per_day']} → {p}")
                q["calendar"]["periods_per_day"] = p
            if not ch.get("days") and not ch.get("periods_per_day"):
                raise EditError("What should change about the week?")
        else:
            raise EditError(f"Unknown setup change '{kind}'. Use set_periods, teacher_availability, lab or week.")
    return q, described


def preview(rows: List[Dict], quotas: Dict, rules: Dict, changes: List[Dict]) -> Dict[str, Any]:
    try:
        new_q, described = apply_changes(rows, quotas, changes)
    except EditError as e:
        return {"ok": False, "message": str(e)}
    before = diagnostics.check(build_school(rows, quotas), rules)
    after = diagnostics.check(build_school(rows, new_q), rules)
    new_errors = [e for e in after["errors"] if e not in before["errors"]]
    return {"ok": True, "quotas": new_q, "changes": described, "new_errors": new_errors,
            "fixed_errors": [e for e in before["errors"] if e not in after["errors"]],
            "still_errors": [e for e in after["errors"] if e in before["errors"]]}


def describe(rows: List[Dict], quotas: Dict, school: Dict, topic: str, name: Optional[str] = None) -> Dict:
    """Read-only view of the setup for the assistant."""
    ctx = Ctx(rows, quotas)
    topic = (topic or "").lower()
    if topic == "class":
        cls = ctx.klass(name)
        n = normalise_class_name(cls)
        subs = sorted({r["subject"] for r in rows if (normalise_class_name(r["class"]) or [None])[0] == cls})
        over = quotas.get("class_overrides", {}).get(cls, {})
        return {"class": cls, "grade_group": ctx.book.grade_to_band.get(n[1]),
                "periods_per_week": {s: ctx.book.lookup(cls, n[1], s) for s in subs},
                "own_values_for_this_class": over}
    if topic == "periods":
        band = ctx.band(name) if name else None
        bands = [band] if band else list(quotas.get("bands", {}))
        out = {}
        for b in bands:
            grades = set(quotas["bands"][b]["grades"])
            subs = sorted({r["subject"] for r in rows if (normalise_class_name(r["class"]) or [0, None])[1] in grades})
            out[b] = {s: ctx.period_value(None, b, s) for s in subs}
        return {"periods_per_week_by_grade_group": out}
    if topic == "teacher":
        t = ctx.teacher(name)
        return {"teacher": t, "not_available": quotas.get("teacher_unavailable", {}).get(t, []) or "every period"}
    if topic == "labs":
        return {"labs": quotas.get("labs", {})}
    if topic == "week":
        return {"days": quotas["calendar"]["days"], "periods_per_day": quotas["calendar"]["periods_per_day"],
                "grade_groups": {b: v["grades"] for b, v in quotas.get("bands", {}).items()},
                "part_time_teachers": sorted(quotas.get("teacher_unavailable", {}))}
    raise EditError("topic must be class, periods, teacher, labs or week")
