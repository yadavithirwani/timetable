"""File-backed workspaces: one per uploaded school.

    <DATA_DIR>/<workspace_id>/
        meta.json        name, original filename, timestamps
        source.xlsx      the uploaded Excel, untouched
        rows.json        contract rows read from the Excel
        quotas.json      period quotas (starts as a copy of config/period_quotas.json)
        rules.json       solver rules (starts as a copy of config/rules.json)
        school.json      lesson model built from rows + quotas
        timetable.json   current timetable (generated, then possibly edited)
        edits.json       applied edits (chat/swap requests) - honoured again on every regenerate
        history.json     earlier timetables, for Undo

DATA_DIR defaults to ./data_store and can be set with TIMETABLE_DATA_DIR (mount a
volume there when deploying so uploads survive restarts).
"""
from __future__ import annotations

import copy
import json
import os
import re
import shutil
import tempfile
import threading
import time
import uuid
from collections import Counter, defaultdict
from typing import Any, Dict, List, Optional

from backend.ingest.excel_loader import (CT_ACTIVITY, DEFAULT_QUOTAS, QuotaBook, build_school,
                                         normalise_class_name, read_contract_rows)
from backend.optimizer import diagnostics
from backend.optimizer.engine import RULES_PATH, generate_timetable, load_rules

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
DATA_DIR = os.environ.get("TIMETABLE_DATA_DIR", os.path.join(ROOT, "data_store"))
MAX_UPLOAD_BYTES = 10 * 1024 * 1024
_ID = re.compile(r"^[a-f0-9]{32}$")


class WorkspaceError(Exception):
    def __init__(self, message: str, status: int = 400):
        super().__init__(message)
        self.status = status


def _dir(ws_id: str) -> str:
    if not _ID.match(ws_id or ""):
        raise WorkspaceError("Unknown workspace", 404)
    d = os.path.join(DATA_DIR, ws_id)
    if not os.path.isdir(d):
        raise WorkspaceError("Workspace not found - it may have been deleted", 404)
    return d


def _read(ws_id: str, name: str, default=None):
    path = os.path.join(_dir(ws_id), name)
    if not os.path.exists(path):
        return default
    with open(path) as f:
        return json.load(f)


def _write(ws_id: str, name: str, data):
    path = os.path.join(_dir(ws_id), name)
    fd, tmp = tempfile.mkstemp(dir=os.path.dirname(path))
    with os.fdopen(fd, "w") as f:
        json.dump(data, f)
    os.replace(tmp, path)


def _parse_excel(content: bytes) -> List[Dict]:
    if len(content) > MAX_UPLOAD_BYTES:
        raise WorkspaceError("File is larger than 10 MB")
    if not content.startswith(b"PK"):
        raise WorkspaceError("That doesn't look like an .xlsx file. In Excel use File > Save As > Excel Workbook.")
    with tempfile.NamedTemporaryFile(suffix=".xlsx") as tmp:
        tmp.write(content)
        tmp.flush()
        try:
            rows = read_contract_rows(tmp.name)
        except ValueError as e:
            raise WorkspaceError(str(e))
        except Exception as e:  # corrupt workbook etc.
            raise WorkspaceError(f"Could not read the workbook: {e}")
    if not rows:
        raise WorkspaceError("No lessons found. The first sheet needs Class, Teacher and Subject columns.")
    return rows


def create(content: bytes, filename: str = "timetable.xlsx", name: Optional[str] = None,
           quotas: Optional[Dict] = None) -> Dict[str, Any]:
    rows = _parse_excel(content)
    ws_id = uuid.uuid4().hex
    d = os.path.join(DATA_DIR, ws_id)
    os.makedirs(d, exist_ok=True)
    with open(os.path.join(d, "source.xlsx"), "wb") as f:
        f.write(content)
    if quotas is None:
        with open(DEFAULT_QUOTAS) as f:
            quotas = json.load(f)
    now = time.time()
    _write(ws_id, "meta.json", {"id": ws_id, "name": name or os.path.splitext(os.path.basename(filename))[0],
                                "filename": os.path.basename(filename), "created": now, "updated": now})
    _write(ws_id, "rows.json", rows)
    _write(ws_id, "quotas.json", quotas)
    _write(ws_id, "rules.json", load_rules(RULES_PATH))
    rebuild(ws_id)
    return summary(ws_id)


def replace_excel(ws_id: str, content: bytes, filename: str) -> Dict[str, Any]:
    rows = _parse_excel(content)
    with open(os.path.join(_dir(ws_id), "source.xlsx"), "wb") as f:
        f.write(content)
    _write(ws_id, "rows.json", rows)
    meta = _read(ws_id, "meta.json")
    meta.update(filename=os.path.basename(filename), updated=time.time())
    _write(ws_id, "meta.json", meta)
    rebuild(ws_id)
    return summary(ws_id)


def delete(ws_id: str):
    shutil.rmtree(_dir(ws_id))


def rebuild(ws_id: str):
    school = build_school(_read(ws_id, "rows.json"), _read(ws_id, "quotas.json"))
    _write(ws_id, "school.json", school)
    tt = os.path.join(_dir(ws_id), "timetable.json")
    if os.path.exists(tt):
        os.remove(tt)  # stale once the data changes


def get_quotas(ws_id: str) -> Dict:
    return _read(ws_id, "quotas.json")


def set_quotas(ws_id: str, quotas: Dict) -> Dict[str, Any]:
    cal = quotas.get("calendar", {})
    days, ppd = cal.get("days"), cal.get("periods_per_day")
    if not isinstance(days, list) or not days or not all(isinstance(x, str) and x for x in days):
        raise WorkspaceError("calendar.days must be a list of day names")
    if not isinstance(ppd, int) or not 1 <= ppd <= 14:
        raise WorkspaceError("calendar.periods_per_day must be a whole number between 1 and 14")
    for band, table in quotas.get("band_quotas", {}).items():
        for subj, n in table.items():
            if not isinstance(n, int) or n < 0 or n > len(days) * ppd:
                raise WorkspaceError(f"Quota for {subj} ({band}) must be a whole number between 0 and {len(days) * ppd}")
    labs = quotas.get("labs", {})
    if not isinstance(labs, dict):
        raise WorkspaceError("labs must be an object")
    used = {}
    for lab, spec in labs.items():
        if not str(lab).strip():
            raise WorkspaceError("Every lab needs a name")
        if not isinstance(spec, dict) or not isinstance(spec.get("count"), int) or not 0 <= spec["count"] <= 50:
            raise WorkspaceError(f"Number of rooms for {lab} must be a whole number between 0 and 50")
        subs = spec.get("subjects", [])
        if not isinstance(subs, list) or not all(isinstance(x, str) for x in subs):
            raise WorkspaceError(f"Subjects for {lab} must be a list of names")
        for x in subs:
            if x in used:
                raise WorkspaceError(f"{x} is listed under both {used[x]} and {lab}; pick one lab")
            used[x] = lab
    for cls, table in (quotas.get("class_overrides") or {}).items():
        if not isinstance(table, dict):
            raise WorkspaceError(f"Overrides for {cls} must be an object")
        for subj, n in table.items():
            if not isinstance(n, int) or n < 0 or n > len(days) * ppd:
                raise WorkspaceError(f"Periods of {subj} for {cls} must be a whole number between 0 and {len(days) * ppd}")
    for name, slots_off in (quotas.get("teacher_unavailable") or {}).items():
        if not isinstance(slots_off, list) or not all(
                isinstance(u, dict) and isinstance(u.get("day"), str) and isinstance(u.get("period"), int)
                for u in slots_off):
            raise WorkspaceError(f"Availability for {name} must be a list of {{day, period}}")
    _write(ws_id, "quotas.json", quotas)
    rebuild(ws_id)
    return summary(ws_id)


def get_rules(ws_id: str) -> Dict:
    return _read(ws_id, "rules.json")


def set_rules(ws_id: str, rules: Dict) -> Dict:
    if not isinstance(rules, dict):
        raise WorkspaceError("rules must be an object")
    _write(ws_id, "rules.json", rules)
    tt = os.path.join(_dir(ws_id), "timetable.json")
    if os.path.exists(tt):
        os.remove(tt)
    return rules


def school(ws_id: str) -> Dict:
    return _read(ws_id, "school.json")


def timetable(ws_id: str) -> Optional[Dict]:
    return _read(ws_id, "timetable.json")


def source_path(ws_id: str) -> str:
    return os.path.join(_dir(ws_id), "source.xlsx")


def summary(ws_id: str) -> Dict[str, Any]:
    """Everything the review screen needs, computed from the stored model."""
    meta = _read(ws_id, "meta.json")
    s = _read(ws_id, "school.json")
    quotas = _read(ws_id, "quotas.json")
    rules = _read(ws_id, "rules.json")
    rows = _read(ws_id, "rows.json")
    slots = len(s["days"]) * s["periods_per_day"]
    subj = {x["id"]: x["name"] for x in s["subjects"]}
    tname = {t["id"]: t["name"] for t in s["teachers"]}
    cname = {c["id"]: c["name"] for c in s["classes"]}

    class_load = defaultdict(int)
    class_fill = defaultdict(int)
    tload = Counter()
    tsubjects = defaultdict(set)
    tclasses = defaultdict(set)
    for l in s["lessons"]:
        for c in l["class_ids"]:
            class_load[c] += l["periods_per_week"]
            if subj[l["subject_id"]] == CT_ACTIVITY:
                class_fill[c] += l["periods_per_week"]
        for t in l["teacher_ids"]:
            tload[t] += l["periods_per_week"]
            tsubjects[t].add(subj[l["subject_id"]])
            tclasses[t].update(cname[c] for c in l["class_ids"])

    book = QuotaBook(quotas)
    # which subjects appear in which band, and where their quota comes from
    band_subjects: Dict[str, Dict[str, Dict]] = defaultdict(dict)
    self_contained = {c["name"] for c in s["classes"] if c.get("self_contained")}
    for r in rows:
        norm = normalise_class_name(r["class"])
        if not norm or norm[0] in self_contained:
            continue
        cn, grade, _ = norm
        band = book.grade_to_band.get(grade, "other")
        subject = r["subject"]
        if subject not in band_subjects[band]:
            if subject in quotas.get("band_quotas", {}).get(band, {}):
                quota, source = quotas["band_quotas"][band][subject], "band"
            elif subject in quotas.get("default", {}):
                quota, source = quotas["default"][subject], "default"
            else:
                quota, source = None, "missing"
            band_subjects[band][subject] = {"subject": subject, "classes": set(), "quota": quota, "source": source}
        band_subjects[band][subject]["classes"].add(cn)

    # per class: each subject's periods and where the number comes from
    class_subjects: Dict[str, Dict[str, Dict]] = defaultdict(dict)
    overrides = quotas.get("class_overrides") or {}
    for r in rows:
        norm = normalise_class_name(r["class"])
        if not norm or norm[0] in self_contained:
            continue
        cn, grade, _ = norm
        e = class_subjects[cn].setdefault(r["subject"], {"subject": r["subject"], "rows": 0})
        e["rows"] += 1
        band_val = quotas.get("band_quotas", {}).get(book.grade_to_band.get(grade), {}).get(r["subject"])
        if band_val is None:
            band_val = quotas.get("default", {}).get(r["subject"])
        e["grade_value"] = band_val
        e["override"] = overrides.get(cn, {}).get(r["subject"])
        e["periods"] = book.lookup(cn, grade, r["subject"])

    tobj = {t["id"]: t for t in s["teachers"]}
    max_day = rules.get("teacher_rules", {}).get("max_periods_per_day", s["periods_per_day"])

    def capacity(tid):
        off = Counter(u["day"] for u in tobj[tid].get("unavailable_slots", []))
        return sum(min(max_day, s["periods_per_day"] - off[d]) for d in s["days"])

    lab_need = Counter()
    for l in s["lessons"]:
        if l.get("lab"):
            lab_need[l["lab"]] += l["periods_per_week"]

    def class_sort(name):
        n = normalise_class_name(name)
        return (n[1], n[2]) if n else (99, name)

    diag = diagnostics.check(s, rules)
    tt = _read(ws_id, "timetable.json")
    return {
        "workspace": meta,
        "calendar": {"days": s["days"], "periods_per_day": s["periods_per_day"], "slots": slots},
        "counts": {
            "classes": len(s["classes"]),
            "scheduled_classes": sum(1 for c in s["classes"] if not c.get("self_contained")),
            "teachers": len(tload),
            "lessons": len(s["lessons"]),
            "joint_lessons": sum(1 for l in s["lessons"] if len(l["class_ids"]) > 1),
            "co_taught_lessons": sum(1 for l in s["lessons"] if len(l["teacher_ids"]) > 1),
            "excel_rows": len(rows),
        },
        "classes": sorted([{
            "id": c["id"], "name": c["name"], "grade": c["grade"],
            "band": book.grade_to_band.get(c["grade"], "other"),
            "self_contained": bool(c.get("self_contained")),
            "class_teacher": tname.get(c.get("class_teacher_id"), ""),
            "total": slots if c.get("self_contained") else class_load[c["id"]],
            "filled_by_class_teacher": class_fill[c["id"]],
            "subjects": sorted(class_subjects.get(c["name"], {}).values(), key=lambda e: e["subject"]),
        } for c in s["classes"]], key=lambda c: class_sort(c["name"])),
        "teachers": sorted([{
            "id": t, "name": tname.get(t, t), "load": n, "capacity": capacity(t),
            "unavailable": tobj[t].get("unavailable_slots", []),
            "subjects": sorted(tsubjects[t]), "classes": sorted(tclasses[t], key=class_sort),
        } for t, n in tload.items()], key=lambda t: -t["load"]),
        "joint_lessons": [{
            "subject": subj[l["subject_id"]], "classes": [cname[c] for c in l["class_ids"]],
            "teachers": [tname[t] for t in l["teacher_ids"]], "periods_per_week": l["periods_per_week"],
        } for l in s["lessons"] if len(l["class_ids"]) > 1],
        "quota_table": {band: sorted(({**e, "classes": sorted(e["classes"], key=class_sort)}
                                      for e in subs.values()), key=lambda e: e["subject"])
                        for band, subs in band_subjects.items()},
        "bands": quotas.get("bands", {}),
        "labs": quotas.get("labs", {}),
        "lab_usage": {lab: {"needed": lab_need[lab], "capacity": spec.get("count", 0) * slots}
                      for lab, spec in quotas.get("labs", {}).items()},
        "all_subjects": sorted({r["subject"] for r in rows} - {"CLASS TEACHER"}),
        "import_warnings": s.get("import_warnings", []),
        "errors": diag["errors"],
        "warnings": diag["warnings"],
        "has_timetable": bool(tt and tt.get("success")),
    }


# --------------------------------------------------------------------------- jobs
class Job:
    def __init__(self, ws_id: str, seconds: float):
        self.id = uuid.uuid4().hex
        self.ws_id = ws_id
        self.seconds = seconds
        self.state = "queued"       # queued -> running -> done | failed | error
        self.phase = "queued"       # checking -> solving -> verifying
        self.started = time.time()
        self.trace: List[List[float]] = []
        self.stop = False
        self.result_summary: Optional[Dict] = None
        self.error: Optional[str] = None

    def public(self) -> Dict[str, Any]:
        return {"id": self.id, "workspace_id": self.ws_id, "state": self.state, "phase": self.phase,
                "elapsed": round(time.time() - self.started, 1), "seconds": self.seconds,
                "trace": self.trace, "stop_requested": self.stop, "result": self.result_summary,
                "error": self.error}


_jobs: Dict[str, Job] = {}
_ws_job: Dict[str, str] = {}
_solve_lock = threading.Semaphore(int(os.environ.get("TIMETABLE_PARALLEL_SOLVES", "1")))


def start_job(ws_id: str, seconds: float) -> Dict[str, Any]:
    _dir(ws_id)
    current = _ws_job.get(ws_id)
    if current and _jobs[current].state in ("queued", "running"):
        return _jobs[current].public()
    seconds = float(max(5, min(seconds, 600)))
    job = Job(ws_id, seconds)
    _jobs[job.id] = job
    _ws_job[ws_id] = job.id
    threading.Thread(target=_run, args=(job,), daemon=True).start()
    return job.public()


def _run(job: Job):
    with _solve_lock:
        job.state = "running"
        job.started = time.time()
        try:
            from backend.editing import constraints_of
            s, locked, forbidden, skipped = constraints_of(school(job.ws_id), get_edits(job.ws_id))
            rules = copy.deepcopy(get_rules(job.ws_id))
            res = generate_timetable(
                s, rules=rules, time_limit=job.seconds, locked_assignments=locked, forbidden=forbidden,
                on_phase=lambda p: setattr(job, "phase", p),
                on_progress=lambda t, obj: job.trace.append([t, obj]),
                should_stop=lambda: job.stop)
            res["generated_at"] = time.time()
            res.setdefault("warnings", []).extend(f"Earlier change no longer fits the data and was ignored: {d}"
                                                  for d in skipped)
            if res["success"]:
                _push_history(job.ws_id)
            _write(job.ws_id, "timetable.json", res)
            meta = res.get("metadata", {})
            job.result_summary = {"success": res["success"], "status": res["status"],
                                  "violations": res.get("violations", [])[:40],
                                  "explanation": res.get("explanation", []),
                                  "quality": meta.get("quality"), "verified": meta.get("verified"),
                                  "duration": meta.get("solve_duration_seconds"),
                                  "first_solution": meta.get("first_solution_seconds"),
                                  "stopped_early": meta.get("stopped_early")}
            job.state = "done" if res["success"] else "failed"
        except WorkspaceError as e:
            job.state, job.error = "error", str(e)
        except Exception as e:  # never leave the UI spinning
            job.state, job.error = "error", f"{type(e).__name__}: {e}"


def get_job(job_id: str) -> Dict[str, Any]:
    if job_id not in _jobs:
        raise WorkspaceError("Unknown job", 404)
    return _jobs[job_id].public()


def stop_job(job_id: str) -> Dict[str, Any]:
    if job_id not in _jobs:
        raise WorkspaceError("Unknown job", 404)
    _jobs[job_id].stop = True
    return _jobs[job_id].public()


# --------------------------------------------------------------------------- edits (chat assistant)
HISTORY_KEEP = 15
_proposals: Dict[str, Dict] = {}


def get_edits(ws_id: str) -> List[Dict]:
    return _read(ws_id, "edits.json", []) or []


def remove_edit(ws_id: str, edit_id: str) -> List[Dict]:
    """Forget an applied edit for future regenerations (the current timetable is unchanged)."""
    edits = [e for e in get_edits(ws_id) if e.get("id") != edit_id]
    _write(ws_id, "edits.json", edits)
    return edits


def _push_history(ws_id: str):
    tt = timetable(ws_id)
    if tt and tt.get("success"):
        hist = _read(ws_id, "history.json", []) or []
        hist.append({"timetable": tt, "edits": get_edits(ws_id), "at": time.time()})
        _write(ws_id, "history.json", hist[-HISTORY_KEEP:])


def history_size(ws_id: str) -> int:
    return len(_read(ws_id, "history.json", []) or [])


def propose_edit(ws_id: str, requests: List[Dict], seconds: float = 20) -> Dict[str, Any]:
    from backend.editing import propose
    tt = timetable(ws_id)
    with _solve_lock:
        res = propose(school(ws_id), copy.deepcopy(get_rules(ws_id)), tt, requests,
                      standing=get_edits(ws_id), time_limit=max(3, min(float(seconds), 120)))
    if not res.get("ok"):
        return res
    pid = uuid.uuid4().hex
    _proposals[pid] = {"ws_id": ws_id, "compiled": res["compiled"], "timetable": res["timetable"],
                       "base_generated_at": (tt or {}).get("generated_at"), "created": time.time()}
    for old in [k for k, v in _proposals.items() if time.time() - v["created"] > 3600]:
        _proposals.pop(old, None)
    return {"ok": True, "proposal_id": pid, "requests": res["requests"], "diff": res["diff"],
            "message": res["message"], "quality": res["timetable"]["metadata"].get("quality"),
            "verified": res["timetable"]["metadata"].get("verified")}


def apply_proposal(ws_id: str, proposal_id: str) -> Dict[str, Any]:
    p = _proposals.get(proposal_id)
    if not p or p["ws_id"] != ws_id:
        raise WorkspaceError("That suggestion has expired. Ask again.", 404)
    tt = timetable(ws_id)
    if (tt or {}).get("generated_at") != p["base_generated_at"]:
        raise WorkspaceError("The timetable changed since this suggestion was made. Ask again.", 409)
    _push_history(ws_id)
    new = dict(p["timetable"])
    new["generated_at"] = time.time()
    new["edited"] = True
    _write(ws_id, "timetable.json", new)
    edits = get_edits(ws_id)
    for c in p["compiled"]:
        edits.append({**c, "id": uuid.uuid4().hex[:12], "applied_at": time.time()})
    _write(ws_id, "edits.json", edits)
    _proposals.pop(proposal_id, None)
    return {"applied": True, "edits": edits, "undo_available": history_size(ws_id)}


def discard_proposal(ws_id: str, proposal_id: str) -> Dict[str, Any]:
    _proposals.pop(proposal_id, None)
    return {"discarded": True}


def undo(ws_id: str) -> Dict[str, Any]:
    hist = _read(ws_id, "history.json", []) or []
    if not hist:
        raise WorkspaceError("Nothing to undo.", 409)
    last = hist.pop()
    _write(ws_id, "timetable.json", last["timetable"])
    _write(ws_id, "edits.json", last.get("edits", []))
    _write(ws_id, "history.json", hist)
    return {"undone": True, "edits": last.get("edits", []), "undo_available": len(hist)}


# --------------------------------------------------------------------------- setup changes (chat assistant)
def propose_setup(ws_id: str, changes: List[Dict]) -> Dict[str, Any]:
    from backend.setup_edit import preview
    quotas = get_quotas(ws_id)
    res = preview(_read(ws_id, "rows.json"), quotas, get_rules(ws_id), changes)
    if not res["ok"]:
        return res
    pid = uuid.uuid4().hex
    _proposals[pid] = {"ws_id": ws_id, "kind": "setup", "quotas": res["quotas"], "base": quotas, "created": time.time()}
    tt = timetable(ws_id)
    return {"ok": True, "proposal_id": pid, "kind": "setup", "changes": res["changes"],
            "new_errors": res["new_errors"], "fixed_errors": res["fixed_errors"], "still_errors": res["still_errors"],
            "clears_timetable": bool(tt and tt.get("success"))}


def apply_setup(ws_id: str, proposal_id: str) -> Dict[str, Any]:
    p = _proposals.get(proposal_id)
    if not p or p["ws_id"] != ws_id or p.get("kind") != "setup":
        raise WorkspaceError("That suggestion has expired. Ask again.", 404)
    if get_quotas(ws_id) != p["base"]:
        raise WorkspaceError("The setup changed since this suggestion was made. Ask again.", 409)
    s = set_quotas(ws_id, p["quotas"])
    _proposals.pop(proposal_id, None)
    return s
