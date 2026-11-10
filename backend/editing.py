"""Small, safe edits to an existing timetable.

The chat assistant (and anything else) never writes a timetable directly. It
describes *what* should change as a list of requests:

    {"type": "move",  "class": "9 A", "subject": "MATHS", "to_day": "Thu", "to_period": 5,
                      "from_day": "Mon", "from_period": 2}            # from_* optional
    {"type": "swap",  "class": "9 A", "day1": "Mon", "period1": 2, "day2": "Tue", "period2": 4}
    {"type": "avoid", "class": "9 A", "subject": "PHY EDU", "day": "Fri", "period": 8}  # subject optional
    {"type": "teacher_unavailable", "teacher": "R SHARMA", "day": "Wed", "periods": [1, 2]}  # periods optional = all day

and :func:`propose` works out the smallest set of moves that satisfies them while
every school rule still holds. The solver is warm-started from the current
timetable and pays a large cost for every period that moves, so a swap in one
class doesn't reshuffle the whole school. The result is re-checked by the
independent verifier and returned with a readable diff; it only replaces the
real timetable when the user presses Apply.
"""
from __future__ import annotations

import copy
import difflib
import re
from collections import defaultdict
from typing import Any, Dict, List, Optional, Tuple

from backend.optimizer.engine import generate_timetable

DAY_ALIASES = {
    "mon": "Mon", "monday": "Mon", "tue": "Tue", "tues": "Tue", "tuesday": "Tue",
    "wed": "Wed", "weds": "Wed", "wednesday": "Wed", "thu": "Thu", "thur": "Thu", "thurs": "Thu",
    "thursday": "Thu", "fri": "Fri", "friday": "Fri", "sat": "Sat", "saturday": "Sat",
    "sun": "Sun", "sunday": "Sun",
}


class EditError(ValueError):
    """A request that can't be understood (unknown class, empty slot, ...)."""


# --------------------------------------------------------------------------- name resolution
def _key(s: str) -> str:
    return re.sub(r"[^a-z0-9]", "", str(s).lower())


def _resolve(kind: str, query: Any, items: List[Dict]) -> Dict:
    if query is None or str(query).strip() == "":
        raise EditError(f"No {kind} given.")
    q = _key(query)
    exact = [x for x in items if _key(x["name"]) == q or _key(x["id"]) == q]
    if len(exact) == 1:
        return exact[0]
    # 'class 9a', 'grade 9 a', 'ms sharma'
    q2 = re.sub(r"^(class|grade|std|section|mr|mrs|ms|miss|dr|sir|maam)", "", q)
    exact = [x for x in items if _key(x["name"]) == q2]
    if len(exact) == 1:
        return exact[0]
    part = [x for x in items if q2 and (q2 in _key(x["name"]) or _key(x["name"]) in q2)]
    if len(part) == 1:
        return part[0]
    names = [x["name"] for x in items]
    if len(part) > 1:
        raise EditError(f"'{query}' could be several {kind}s: {', '.join(sorted(x['name'] for x in part)[:8])}.")
    close = difflib.get_close_matches(str(query).upper(), names, n=1, cutoff=0.75)
    if close:
        return next(x for x in items if x["name"] == close[0])
    hint = difflib.get_close_matches(str(query).upper(), names, n=3, cutoff=0.4)
    raise EditError(f"I don't know a {kind} called '{query}'." + (f" Did you mean {', '.join(hint)}?" if hint else ""))


def resolve_class(school, q):
    return _resolve("class", q, school["classes"])


def resolve_teacher(school, q):
    return _resolve("teacher", q, school["teachers"])


def resolve_subject(school, q):
    return _resolve("subject", q, school["subjects"])


def resolve_day(school, q) -> str:
    days = school["days"]
    if q in days:
        return q
    k = _key(q)
    short = DAY_ALIASES.get(k, k[:3]).lower()
    for day in days:
        if _key(day) == k or (len(k) >= 2 and _key(day).startswith(short)):
            return day
    raise EditError(f"'{q}' isn't a school day ({', '.join(days)}).")


def resolve_period(school, q) -> int:
    try:
        p = int(re.sub(r"[^0-9]", "", str(q)))
    except ValueError:
        raise EditError(f"'{q}' isn't a period number.")
    if not 1 <= p <= school["periods_per_day"]:
        raise EditError(f"Period {p} doesn't exist (periods are 1-{school['periods_per_day']}).")
    return p


# --------------------------------------------------------------------------- timetable helpers
def baseline_of(assignments: List[Dict]) -> Dict[str, List[Tuple[str, int]]]:
    out: Dict[str, set] = defaultdict(set)
    for a in assignments:
        if not a["lesson_id"].startswith("HOME_"):
            out[a["lesson_id"]].add((a["day"], a["period"]))
    return {k: sorted(v) for k, v in out.items()}


def lesson_at(school, assignments, class_id: str, day: str, period: int) -> Optional[Dict]:
    lessons = {l["id"]: l for l in school["lessons"]}
    for a in assignments:
        if a["class_id"] == class_id and a["day"] == day and a["period"] == period:
            return lessons.get(a["lesson_id"])
    return None


# --------------------------------------------------------------------------- requests -> constraints
def compile_requests(school: Dict, assignments: List[Dict], requests: List[Dict]):
    """Turn user requests into compiled edits: [{description, locked, forbidden, unavailable}]."""
    s = copy.deepcopy(school)
    locked: List[Dict] = []
    forbidden: List[Dict] = []
    unavailable: List[Dict] = []
    described: List[str] = []
    compiled: List[Dict] = []          # per request: what was added; stored when the change is applied
    subj = {x["id"]: x["name"] for x in s["subjects"]}

    def lock(lesson, cid, day, period):
        locked.append({"lesson_id": lesson["id"], "class_id": cid, "subject_id": lesson["subject_id"],
                       "teacher_id": (lesson["teacher_ids"] or [""])[0], "day": day, "period": period})

    for r in requests:
        n_lock, n_forb, n_unav = len(locked), len(forbidden), len(unavailable)
        kind = r.get("type")
        if kind == "move":
            c = resolve_class(s, r.get("class"))
            sb = resolve_subject(s, r.get("subject"))
            cands = [l for l in s["lessons"] if c["id"] in l["class_ids"] and l["subject_id"] == sb["id"]]
            if not cands:
                raise EditError(f"{c['name']} doesn't have {sb['name']}.")
            if r.get("teacher"):
                t = resolve_teacher(s, r["teacher"])
                cands = [l for l in cands if t["id"] in l["teacher_ids"]] or cands
            day, period = resolve_day(s, r.get("to_day")), resolve_period(s, r.get("to_period"))
            fd = r.get("from_day")
            if fd:
                fd, fp = resolve_day(s, fd), resolve_period(s, r.get("from_period"))
                at = lesson_at(s, assignments, c["id"], fd, fp)
                if not at or at["subject_id"] != sb["id"]:
                    have = subj[at["subject_id"]] if at else "nothing"
                    raise EditError(f"{c['name']} has {have} on {fd} period {fp}, not {sb['name']}.")
                cands = [at]
                forbidden.append({"lesson_id": at["id"], "class_id": c["id"], "subject_id": sb["id"],
                                  "day": fd, "period": fp})
            lock(cands[0], c["id"], day, period)
            src = f" from {fd} P{fp}" if fd else ""
            described.append(f"Move {sb['name']} for {c['name']}{src} to {day} P{period}")
        elif kind == "swap":
            c = resolve_class(s, r.get("class"))
            d1, p1 = resolve_day(s, r.get("day1")), resolve_period(s, r.get("period1"))
            d2, p2 = resolve_day(s, r.get("day2")), resolve_period(s, r.get("period2"))
            a, b = lesson_at(s, assignments, c["id"], d1, p1), lesson_at(s, assignments, c["id"], d2, p2)
            if not a or not b:
                raise EditError(f"{c['name']} has a free period at {d1} P{p1 if not a else p2}; use a move instead.")
            if a["id"] == b["id"]:
                raise EditError(f"Both periods are already {subj[a['subject_id']]} for {c['name']}.")
            lock(a, c["id"], d2, p2)
            lock(b, c["id"], d1, p1)
            described.append(f"Swap {c['name']}'s {d1} P{p1} ({subj[a['subject_id']]}) with "
                             f"{d2} P{p2} ({subj[b['subject_id']]})")
        elif kind == "avoid":
            c = resolve_class(s, r.get("class"))
            day, period = resolve_day(s, r.get("day")), resolve_period(s, r.get("period"))
            fb = {"class_id": c["id"], "day": day, "period": period}
            what = "anything moved"
            if r.get("subject"):
                sb = resolve_subject(s, r["subject"])
                if not any(c["id"] in l["class_ids"] and l["subject_id"] == sb["id"] for l in s["lessons"]):
                    raise EditError(f"{c['name']} doesn't have {sb['name']}.")
                fb["subject_id"] = sb["id"]
                what = sb["name"]
            else:
                at = lesson_at(s, assignments, c["id"], day, period)
                if at:
                    fb["subject_id"], what = at["subject_id"], subj[at["subject_id"]]
            forbidden.append(fb)
            described.append(f"No {what} for {c['name']} on {day} P{period}")
        elif kind == "teacher_unavailable":
            t = resolve_teacher(s, r.get("teacher"))
            day = resolve_day(s, r.get("day"))
            periods = r.get("periods") or list(range(1, s["periods_per_day"] + 1))
            periods = sorted({resolve_period(s, p) for p in periods})
            unavailable += [{"teacher_id": t["id"], "day": day, "period": p} for p in periods]
            when = "all day" if len(periods) == s["periods_per_day"] else "P" + ", ".join(map(str, periods))
            described.append(f"{t['name']} unavailable on {day} {when}")
        else:
            raise EditError(f"Unknown change type '{kind}'. Use move, swap, avoid or teacher_unavailable.")
        compiled.append({"description": described[-1], "locked": locked[n_lock:],
                         "forbidden": forbidden[n_forb:], "unavailable": unavailable[n_unav:]})
    return compiled


def constraints_of(school: Dict, compiled: List[Dict]):
    """(school copy with teacher unavailability, locks, forbidden) for a list of compiled edits.

    Entries that no longer match the school (e.g. after a new Excel upload) are skipped and reported.
    """
    s = copy.deepcopy(school)
    lessons = {l["id"]: l for l in s["lessons"]}
    teachers = {t["id"]: t for t in s["teachers"]}
    locked, forbidden, skipped = [], [], []
    for e in compiled:
        refs = [x for x in e.get("locked", []) + e.get("forbidden", []) if x.get("lesson_id")]
        if any(x["lesson_id"] not in lessons or x.get("subject_id") != lessons[x["lesson_id"]]["subject_id"]
               or x.get("class_id") not in lessons[x["lesson_id"]]["class_ids"] for x in refs) \
                or any(u["teacher_id"] not in teachers or u["day"] not in s["days"] for u in e.get("unavailable", [])):
            skipped.append(e.get("description", "?"))
            continue
        locked += e.get("locked", [])
        forbidden += e.get("forbidden", [])
        for u in e.get("unavailable", []):
            slots = teachers[u["teacher_id"]].setdefault("unavailable_slots", [])
            if {"day": u["day"], "period": u["period"]} not in slots:
                slots.append({"day": u["day"], "period": u["period"]})
    return s, locked, forbidden, skipped


# --------------------------------------------------------------------------- diff
def diff(school: Dict, old: List[Dict], new: List[Dict]) -> Dict[str, Any]:
    subj = {x["id"]: x["name"] for x in school["subjects"]}
    cname = {c["id"]: c["name"] for c in school["classes"]}
    tname = {t["id"]: t["name"] for t in school["teachers"]}
    lessons = {l["id"]: l for l in school["lessons"]}
    days = school["days"]
    bo, bn = baseline_of(old), baseline_of(new)
    changes = []
    for lid in sorted(set(bo) | set(bn)):
        was, now = set(bo.get(lid, [])), set(bn.get(lid, []))
        gone = sorted(was - now, key=lambda x: (days.index(x[0]), x[1]))
        came = sorted(now - was, key=lambda x: (days.index(x[0]), x[1]))
        if not gone and not came:
            continue
        l = lessons[lid]
        for (fd, fp), (td, tp) in zip(gone, came):
            changes.append({
                "lesson_id": lid, "subject": subj[l["subject_id"]],
                "classes": [cname.get(c, c) for c in l["class_ids"]],
                "teachers": [tname.get(t, t) for t in l["teacher_ids"]],
                "from": {"day": fd, "period": fp}, "to": {"day": td, "period": tp},
                "text": f"{' + '.join(cname.get(c, c) for c in l['class_ids'])} · {subj[l['subject_id']]}: "
                        f"{fd} P{fp} → {td} P{tp}",
            })
    changes.sort(key=lambda c: (c["classes"][0], days.index(c["from"]["day"]), c["from"]["period"]))
    return {
        "moved_periods": len(changes),
        "classes": sorted({c for ch in changes for c in ch["classes"]}),
        "teachers": sorted({t for ch in changes for t in ch["teachers"]}),
        "changes": changes,
    }


# --------------------------------------------------------------------------- main entry
def propose(school: Dict, rules: Dict, timetable: Dict, requests: List[Dict],
            standing: Optional[List[Dict]] = None, time_limit: float = 20,
            stability_weight: int = 100) -> Dict[str, Any]:
    """Re-solve with the requests (plus earlier applied ones) while moving as little as possible."""
    if not timetable or not timetable.get("success"):
        return {"ok": False, "message": "There is no timetable yet. Generate one first."}
    if not requests:
        return {"ok": False, "message": "No changes were requested."}
    old = timetable["assignments"]
    try:
        compiled = compile_requests(school, old, requests)
    except EditError as e:
        return {"ok": False, "message": str(e)}
    described = [c["description"] for c in compiled]
    s, locked, forbidden, _ = constraints_of(school, list(standing or []) + compiled)

    rules = copy.deepcopy(rules)
    res = generate_timetable(s, locked_assignments=locked, rules=rules, time_limit=time_limit,
                             forbidden=forbidden, baseline=baseline_of(old), stability_weight=stability_weight)
    if not res["success"]:
        why = res.get("explanation") or res.get("violations") or []
        return {"ok": False, "requests": described, "status": res["status"],
                "message": "That change can't be made without breaking a school rule.",
                "reasons": why[:8]}
    d = diff(school, old, res["assignments"])
    if d["moved_periods"] == 0:
        return {"ok": True, "requests": described, "compiled": compiled, "diff": d, "timetable": res,
                "message": "The timetable already satisfies that; nothing needs to move."}
    return {"ok": True, "requests": described, "compiled": compiled, "diff": d, "timetable": res,
            "message": f"{d['moved_periods']} period(s) move, in {len(d['classes'])} class(es)."}


# --------------------------------------------------------------------------- read-only queries
def class_grid(school: Dict, assignments: List[Dict], class_id: str) -> Dict[str, Dict[int, str]]:
    subj = {x["id"]: x["name"] for x in school["subjects"]}
    tname = {t["id"]: t["name"] for t in school["teachers"]}
    cells: Dict[Tuple[str, int], List] = defaultdict(lambda: [None, []])
    for a in assignments:
        if a["class_id"] == class_id:
            c = cells[a["day"], a["period"]]
            c[0] = subj.get(a["subject_id"], a["subject_id"])
            if a["teacher_id"] and tname.get(a["teacher_id"]) not in c[1]:
                c[1].append(tname.get(a["teacher_id"], a["teacher_id"]))
    return {d: {p: (f"{cells[d, p][0]} ({', '.join(cells[d, p][1])})" if (d, p) in cells else "free")
                for p in range(1, school["periods_per_day"] + 1)} for d in school["days"]}


def teacher_grid(school: Dict, assignments: List[Dict], teacher_id: str) -> Dict[str, Dict[int, str]]:
    subj = {x["id"]: x["name"] for x in school["subjects"]}
    cname = {c["id"]: c["name"] for c in school["classes"]}
    cells: Dict[Tuple[str, int], List] = defaultdict(lambda: [None, []])
    for a in assignments:
        if a["teacher_id"] == teacher_id:
            c = cells[a["day"], a["period"]]
            c[0] = subj.get(a["subject_id"], a["subject_id"])
            if cname.get(a["class_id"]) not in c[1]:
                c[1].append(cname.get(a["class_id"], a["class_id"]))
    return {d: {p: (f"{cells[d, p][0]} ({', '.join(cells[d, p][1])})" if (d, p) in cells else "free")
                for p in range(1, school["periods_per_day"] + 1)} for d in school["days"]}


def free_teachers(school: Dict, assignments: List[Dict], day: str, period: int,
                  subject_id: Optional[str] = None) -> List[Dict]:
    """Teachers with no lesson at (day, period), busiest-that-day last."""
    busy = {a["teacher_id"] for a in assignments if a["day"] == day and a["period"] == period}
    day_load = defaultdict(set)
    for a in assignments:
        if a["day"] == day and a["teacher_id"]:
            day_load[a["teacher_id"]].add(a["period"])
    teaches = defaultdict(set)
    for l in school["lessons"]:
        for t in l["teacher_ids"]:
            teaches[t].add(l["subject_id"])
    subj = {x["id"]: x["name"] for x in school["subjects"]}
    out = []
    for t in school["teachers"]:
        if t["id"] in busy or not teaches.get(t["id"]):
            continue
        if any(u["day"] == day and u["period"] == period for u in t.get("unavailable_slots", [])):
            continue
        if subject_id and subject_id not in teaches[t["id"]]:
            continue
        out.append({"teacher": t["name"], "periods_that_day": len(day_load[t["id"]]),
                    "subjects": sorted(subj[x] for x in teaches[t["id"]])})
    out.sort(key=lambda x: (x["periods_that_day"], x["teacher"]))
    return out


def substitutes(school: Dict, assignments: List[Dict], teacher_id: str, day: str,
                periods: Optional[List[int]] = None, max_load: Optional[int] = None) -> List[Dict]:
    """Cover plan for a teacher who is away for a day (the timetable itself doesn't change).

    For each of their periods, suggest free teachers: those who already teach that class
    first, then those who teach the subject, then the least busy that day. A teacher is
    used for at most one cover per period, and covers are spread so nobody gets many.
    """
    subj = {x["id"]: x["name"] for x in school["subjects"]}
    cname = {c["id"]: c["name"] for c in school["classes"]}
    tname = {t["id"]: t["name"] for t in school["teachers"]}
    busy = defaultdict(set)
    for a in assignments:
        if a["day"] == day and a["teacher_id"]:
            busy[a["teacher_id"]].add(a["period"])
    teaches_class, teaches_subj = defaultdict(set), defaultdict(set)
    for l in school["lessons"]:
        for t in l["teacher_ids"]:
            teaches_subj[t].add(l["subject_id"])
            teaches_class[t].update(l["class_ids"])
    unav = {(t["id"], u["period"]) for t in school["teachers"] for u in t.get("unavailable_slots", []) if u["day"] == day}
    covers = defaultdict(int)
    slots: Dict[int, Dict] = {}
    for a in assignments:
        if a["teacher_id"] == teacher_id and a["day"] == day and (not periods or a["period"] in periods):
            e = slots.setdefault(a["period"], {"period": a["period"], "subject": subj.get(a["subject_id"]),
                                               "subject_id": a["subject_id"], "class_ids": []})
            if a["class_id"] not in e["class_ids"]:
                e["class_ids"].append(a["class_id"])
    out = []
    for p in sorted(slots):
        e = slots[p]
        cands = []
        for t in school["teachers"]:
            tid = t["id"]
            if tid == teacher_id or p in busy[tid] or (tid, p) in unav or not teaches_subj.get(tid):
                continue
            load = len(busy[tid]) + covers[tid]
            if max_load is not None and load >= max_load:
                continue
            knows = bool(teaches_class[tid] & set(e["class_ids"]))
            same = e["subject_id"] in teaches_subj[tid]
            cands.append(((not knows, not same, load, covers[tid], tname[tid]),
                          {"teacher": tname[tid], "periods_that_day": load,
                           "teaches_this_class": knows, "teaches_this_subject": same}))
        cands.sort(key=lambda x: x[0])
        if cands:
            covers[next(x for x in school["teachers"] if x["name"] == cands[0][1]["teacher"])["id"]] += 1
        out.append({"period": p, "subject": e["subject"], "classes": [cname.get(c, c) for c in e["class_ids"]],
                    "suggested": cands[0][1] if cands else None, "alternatives": [c[1] for c in cands[1:5]]})
    return out
