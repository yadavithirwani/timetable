"""Lesson-based CP-SAT timetable model.

Decision variable: y[lesson, day, period] = 1 if the lesson is taught then.
Because a lesson already fixes *who* teaches *whom*, the model is small
(~ lessons x 48 booleans) and every clash rule is a simple sum over a slot.

Every hard rule belongs to a named *group*. When the model is infeasible we
re-solve with one assumption literal per group and ask CP-SAT for a small
subset of groups that cannot hold together. That subset is turned into a
plain-English explanation ("ANITA SHARMA can only be in one place at a time",
"CS/BIO/ECO for 11 A + 11 D needs 7 periods") instead of "INFEASIBLE".
"""
from __future__ import annotations

import os
import threading
import time
from collections import defaultdict
from typing import Any, Dict, List, Optional, Tuple

from ortools.sat.python import cp_model

from backend.optimizer.lessons import normalise


class _Builder:
    def __init__(self, school: Dict[str, Any], rules: Dict[str, Any], locked: Optional[List[Dict]],
                 explain: bool, forbidden: Optional[List[Dict]] = None,
                 baseline: Optional[Dict[str, List]] = None, stability_weight: int = 100):
        self.s = school
        self.rules = rules
        self.locked = locked or []
        self.forbidden = forbidden or []
        self.baseline = baseline or {}
        self.stability_weight = stability_weight
        self.explain = explain
        self.m = cp_model.CpModel()
        self.groups: Dict[str, cp_model.IntVar] = {}
        self.days = school["days"]
        self.P = school["periods_per_day"]
        self.D = range(len(self.days))
        self.Ps = range(1, self.P + 1)
        self.lessons = school["lessons"]
        self.subj = {x["id"]: x for x in school["subjects"]}
        self.tname = {t["id"]: t["name"] for t in school["teachers"]}
        self.cname = {c["id"]: c["name"] for c in school["classes"]}
        self.y: Dict[Tuple[str, int, int], cp_model.IntVar] = {}
        self.penalties: List[Tuple[int, Any, str]] = []

    # -- helpers -----------------------------------------------------------
    def label(self, l) -> str:
        cls = " + ".join(self.cname.get(c, c) for c in l["class_ids"])
        return f"{self.subj[l['subject_id']]['name']} for {cls}"

    def add(self, ct, group: str):
        """Attach a constraint to a named group (only enforced via literal in explain mode)."""
        if self.explain:
            if group not in self.groups:
                self.groups[group] = self.m.NewBoolVar(f"g{len(self.groups)}")
            ct.OnlyEnforceIf(self.groups[group])
        return ct

    # -- model -------------------------------------------------------------
    def build(self):
        m, y = self.m, self.y
        cr = self.rules.get("class_rules", {})
        tr = self.rules.get("teacher_rules", {})
        w = self.rules.get("weights", {})

        for l in self.lessons:
            for d in self.D:
                for p in self.Ps:
                    y[l["id"], d, p] = m.NewBoolVar(f"y_{l['id']}_{d}_{p}")

        by_class = defaultdict(list)
        by_teacher = defaultdict(list)
        by_lab = defaultdict(list)
        for l in self.lessons:
            for c in l["class_ids"]:
                by_class[c].append(l)
            for t in set(l["teacher_ids"]):
                by_teacher[t].append(l)
            if l.get("lab"):
                by_lab[l["lab"]].append(l)

        double_labs = cr.get("lab_practicals_as_double_period", False)

        # 1. weekly quota + fixed period (class teacher in period 1) + per-day cap
        for l in self.lessons:
            lab_double = double_labs and l.get("lab") and l["periods_per_week"] % 2 == 0
            self.add(m.Add(sum(y[l["id"], d, p] for d in self.D for p in self.Ps) == l["periods_per_week"]),
                     f"{self.label(l)} needs {l['periods_per_week']} periods a week")
            if l.get("fixed_period"):
                self.add(m.Add(sum(y[l["id"], d, p] for d in self.D for p in self.Ps
                                   if p != l["fixed_period"]) == 0),
                         f"{self.label(l)} must be in period {l['fixed_period']}")
            cap = 2 if lab_double else l.get("max_per_day", 2)
            for d in self.D:
                daily = sum(y[l["id"], d, p] for p in self.Ps)
                self.add(m.Add(daily <= cap), f"{self.label(l)} at most {cap} periods a day")
                if lab_double:
                    # practicals come as back-to-back pairs
                    starts = [m.NewBoolVar("") for _ in range(self.P - 1)]
                    for p in self.Ps:
                        covering = [starts[i] for i in (p - 2, p - 1) if 0 <= i < self.P - 1]
                        self.add(m.Add(y[l["id"], d, p] == sum(covering)),
                                 f"{self.label(l)} practicals are double periods")
                    m.Add(sum(starts) <= 1)
                elif w.get("same_subject_twice_a_day", 0) and cap > 1:
                    dbl = m.NewBoolVar("")
                    m.Add(daily <= 1 + dbl)
                    self.penalties.append((w["same_subject_twice_a_day"], dbl, "same subject twice a day"))

        # 2. a class has at most one lesson per slot
        for c, ls in by_class.items():
            for d in self.D:
                for p in self.Ps:
                    self.add(m.Add(sum(y[l["id"], d, p] for l in ls) <= 1),
                             f"Class {self.cname.get(c, c)} can only have one lesson at a time")

        # 3. a teacher is in one place at a time (optionally PE/library may club classes)
        club = tr.get("clubbed_classes", {})
        clubbable = set(club.get("allowed_subjects", [])) if club.get("enabled") else set()
        k = club.get("max_concurrent", 2)
        max_day = tr.get("max_periods_per_day", self.P)
        soft_max = tr.get("soft_max_periods_per_day")
        unavail = {t["id"]: t.get("unavailable_slots", []) for t in self.s["teachers"]}
        for t, ls in by_teacher.items():
            tn = self.tname.get(t, t)
            for d in self.D:
                for p in self.Ps:
                    std = [y[l["id"], d, p] for l in ls if self.subj[l["subject_id"]]["name"] not in clubbable]
                    clb = [y[l["id"], d, p] for l in ls if self.subj[l["subject_id"]]["name"] in clubbable]
                    if clb:
                        self.add(m.Add(k * sum(std) + sum(clb) <= k), f"{tn} can only be in one place at a time")
                    else:
                        self.add(m.Add(sum(std) <= 1), f"{tn} can only be in one place at a time")
                load = sum(y[l["id"], d, p] for l in ls for p in self.Ps)
                if max_day < self.P:
                    self.add(m.Add(load <= max_day), f"{tn} teaches at most {max_day} periods a day")
                if soft_max and w.get("teacher_over_soft_daily_max"):
                    over = m.NewIntVar(0, self.P, "")
                    m.Add(over >= load - soft_max)
                    self.penalties.append((w["teacher_over_soft_daily_max"], over, "teacher overloaded day"))
                run = tr.get("max_consecutive_periods")
                if run and run < self.P and w.get("teacher_long_run"):
                    for start in range(1, self.P - run + 1):
                        window = [y[l["id"], d, p] for l in ls for p in range(start, start + run + 1)]
                        too_long = m.NewBoolVar("")
                        m.Add(sum(window) <= run + too_long)
                        self.penalties.append((w["teacher_long_run"], too_long, "teacher long run"))
            for u in unavail.get(t, []):
                if u["day"] in self.days:
                    d = self.days.index(u["day"])
                    self.add(m.Add(sum(y[l["id"], d, u["period"]] for l in ls) == 0),
                             f"{tn} is unavailable on {u['day']} period {u['period']}")

        # 4. lab capacity
        for lab, ls in by_lab.items():
            cap = self.s.get("labs", {}).get(lab, 1)
            for d in self.D:
                for p in self.Ps:
                    self.add(m.Add(sum(y[l["id"], d, p] for l in ls) <= cap), f"Only {cap} {lab}(s) available")

        # 5. user / AI requests: locks ("X must be here") and forbidden slots ("X must not be here")
        by_id = {l["id"]: l for l in self.lessons}

        def matching(req):
            if req.get("lesson_id") in by_id:
                return [by_id[req["lesson_id"]]]
            same = [l for l in self.lessons
                    if req.get("class_id") in l["class_ids"] and l["subject_id"] == req.get("subject_id")]
            # prefer the named teacher's lesson, but don't silently drop the request if the
            # caller (e.g. the AI assistant) guessed the wrong teacher
            return [l for l in same if req.get("teacher_id") in l["teacher_ids"]] or same

        for lk in self.locked:
            if lk.get("day") not in self.days or not (1 <= lk.get("period", 0) <= self.P):
                continue
            d = self.days.index(lk["day"])
            cands = matching(lk)
            if not cands:
                continue
            self.add(m.Add(sum(y[l["id"], d, lk["period"]] for l in cands) == 1),
                     f"Requested: {self.label(cands[0])} on {lk['day']} period {lk['period']}")
        for fb in self.forbidden:
            if fb.get("day") not in self.days or not (1 <= fb.get("period", 0) <= self.P):
                continue
            d = self.days.index(fb["day"])
            for l in matching(fb) if (fb.get("lesson_id") or fb.get("subject_id")) else \
                    [l for l in self.lessons if fb.get("class_id") in l["class_ids"]]:
                self.add(m.Add(y[l["id"], d, fb["period"]] == 0),
                         f"Requested: no {self.subj[l['subject_id']]['name']} for "
                         f"{' + '.join(self.cname.get(c, c) for c in l['class_ids'])} on {fb['day']} period {fb['period']}")

        # 5b. stay close to an existing timetable (used when editing): every period that
        # moves costs `stability_weight`, and the old timetable is the search's starting point
        if self.baseline:
            for l in self.lessons:
                was = {(self.days.index(dn), p) for dn, p in self.baseline.get(l["id"], []) if dn in self.days}
                for d in self.D:
                    for p in self.Ps:
                        m.AddHint(y[l["id"], d, p], (d, p) in was)
                        if (d, p) in was and not self.explain:
                            self.penalties.append((self.stability_weight, y[l["id"], d, p].Not(), "moved"))

        # 6. soft: heavy subjects not in the last periods
        heavy = set(cr.get("heavy_subjects", []))
        late = cr.get("heavy_subjects_not_after_period")
        if heavy and late and w.get("heavy_subject_late"):
            for l in self.lessons:
                if self.subj[l["subject_id"]]["name"] in heavy:
                    for d in self.D:
                        for p in self.Ps:
                            if p > late:
                                self.penalties.append((w["heavy_subject_late"], y[l["id"], d, p], "heavy subject late"))

        if self.penalties and not self.explain:
            m.Minimize(sum(wt * v for wt, v, _ in self.penalties))
        return self


class _Progress(cp_model.CpSolverSolutionCallback):
    """Records (seconds since model build started, objective) for every improving solution."""

    def __init__(self, t0: float, on_progress=None, should_stop=None):
        super().__init__()
        self.t0 = t0
        self.trace: List[Tuple[float, float]] = []
        self.on_progress = on_progress
        self.should_stop = should_stop
        self.stopped = False

    def on_solution_callback(self):
        point = (round(time.time() - self.t0, 2), self.ObjectiveValue())
        self.trace.append(point)
        if self.on_progress:
            self.on_progress(*point)
        if self.should_stop and self.should_stop():
            self.stopped = True
            self.StopSearch()


def _explain(school, rules, locked, time_limit: float, forbidden=None) -> List[str]:
    b = _Builder(school, rules, locked, explain=True, forbidden=forbidden).build()
    lits = list(b.groups.values())
    b.m.AddAssumptions(lits)
    solver = cp_model.CpSolver()
    solver.parameters.max_time_in_seconds = time_limit
    solver.parameters.num_workers = 1
    status = solver.Solve(b.m)
    if status != cp_model.INFEASIBLE:
        return []
    idx_to_name = {v.Index(): k for k, v in b.groups.items()}
    core = [idx_to_name[i] for i in solver.SufficientAssumptionsForInfeasibility() if i in idx_to_name]
    return sorted(set(core))


def solve(school_in: Dict[str, Any], rules: Dict[str, Any], locked: Optional[List[Dict]] = None,
          time_limit: Optional[float] = None, workers: Optional[int] = None, seed: Optional[int] = None,
          explain_on_failure: bool = True, on_progress=None, should_stop=None,
          forbidden: Optional[List[Dict]] = None, baseline: Optional[Dict[str, List]] = None,
          stability_weight: int = 100) -> Dict[str, Any]:
    """on_progress(seconds, objective) is called for every improving solution;
    should_stop() is polled on each solution and ends the search early, keeping the best found."""
    school = normalise(school_in, rules)
    cfg = rules.get("solver", {})
    time_limit = time_limit or cfg.get("time_limit_seconds", 60)

    t0 = time.time()
    b = _Builder(school, rules, locked, explain=False, forbidden=forbidden, baseline=baseline,
                 stability_weight=stability_weight).build()
    solver = cp_model.CpSolver()
    solver.parameters.max_time_in_seconds = time_limit
    # TIMETABLE_SOLVER_WORKERS caps workers on small servers (each worker costs ~100-150 MB on a
    # big school; 1 worker is far too slow to find a first solution, so never go below 2).
    env_workers = int(os.environ.get("TIMETABLE_SOLVER_WORKERS", "0") or 0)
    n = workers or env_workers or cfg.get("workers", 8) or (os.cpu_count() or 2)
    solver.parameters.num_workers = max(2, n)
    solver.parameters.random_seed = cfg.get("random_seed", 0) if seed is None else seed
    progress = _Progress(t0, on_progress, should_stop)
    done = threading.Event()
    if should_stop:
        def _watch():
            while not done.wait(0.5):
                if should_stop():
                    progress.stopped = True
                    solver.StopSearch()
                    return
        threading.Thread(target=_watch, daemon=True).start()
    try:
        status = solver.Solve(b.m, progress)
    finally:
        done.set()
    duration = time.time() - t0

    meta = {
        "solve_duration_seconds": round(duration, 3),
        "first_solution_seconds": progress.trace[0][0] if progress.trace else None,
        "objective_trace": progress.trace,
        "stopped_early": progress.stopped,
        "lessons": len(school["lessons"]),
        "variables": len(b.m.Proto().variables),
        "constraints": len(b.m.Proto().constraints),
    }
    status_name = solver.StatusName(status)
    if status not in (cp_model.OPTIMAL, cp_model.FEASIBLE):
        explanation = []
        if explain_on_failure and status == cp_model.INFEASIBLE:
            explanation = _explain(school, rules, locked, time_limit, forbidden)
        return {"success": False, "status": status_name, "assignments": [], "score": 0.0,
                "violations": ["No timetable satisfies all the rules."] + explanation,
                "explanation": explanation, "metadata": meta}

    placed = defaultdict(list)  # (d, p) -> [lesson]
    for l in school["lessons"]:
        for d in b.D:
            for p in b.Ps:
                if solver.Value(b.y[l["id"], d, p]):
                    placed[d, p].append(l)

    breakdown = defaultdict(int)
    for wt, v, name in b.penalties:
        breakdown[name] += wt * solver.Value(v)
    meta.update({
        "objective": solver.ObjectiveValue() if b.penalties else 0.0,
        "best_bound": solver.BestObjectiveBound() if b.penalties else 0.0,
        "penalty_breakdown": dict(breakdown),
    })
    return {"success": True, "status": status_name,
            "assignments": expand_assignments(school, placed),
            "score": meta["objective"], "violations": [], "metadata": meta}


def expand_assignments(school: Dict[str, Any], placed: Dict[Tuple[int, int], List[Dict]]) -> List[Dict]:
    """Turn placed lessons into one row per (class, teacher, slot) and allocate rooms."""
    days = school["days"]
    rooms = school.get("rooms", [])
    homeroom = {}
    for c in school["classes"]:
        for cand in (f"r_{c['id']}", c["id"].replace("c_", "r_", 1)):
            if any(r["id"] == cand for r in rooms):
                homeroom[c["id"]] = cand
                break
        else:
            general = [r["id"] for r in rooms if not r.get("is_lab")]
            homeroom[c["id"]] = general[0] if general else ""
    labs_by_type = defaultdict(list)
    for r in rooms:
        if r.get("is_lab"):
            labs_by_type[r.get("lab_type", "LAB")].append(r["id"])

    out = []
    for (d, p), ls in sorted(placed.items()):
        lab_free = {k: list(v) for k, v in labs_by_type.items()}
        for l in ls:
            room = None
            if l.get("lab") and lab_free.get(l["lab"]):
                room = lab_free[l["lab"]].pop(0)
            for c in l["class_ids"]:
                for t in l["teacher_ids"] or [""]:
                    out.append({"lesson_id": l["id"], "teacher_id": t, "subject_id": l["subject_id"],
                                "class_id": c, "room_id": room or homeroom.get(c, ""),
                                "day": days[d], "period": p})

    # self-contained classes (e.g. Nursery): class teacher every period
    ct_sub = next((s["id"] for s in school["subjects"] if s["name"] == "CLASS TEACHER"), "sub_CLASSTEACHER")
    for c in school["classes"]:
        if c.get("self_contained") and c.get("class_teacher_id"):
            for d in days:
                for p in range(1, school["periods_per_day"] + 1):
                    out.append({"lesson_id": f"HOME_{c['id']}", "teacher_id": c["class_teacher_id"],
                                "subject_id": ct_sub, "class_id": c["id"], "room_id": homeroom.get(c["id"], ""),
                                "day": d, "period": p})
    return out
