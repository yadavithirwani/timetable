"""Chat assistant driven by a scripted fake LLM: tool plumbing, safety (nothing saved before Apply),
error handling, and the edit -> apply -> regenerate -> undo flow through the HTTP API."""
import time

import pytest

pytest.importorskip("fastapi")
from fastapi.testclient import TestClient  # noqa: E402

import backend.main as main  # noqa: E402
from backend import workspaces as W  # noqa: E402
from backend.ai import chat_agent as A  # noqa: E402
from backend.ai.chat_agent import Reply, ScriptedLLM, ToolCall  # noqa: E402
from backend.ingest.template import build_workbook  # noqa: E402


@pytest.fixture(scope="module")
def solved(tmp_path_factory):
    """One uploaded + solved template school, shared (tests that change it restore via undo)."""
    d = str(tmp_path_factory.mktemp("ws"))
    old = W.DATA_DIR
    W.DATA_DIR = d
    c = TestClient(main.app)
    ws_id = c.post("/api/v2/workspaces?filename=s.xlsx", content=build_workbook(),
                   headers={"Content-Type": "application/octet-stream"}).json()["workspace"]["id"]
    job = c.post(f"/api/v2/workspaces/{ws_id}/solve", json={"seconds": 10}).json()
    for _ in range(300):
        if c.get(f"/api/v2/jobs/{job['id']}").json()["state"] not in ("queued", "running"):
            break
        time.sleep(0.1)
    assert W.timetable(ws_id)["success"]
    yield c, ws_id
    W.DATA_DIR = old


def _two_slots(ws_id, class_name):
    s, asg = W.school(ws_id), W.timetable(ws_id)["assignments"]
    cid = next(c["id"] for c in s["classes"] if c["name"] == class_name)
    lessons = {l["id"]: l for l in s["lessons"]}
    rows = [a for a in asg if a["class_id"] == cid and not lessons[a["lesson_id"]].get("fixed_period")
            and not lessons[a["lesson_id"]].get("lab") and len(lessons[a["lesson_id"]]["class_ids"]) == 1]
    a = rows[0]
    b = next(r for r in rows if r["lesson_id"] != a["lesson_id"] and r["day"] != a["day"])
    return a, b


def test_agent_looks_then_proposes_without_saving(solved):
    c, ws_id = solved
    cname = W.school(ws_id)["classes"][0]["name"]
    a, b = _two_slots(ws_id, cname)
    before = W.timetable(ws_id)
    llm = ScriptedLLM([
        lambda m: Reply(tool_calls=[ToolCall("show_timetable", {"kind": "class", "name": cname.lower()}, "1")]),
        lambda m: Reply(tool_calls=[ToolCall("propose_changes", {"changes": [
            {"type": "swap", "class": cname, "day1": a["day"], "period1": a["period"],
             "day2": b["day"], "period2": b["period"]}]}, "2")]),
        lambda m: Reply(text="Here's the swap - press Apply to keep it."),
    ])
    out = A.chat(ws_id, "swap these two", llm=llm)
    assert out["proposal"]["ok"] and out["proposal"]["verified"]
    assert out["view"] == {"kind": "class", "name": cname}
    assert [t["tool"] for t in out["tool_log"]] == ["show_timetable", "propose_changes"]
    # the model saw the real timetable and the solver's summary
    tool_msgs = [m for m in llm.seen[1] if m["role"] == "tool"]
    assert a["day"] in tool_msgs[0]["result"]["timetable"]
    assert "Not applied" in [m for m in llm.seen[2] if m["role"] == "tool"][-1]["result"]["note"]
    # nothing saved before Apply
    assert W.timetable(ws_id) == before


def test_tool_errors_go_back_to_the_model(solved):
    c, ws_id = solved
    llm = ScriptedLLM([
        lambda m: Reply(tool_calls=[ToolCall("show_timetable", {"kind": "class", "name": "99 Z"})]),
        lambda m: Reply(text="I couldn't find that class."),
    ])
    out = A.chat(ws_id, "show 99 Z", llm=llm)
    err = [m for m in llm.seen[1] if m["role"] == "tool"][0]["result"]
    assert "don't know a class" in err["error"]
    assert out["tool_log"][0]["ok"] is False and out["proposal"] is None


def test_impossible_change_reported_to_model(solved):
    c, ws_id = solved
    cname = W.school(ws_id)["classes"][0]["name"]
    llm = ScriptedLLM([
        lambda m: Reply(tool_calls=[ToolCall("propose_changes", {"changes": [
            {"type": "move", "class": cname, "subject": "CLASS TEACHER", "to_day": "Monday", "to_period": 6}]})]),
        lambda m: Reply(text="Sorry, the class teacher period has to stay first."),
    ])
    out = A.chat(ws_id, "move class teacher period to 6th", llm=llm)
    res = [m for m in llm.seen[1] if m["role"] == "tool"][0]["result"]
    assert res["ok"] is False and res["reasons"]
    assert out["proposal"] is None


def test_runaway_tool_loop_stops(solved):
    c, ws_id = solved
    cname = W.school(ws_id)["classes"][0]["name"]
    loop = lambda m: Reply(tool_calls=[ToolCall("show_timetable", {"kind": "class", "name": cname})])  # noqa: E731
    out = A.chat(ws_id, "hi", llm=ScriptedLLM([loop] * 20))
    assert len(out["tool_log"]) == A.MAX_STEPS and "couldn't finish" in out["reply"]


def test_cover_plan_tool(solved):
    c, ws_id = solved
    asg = W.timetable(ws_id)["assignments"]
    s = W.school(ws_id)
    t = next(t for t in s["teachers"] if any(a["teacher_id"] == t["id"] and a["day"] == "Wednesday" for a in asg))
    llm = ScriptedLLM([
        lambda m: Reply(tool_calls=[ToolCall("plan_cover", {"teacher": t["name"], "day": "wed"})]),
        lambda m: Reply(text="Here is the cover plan."),
    ])
    out = A.chat(ws_id, f"{t['name']} is absent on wednesday", llm=llm)
    assert out["cover"]["teacher"] == t["name"] and out["cover"]["periods"]


def test_no_key_is_a_clear_error(solved, monkeypatch):
    c, ws_id = solved
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.delenv("GOOGLE_API_KEY", raising=False)
    r = c.post(f"/api/v2/workspaces/{ws_id}/chat", json={"message": "hello"})
    assert r.status_code == 503 and "API key" in r.json()["detail"]
    assert c.get(f"/api/v2/workspaces/{ws_id}/assistant").json()["ai_available"] is False


def test_apply_regenerate_undo_via_api(solved):
    c, ws_id = solved
    cname = W.school(ws_id)["classes"][1]["name"]
    a, b = _two_slots(ws_id, cname)
    original = W.timetable(ws_id)
    p = c.post(f"/api/v2/workspaces/{ws_id}/edits/propose", json={"changes": [
        {"type": "swap", "class": cname, "day1": a["day"], "period1": a["period"],
         "day2": b["day"], "period2": b["period"]}]}).json()
    assert p["ok"] and p["diff"]["moved_periods"] >= 2

    applied = c.post(f"/api/v2/workspaces/{ws_id}/proposals/{p['proposal_id']}/apply").json()
    assert applied["applied"] and len(applied["edits"]) == 1
    tt = c.get(f"/api/v2/workspaces/{ws_id}/timetable").json()
    assert tt["timetable"]["edited"] and tt["undo_available"] >= 1

    def subj_at(asg, slot):
        return next(x["subject_id"] for x in asg if x["class_id"] == a["class_id"]
                    and x["day"] == slot["day"] and x["period"] == slot["period"])
    assert subj_at(tt["timetable"]["assignments"], b) == a["subject_id"]

    # stale proposal can't be applied twice
    assert c.post(f"/api/v2/workspaces/{ws_id}/proposals/{p['proposal_id']}/apply").status_code == 404

    # a full regenerate keeps the applied swap
    job = c.post(f"/api/v2/workspaces/{ws_id}/solve", json={"seconds": 8}).json()
    for _ in range(300):
        if c.get(f"/api/v2/jobs/{job['id']}").json()["state"] not in ("queued", "running"):
            break
        time.sleep(0.1)
    regen = W.timetable(ws_id)
    assert regen["success"] and subj_at(regen["assignments"], b) == a["subject_id"]

    # undo twice: back past the regenerate, then past the edit
    c.post(f"/api/v2/workspaces/{ws_id}/undo")
    u = c.post(f"/api/v2/workspaces/{ws_id}/undo").json()
    assert u["undone"] and u["edits"] == []
    assert W.timetable(ws_id)["assignments"] == original["assignments"]


def test_gemini_message_conversion(monkeypatch):
    """GeminiLLM builds valid google-genai contents (tool results paired with calls) without a network call."""
    pytest.importorskip("google.genai")
    from google.genai import types
    captured = {}

    class FakeModels:
        def generate_content(self, model, contents, config):
            captured.update(contents=contents, config=config)
            part = types.Part(function_call=types.FunctionCall(name="show_timetable", args={"kind": "class", "name": "9 A"}, id="x1"))
            return types.GenerateContentResponse(candidates=[types.Candidate(content=types.Content(role="model", parts=[part]))])

    llm = A.GeminiLLM.__new__(A.GeminiLLM)
    llm.client = type("C", (), {"models": FakeModels()})()
    llm.model = "fake"
    r1 = llm.chat("sys", [{"role": "user", "text": "show 9 A"}], A.TOOLS)
    assert r1.tool_calls[0].name == "show_timetable" and r1.tool_calls[0].args["name"] == "9 A"
    msgs = [{"role": "user", "text": "show 9 A"}, {"role": "assistant", "reply": r1},
            {"role": "tool", "name": "show_timetable", "id": "x1", "result": {"ok": 1}}]
    llm.chat("sys", msgs, A.TOOLS)
    cs = captured["contents"]
    assert [c.role for c in cs] == ["user", "model", "user"]
    assert cs[2].parts[0].function_response.name == "show_timetable"
    assert {d.name for d in captured["config"].tools[0].function_declarations} == {t["name"] for t in A.TOOLS}
