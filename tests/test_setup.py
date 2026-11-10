"""Setup screens: teacher availability, labs and per-class periods - stored in quotas, applied on rebuild."""
import copy
import time

import pytest

pytest.importorskip("fastapi")
from fastapi.testclient import TestClient  # noqa: E402

import backend.main as main  # noqa: E402
from backend import workspaces as W  # noqa: E402
from backend.ingest.template import build_workbook  # noqa: E402


@pytest.fixture
def ws(tmp_path, monkeypatch):
    monkeypatch.setattr(W, "DATA_DIR", str(tmp_path))
    c = TestClient(main.app)
    ws_id = c.post("/api/v2/workspaces?filename=s.xlsx", content=build_workbook(),
                   headers={"Content-Type": "application/octet-stream"}).json()["workspace"]["id"]
    return c, ws_id


def _put(c, ws_id, q):
    return c.put(f"/api/v2/workspaces/{ws_id}/quotas", json=q)


def _teacher(summary, class_teacher: bool):
    return next(t for t in summary["teachers"] if ("CLASS TEACHER" in t["subjects"]) == class_teacher)


def test_availability_is_honoured_by_the_solver(ws):
    c, ws_id = ws
    s = c.get(f"/api/v2/workspaces/{ws_id}").json()
    t = _teacher(s, class_teacher=False)
    q = c.get(f"/api/v2/workspaces/{ws_id}/quotas").json()
    q["teacher_unavailable"] = {t["name"].lower(): [{"day": "Saturday", "period": p} for p in range(1, 9)]}
    s2 = _put(c, ws_id, q).json()
    assert s2["errors"] == []
    t2 = next(x for x in s2["teachers"] if x["name"] == t["name"])
    assert t2["capacity"] == t["capacity"] - 8 and len(t2["unavailable"]) == 8
    job = c.post(f"/api/v2/workspaces/{ws_id}/solve", json={"seconds": 8}).json()
    for _ in range(200):
        if c.get(f"/api/v2/jobs/{job['id']}").json()["state"] not in ("queued", "running"):
            break
        time.sleep(0.1)
    tt = W.timetable(ws_id)
    assert tt["success"]
    tid = next(x["id"] for x in W.school(ws_id)["teachers"] if x["name"] == t["name"])
    assert not any(a["teacher_id"] == tid and a["day"] == "Saturday" for a in tt["assignments"])


def test_class_teacher_off_in_period_one_is_explained(ws):
    c, ws_id = ws
    s = c.get(f"/api/v2/workspaces/{ws_id}").json()
    t = _teacher(s, class_teacher=True)
    q = c.get(f"/api/v2/workspaces/{ws_id}/quotas").json()
    q["teacher_unavailable"] = {t["name"]: [{"day": "Wednesday", "period": 1}]}
    errs = _put(c, ws_id, q).json()["errors"]
    assert any(t["name"] in e and "period 1" in e and "Wednesday" in e for e in errs)


def test_unknown_teacher_is_a_warning_not_a_crash(ws):
    c, ws_id = ws
    q = c.get(f"/api/v2/workspaces/{ws_id}/quotas").json()
    q["teacher_unavailable"] = {"NOBODY": [{"day": "Monday", "period": 1}]}
    s = _put(c, ws_id, q).json()
    assert any("NOBODY" in w for w in s["import_warnings"])


def test_class_override_changes_only_that_class(ws):
    c, ws_id = ws
    q = c.get(f"/api/v2/workspaces/{ws_id}/quotas").json()
    q["class_overrides"] = {"6 A": {"MATHS": 7, "ENG": 5}}
    s = _put(c, ws_id, q).json()
    a = next(x for x in s["classes"] if x["name"] == "6 A")
    b = next(x for x in s["classes"] if x["name"] == "6 B")
    m = {e["subject"]: e for e in a["subjects"]}
    assert m["MATHS"]["periods"] == 7 and m["MATHS"]["override"] == 7 and m["MATHS"]["grade_value"] != 7
    assert {e["subject"]: e["periods"] for e in b["subjects"]}["MATHS"] == m["MATHS"]["grade_value"]


def test_labs_edit_and_usage(ws):
    c, ws_id = ws
    q = c.get(f"/api/v2/workspaces/{ws_id}/quotas").json()
    q["labs"]["ART ROOM"] = {"count": 1, "subjects": ["ART"]}
    s = _put(c, ws_id, q).json()
    assert s["lab_usage"]["ART ROOM"]["needed"] > 0 and s["lab_usage"]["ART ROOM"]["capacity"] == 48
    assert "ART" in s["all_subjects"] and "CLASS TEACHER" not in s["all_subjects"]
    q["labs"]["ART ROOM"]["count"] = 0
    s = _put(c, ws_id, q).json()
    assert any("ART ROOM" in e for e in s["errors"])


@pytest.mark.parametrize("mutate,msg", [
    (lambda q: q["labs"].__setitem__("X", {"count": -1, "subjects": []}), "between 0 and 50"),
    (lambda q: q["labs"].__setitem__("X", {"count": 1, "subjects": ["COMPUTERS-PR"]}), "listed under both"),
    (lambda q: q.__setitem__("class_overrides", {"6 A": {"MATHS": -2}}), "whole number"),
    (lambda q: q.__setitem__("teacher_unavailable", {"A": [{"day": "Mon"}]}), "list of {day, period}"),
])
def test_setup_validation(ws, mutate, msg):
    c, ws_id = ws
    q = copy.deepcopy(c.get(f"/api/v2/workspaces/{ws_id}/quotas").json())
    mutate(q)
    r = _put(c, ws_id, q)
    assert r.status_code == 400 and msg in r.json()["detail"], r.json()


# ---------------------------------------------------------------- setup changes in words (assistant)
from backend import setup_edit as S  # noqa: E402
from backend.editing import EditError  # noqa: E402


def _rq(ws_id):
    return W._read(ws_id, "rows.json"), W.get_quotas(ws_id)


@pytest.mark.parametrize("grades", ["7", "6-8", "6 to 8", "grades 6 and 8", "Class 7"])
def test_grade_words_resolve_to_band(ws, grades):
    c, ws_id = ws
    rows, q = _rq(ws_id)
    new, d = S.apply_changes(rows, q, [{"type": "set_periods", "subject": "phy edu", "periods": 3, "grades": grades}])
    assert new["band_quotas"]["6-8"]["PHY EDU"] == 3 and "grades 6-8" in d[0]


def test_class_override_and_availability_toggle(ws):
    c, ws_id = ws
    rows, q = _rq(ws_id)
    new, d = S.apply_changes(rows, q, [
        {"type": "set_periods", "subject": "MATHS", "periods": 7, "class": "6b"},
        {"type": "teacher_availability", "teacher": "vikram rao", "day": "sat"},
        {"type": "teacher_availability", "teacher": "vikram rao", "day": "saturday", "periods": [8], "available": True},
    ])
    assert new["class_overrides"]["6 B"]["MATHS"] == 7
    assert [u["period"] for u in new["teacher_unavailable"]["VIKRAM RAO"]] == list(range(1, 8))
    assert q.get("class_overrides", {}) == {}     # input untouched


def test_lab_changes(ws):
    c, ws_id = ws
    rows, q = _rq(ws_id)
    new, d = S.apply_changes(rows, q, [{"type": "lab", "lab": "computer lab", "rooms": 1},
                                       {"type": "lab", "lab": "Robotics Lab", "rooms": 1, "add_subjects": ["computers-pr"]}])
    assert new["labs"]["COMPUTER LAB"]["count"] == 1
    assert new["labs"]["ROBOTICS LAB"]["subjects"] == ["COMPUTERS-PR"]
    assert "COMPUTERS-PR" not in new["labs"]["COMPUTER LAB"]["subjects"]


@pytest.mark.parametrize("ch,msg", [
    ({"type": "set_periods", "subject": "MATHS", "periods": 7}, "which grades or class"),
    ({"type": "set_periods", "subject": "ASTROLOGY", "periods": 2, "grades": "7"}, "don't know a subject"),
    ({"type": "set_periods", "subject": "MATHS", "periods": 2, "grades": "15"}, "grade groups"),
    ({"type": "teacher_availability", "teacher": "NOBODY HERE", "day": "Mon"}, "don't know a teacher"),
    ({"type": "week", "periods_per_day": 30}, "1-14"),
])
def test_setup_change_errors(ws, ch, msg):
    c, ws_id = ws
    rows, q = _rq(ws_id)
    with pytest.raises(EditError, match=msg):
        S.apply_changes(rows, q, [ch])


def test_preview_reports_new_problems(ws):
    c, ws_id = ws
    r = W.propose_setup(ws_id, [{"type": "set_periods", "subject": "MATHS", "periods": 12, "grades": "6-8"}])
    assert r["ok"] and any("week only has" in e for e in r["new_errors"])
    assert W.get_quotas(ws_id)["band_quotas"]["6-8"]["MATHS"] != 12   # nothing saved


def test_agent_setup_flow_and_apply(ws):
    from backend.ai import chat_agent as A
    from backend.ai.chat_agent import Reply, ScriptedLLM, ToolCall
    c, ws_id = ws
    llm = ScriptedLLM([
        lambda m: Reply(tool_calls=[ToolCall("show_setup", {"topic": "class", "name": "6 A"})]),
        lambda m: Reply(tool_calls=[ToolCall("propose_setup", {"changes": [
            {"type": "set_periods", "subject": "PHY EDU", "periods": 3, "class": "6 A"},
            {"type": "set_periods", "subject": "MUSIC", "periods": 1, "class": "6 A"}]})]),
        lambda m: Reply(text="6 A gets one more PE and one less music. Press Apply to save."),
    ])
    out = A.chat(ws_id, "one more PE for 6 A, take it from music", llm=llm)
    seen = [x for x in llm.seen[1] if x["role"] == "tool"][0]["result"]
    assert seen["class"] == "6 A" and "PHY EDU" in seen["periods_per_week"]
    assert out["setup"]["ok"] and len(out["setup"]["changes"]) == 2 and not out["setup"]["new_errors"]
    assert "class_overrides" not in W.get_quotas(ws_id) or not W.get_quotas(ws_id)["class_overrides"]
    s = c.post(f"/api/v2/workspaces/{ws_id}/setup-proposals/{out['setup']['proposal_id']}/apply").json()
    assert W.get_quotas(ws_id)["class_overrides"]["6 A"] == {"PHY EDU": 3, "MUSIC": 1}
    assert s["errors"] == []
    # a second apply of the same suggestion is refused
    assert c.post(f"/api/v2/workspaces/{ws_id}/setup-proposals/{out['setup']['proposal_id']}/apply").status_code == 404
