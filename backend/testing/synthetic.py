"""Planted-solution school generator.

Random test data has a problem: if the solver says "infeasible" you don't know
whether the solver is wrong or the data is impossible. So we generate the
*answer first*: build a valid timetable at random, then derive the school
(classes, teachers, lessons, quotas) from it. Every generated school is therefore
guaranteed to have at least one valid timetable - the planted one. If the solver
ever fails on one of these, it is a solver bug, full stop.

The generated schools mimic the real data: class teacher in period 1 (and often
teaching a core subject too), joint electives shared by paired sections with two
teachers, lab practicals as double periods with a limited number of labs, and
teachers loaded close to the real school's 35-44 periods a week.
"""
from __future__ import annotations

import random
from collections import defaultdict
from typing import Any, Dict, List, Optional, Set, Tuple

DAYS6 = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday"]

JUNIOR = [("ENG", 8), ("MATHS", 8), ("HINDI", 6), ("EVS", 5), ("ART", 2), ("MUSIC", 2), ("PHY EDU", 2),
          ("LIBRARY", 1), ("COMPUTERS", 2), ("GK", 1), ("DANCE", 2), ("STORY TIME", 1), ("LIFE SKILLS", 2)]
SENIOR = [("ENG", 6), ("MATHS", 7), ("SCIENCE", 6), ("SST", 5), ("HINDI", 4), ("PHY EDU", 2), ("LIBRARY", 1),
          ("COMPUTERS", 1), ("ART", 2), ("MUSIC", 2), ("KB", 1)]
ELECTIVE = ("ELECTIVE", 3)       # joint across paired sections, two teachers
PRACTICAL = ("COMPUTERS-PR", 2)  # one double period in a lab
CT_JUNIOR = {"ENG", "MATHS", "EVS", "GK", "STORY TIME", "LIFE SKILLS", "ART"}
CT_SENIOR = {"MATHS", "SCIENCE", "ENG", "SST", "HINDI"}

Slot = Tuple[int, int]


class _Retry(Exception):
    pass


def generate(grades: int = 4, sections: int = 4, seed: int = 0, n_labs: int = 2, teacher_cap: int = 40,
             periods_per_day: int = 8, days: Optional[List[str]] = None) -> Dict[str, Any]:
    """Return a v2 school dict plus `planted_solution` (a valid list of assignments)."""
    for attempt in range(300):
        try:
            return _Gen(grades, sections, random.Random(seed * 1000 + attempt), n_labs, teacher_cap,
                        periods_per_day, days or DAYS6).run()
        except _Retry:
            continue
    raise RuntimeError("generator could not plant a timetable - parameters too tight")


class _Gen:
    def __init__(self, grades, sections, rng, n_labs, cap, P, days):
        self.rng, self.n_labs, self.cap, self.P, self.days = rng, n_labs, cap, P, days
        self.D = len(days)
        self.classes = [{"id": f"c_{g}{s}", "name": f"{g} {s}", "grade": g, "section": s, "subject_ids": [],
                         "self_contained": False, "class_teacher_id": None}
                        for g in range(1, grades + 1) for s in "ABCDEFGH"[:sections]]
        self.senior_from = grades // 2 + 1
        self.cgrid: Dict[str, Set[Slot]] = defaultdict(set)
        self.tbusy: Dict[str, Set[Slot]] = defaultdict(set)
        self.pool: Dict[str, List[str]] = defaultdict(list)
        self.teachers: List[str] = []
        self.lab_use: Dict[Slot, int] = defaultdict(int)
        self.lessons: List[Dict] = []  # {subject, classes, teachers, slots, fixed, lab}

    # -- primitives ---------------------------------------------------------
    def new_teacher(self, subject):
        t = f"t_{len(self.teachers) + 1:03d}"
        self.teachers.append(t)
        self.pool[subject].append(t)
        return t

    def all_slots(self):
        return [(d, p) for d in range(self.D) for p in range(2, self.P + 1)]

    def place(self, subject, cids, ppw, n_teachers=1, lab=None, prefer: Optional[List[str]] = None):
        """Choose teachers (busiest that still fit first) and slots free for everyone."""
        for _ in range(30):
            chosen: List[str] = []
            for _k in range(n_teachers):
                cands = [t for t in (prefer or []) + self.pool[subject]
                         if t not in chosen and len(self.tbusy[t]) + ppw <= self.cap]
                cands = list(dict.fromkeys(cands))
                self.rng.shuffle(cands)
                cands.sort(key=lambda t: -len(self.tbusy[t]))
                pick = None
                for t in cands[:12]:
                    if self._fits(chosen + [t], cids, ppw, lab):
                        pick = t
                        break
                chosen.append(pick or self.new_teacher(subject))
            slots = self._slots(chosen, cids, ppw, lab)
            if slots is not None:
                for t in chosen:
                    self.tbusy[t] |= set(slots)
                    if t not in self.pool[subject]:
                        self.pool[subject].append(t)  # a class teacher becomes a subject teacher too
                for c in cids:
                    self.cgrid[c] |= set(slots)
                if lab:
                    for s in slots:
                        self.lab_use[s] += 1
                self.lessons.append({"subject": subject, "classes": cids, "teachers": chosen, "slots": slots,
                                     "fixed": None, "lab": lab})
                return
        raise _Retry

    def _fits(self, ts, cids, ppw, lab):
        return self._slots(ts, cids, ppw, lab, dry=True) is not None

    def _slots(self, ts, cids, ppw, lab, dry=False):
        free = [s for s in self.all_slots()
                if all(s not in self.cgrid[c] for c in cids) and all(s not in self.tbusy[t] for t in ts)]
        if lab:
            fs = set(free)
            opts = [(d, p) for (d, p) in free if (d, p + 1) in fs
                    and self.lab_use[d, p] < self.n_labs and self.lab_use[d, p + 1] < self.n_labs]
            if not opts:
                return None
            d, p = self.rng.choice(opts)
            return [(d, p), (d, p + 1)]
        by_day = defaultdict(list)
        for s in free:
            by_day[s[0]].append(s)
        for v in by_day.values():
            self.rng.shuffle(v)
        chosen, per_day = [], defaultdict(int)
        for cap in (1, 2):
            order = sorted(by_day, key=lambda d: (-len(by_day[d]), self.rng.random()))
            for d in order:
                while len(chosen) < ppw and per_day[d] < cap and by_day[d]:
                    chosen.append(by_day[d].pop())
                    per_day[d] += 1
        return chosen if len(chosen) == ppw else None

    # -- generation ---------------------------------------------------------
    def run(self):
        ct_of = {}
        for c in self.classes:
            t = self.new_teacher("CLASS TEACHER")
            ct_of[c["id"]] = t
            slots = [(d, 1) for d in range(self.D)]
            self.tbusy[t] |= set(slots)
            self.cgrid[c["id"]] |= set(slots)
            self.lessons.append({"subject": "CLASS TEACHER", "classes": [c["id"]], "teachers": [t],
                                 "slots": slots, "fixed": 1, "lab": None})

        # most constrained first: joint electives, practicals, then big subjects
        for g in range(self.senior_from, max(c["grade"] for c in self.classes) + 1):
            ids = [c["id"] for c in self.classes if c["grade"] == g]
            for i in range(0, len(ids) - 1, 2):
                self.place(ELECTIVE[0], ids[i:i + 2], ELECTIVE[1], n_teachers=2)
        for c in self.classes:
            if c["grade"] >= self.senior_from:
                self.place(PRACTICAL[0], [c["id"]], PRACTICAL[1], lab="LAB")
        work = []
        cls_grade = {c["id"]: c["grade"] for c in self.classes}
        for c in self.classes:
            for name, ppw in (JUNIOR if c["grade"] < self.senior_from else SENIOR):
                work.append((ppw, self.rng.random(), name, c["id"]))
        for ppw, _r, name, cid in sorted(work, reverse=True):
            junior = cls_grade[cid] < self.senior_from
            ct = ct_of[cid]
            if junior:
                prefer = [ct] if name in CT_JUNIOR and self.rng.random() < 0.9 else []
            else:  # a senior class teacher usually teaches one main subject of their class
                prefer = [ct] if name in CT_SENIOR and len(self.tbusy[ct]) <= self.D else []
            self.place(name, [cid], ppw, prefer=prefer)
        for c in self.classes:
            if len(self.cgrid[c["id"]]) != self.D * self.P:
                raise _Retry
        return self.to_school(ct_of)

    def to_school(self, ct_of):
        subjects, lessons, teachers, planted = {}, [], {}, []
        tnum = {t: i + 1 for i, t in enumerate(self.teachers)}
        for t in self.teachers:
            teachers[t] = {"id": t, "name": f"TEACHER {tnum[t]:03d}", "qualified_subjects": [],
                           "max_periods_per_day": self.P, "unavailable_slots": []}
        cls = {c["id"]: c for c in self.classes}
        lab_rooms = defaultdict(int)
        for les in self.lessons:
            sid = "sub_" + "".join(ch for ch in les["subject"] if ch.isalnum())
            subjects.setdefault(sid, {"id": sid, "name": les["subject"], "periods_per_week": 0,
                                      "requires_lab": bool(les["lab"])})
            lid = f"L{len(lessons) + 1:04d}"
            lessons.append({"id": lid, "subject_id": sid, "class_ids": les["classes"],
                            "teacher_ids": sorted(set(les["teachers"])), "periods_per_week": len(les["slots"]),
                            "max_per_day": 2, "fixed_period": les["fixed"], "lab": les["lab"]})
            for t in les["teachers"]:
                if sid not in teachers[t]["qualified_subjects"]:
                    teachers[t]["qualified_subjects"].append(sid)
            for c in les["classes"]:
                if sid not in cls[c]["subject_ids"]:
                    cls[c]["subject_ids"].append(sid)
            for (d, p) in les["slots"]:
                room = None
                if les["lab"]:
                    lab_rooms[d, p] += 1
                    room = f"r_LAB_{lab_rooms[d, p]}"
                for c in les["classes"]:
                    for t in sorted(set(les["teachers"])):
                        planted.append({"lesson_id": lid, "teacher_id": t, "subject_id": sid, "class_id": c,
                                        "room_id": room or f"r_{c}", "day": self.days[d], "period": p})
        for c in self.classes:
            c["class_teacher_id"] = ct_of[c["id"]]
        rooms = [{"id": f"r_{c['id']}", "name": f"Room {c['name']}", "capacity": 40, "is_lab": False}
                 for c in self.classes]
        rooms += [{"id": f"r_LAB_{i}", "name": f"Lab {i}", "capacity": 40, "is_lab": True, "lab_type": "LAB"}
                  for i in range(1, self.n_labs + 1)]
        return {"version": 2, "days": list(self.days), "periods_per_day": self.P,
                "subjects": list(subjects.values()), "teachers": list(teachers.values()),
                "classes": self.classes, "rooms": rooms, "lessons": lessons, "labs": {"LAB": self.n_labs},
                "planted_solution": planted}
