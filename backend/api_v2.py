"""Upload-first API used by the web app (frontend/index.html).

Uploads are sent as the raw request body (no multipart dependency):
    POST /api/v2/workspaces?filename=school.xlsx   body = .xlsx bytes
"""
from __future__ import annotations

import io
from typing import Any, Dict

from fastapi import APIRouter, Body, Request
from fastapi.responses import JSONResponse, Response

from backend import workspaces as W
from backend.export.excel_export import export
from backend.ingest.template import build_workbook

router = APIRouter(prefix="/api/v2")
XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


def _err(e: W.WorkspaceError):
    return JSONResponse(status_code=e.status, content={"detail": str(e)})


async def _body(request: Request) -> bytes:
    data = await request.body()
    if not data:
        raise W.WorkspaceError("No file received")
    return data


@router.get("/health")
def health():
    return {"ok": True}


@router.get("/template.xlsx")
def template():
    return Response(build_workbook(), media_type=XLSX,
                    headers={"Content-Disposition": 'attachment; filename="timetable_template.xlsx"'})


@router.post("/demo")
def demo():
    try:
        return W.create(build_workbook(), "demo_school.xlsx", "Demo school")
    except W.WorkspaceError as e:
        return _err(e)


@router.post("/workspaces")
async def create(request: Request, filename: str = "school.xlsx", name: str = None):
    try:
        return W.create(await _body(request), filename, name)
    except W.WorkspaceError as e:
        return _err(e)


@router.get("/workspaces/{ws_id}")
def get(ws_id: str):
    try:
        return W.summary(ws_id)
    except W.WorkspaceError as e:
        return _err(e)


@router.delete("/workspaces/{ws_id}")
def remove(ws_id: str):
    try:
        W.delete(ws_id)
        return {"deleted": True}
    except W.WorkspaceError as e:
        return _err(e)


@router.put("/workspaces/{ws_id}/excel")
async def replace(ws_id: str, request: Request, filename: str = "school.xlsx"):
    try:
        return W.replace_excel(ws_id, await _body(request), filename)
    except W.WorkspaceError as e:
        return _err(e)


@router.get("/workspaces/{ws_id}/source.xlsx")
def source(ws_id: str):
    try:
        with open(W.source_path(ws_id), "rb") as f:
            return Response(f.read(), media_type=XLSX)
    except W.WorkspaceError as e:
        return _err(e)


@router.get("/workspaces/{ws_id}/quotas")
def get_quotas(ws_id: str):
    try:
        return W.get_quotas(ws_id)
    except W.WorkspaceError as e:
        return _err(e)


@router.put("/workspaces/{ws_id}/quotas")
def put_quotas(ws_id: str, quotas: Dict[str, Any] = Body(...)):
    try:
        return W.set_quotas(ws_id, quotas)
    except W.WorkspaceError as e:
        return _err(e)


@router.get("/workspaces/{ws_id}/rules")
def get_rules(ws_id: str):
    try:
        return W.get_rules(ws_id)
    except W.WorkspaceError as e:
        return _err(e)


@router.put("/workspaces/{ws_id}/rules")
def put_rules(ws_id: str, rules: Dict[str, Any] = Body(...)):
    try:
        W.set_rules(ws_id, rules)
        return W.summary(ws_id)
    except W.WorkspaceError as e:
        return _err(e)


@router.post("/workspaces/{ws_id}/solve")
def solve(ws_id: str, body: Dict[str, Any] = Body(default={})):
    try:
        seconds = float(body.get("seconds") or W.get_rules(ws_id).get("solver", {}).get("time_limit_seconds", 60))
        return W.start_job(ws_id, seconds)
    except W.WorkspaceError as e:
        return _err(e)


@router.get("/jobs/{job_id}")
def job(job_id: str):
    try:
        return W.get_job(job_id)
    except W.WorkspaceError as e:
        return _err(e)


@router.post("/jobs/{job_id}/stop")
def stop(job_id: str):
    try:
        return W.stop_job(job_id)
    except W.WorkspaceError as e:
        return _err(e)


@router.get("/workspaces/{ws_id}/timetable")
def timetable(ws_id: str):
    """School entities + assignments, everything the timetable explorer needs."""
    try:
        s = W.school(ws_id)
        tt = W.timetable(ws_id)
        return {"days": s["days"], "periods_per_day": s["periods_per_day"],
                "subjects": s["subjects"], "teachers": s["teachers"], "classes": s["classes"],
                "rooms": s["rooms"], "lessons": s["lessons"], "timetable": tt,
                "edits": W.get_edits(ws_id), "undo_available": W.history_size(ws_id)}
    except W.WorkspaceError as e:
        return _err(e)


@router.get("/workspaces/{ws_id}/export.xlsx")
def export_xlsx(ws_id: str):
    try:
        s, tt = W.school(ws_id), W.timetable(ws_id)
        if not tt or not tt.get("success"):
            raise W.WorkspaceError("Generate a timetable first", 409)
        meta = W.summary(ws_id)["workspace"]
        q = tt["metadata"].get("quality", {})
        lines = [f"Timetable for {meta['name']}", f"Status: {tt['status']}",
                 "Independent verification: PASSED" if tt["metadata"].get("verified") else "Verification: FAILED",
                 f"Free class periods: {q.get('free_class_periods')}",
                 f"Same subject twice in a day: {q.get('same_subject_twice_a_day')}",
                 ""] + [f"Note: {w}" for w in tt.get("warnings", [])]
        buf = io.BytesIO()
        export(s, tt, buf, lines)
        safe = "".join(ch for ch in meta["name"] if ch.isalnum() or ch in " -_").strip() or "timetable"
        return Response(buf.getvalue(), media_type=XLSX,
                        headers={"Content-Disposition": f'attachment; filename="{safe} timetable.xlsx"'})
    except W.WorkspaceError as e:
        return _err(e)


# --------------------------------------------------------------------------- edits + assistant
def _guard(fn):
    try:
        return fn()
    except W.WorkspaceError as e:
        return _err(e)


@router.get("/workspaces/{ws_id}/assistant")
def assistant_status(ws_id: str):
    import os
    return _guard(lambda: {"ai_available": bool(os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY")),
                           "edits": W.get_edits(ws_id), "undo_available": W.history_size(ws_id)})


@router.post("/workspaces/{ws_id}/chat")
def chat(ws_id: str, body: Dict[str, Any] = Body(...)):
    from backend.ai.chat_agent import chat as run_chat

    def go():
        msg = str(body.get("message") or "").strip()
        if not msg:
            raise W.WorkspaceError("Type a message first")
        if len(msg) > 2000:
            raise W.WorkspaceError("That message is too long")
        W.school(ws_id)
        try:
            return run_chat(ws_id, msg, body.get("history") or [], api_key=body.get("api_key") or None)
        except W.WorkspaceError:
            raise
        except Exception as e:  # provider errors (bad key, quota) -> readable message
            raise W.WorkspaceError(f"The assistant couldn't answer: {type(e).__name__}: {str(e)[:300]}", 502)
    return _guard(go)


@router.post("/workspaces/{ws_id}/edits/propose")
def propose(ws_id: str, body: Dict[str, Any] = Body(...)):
    """Direct (non-AI) edit requests, e.g. the swap-by-clicking UI."""
    changes = body.get("changes")
    if not isinstance(changes, list) or not changes:
        return _err(W.WorkspaceError("changes must be a non-empty list"))
    return _guard(lambda: W.propose_edit(ws_id, changes, seconds=float(body.get("seconds") or 20)))


@router.post("/workspaces/{ws_id}/proposals/{pid}/apply")
def apply(ws_id: str, pid: str):
    return _guard(lambda: W.apply_proposal(ws_id, pid))


@router.post("/workspaces/{ws_id}/proposals/{pid}/discard")
def discard(ws_id: str, pid: str):
    return _guard(lambda: W.discard_proposal(ws_id, pid))


@router.post("/workspaces/{ws_id}/undo")
def undo(ws_id: str):
    return _guard(lambda: W.undo(ws_id))


@router.delete("/workspaces/{ws_id}/edits/{edit_id}")
def forget_edit(ws_id: str, edit_id: str):
    return _guard(lambda: {"edits": W.remove_edit(ws_id, edit_id)})


@router.post("/workspaces/{ws_id}/setup-proposals/{pid}/apply")
def apply_setup(ws_id: str, pid: str):
    return _guard(lambda: W.apply_setup(ws_id, pid))


@router.post("/workspaces/{ws_id}/setup-proposals")
def propose_setup(ws_id: str, body: Dict[str, Any] = Body(...)):
    changes = body.get("changes")
    if not isinstance(changes, list) or not changes:
        return _err(W.WorkspaceError("changes must be a non-empty list"))
    return _guard(lambda: W.propose_setup(ws_id, changes))
