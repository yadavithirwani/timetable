"""Upload-first API used by the web app: upload -> review -> edit quotas -> solve -> export."""
import io
import time

import pytest

pytest.importorskip("fastapi")
from fastapi.testclient import TestClient  # noqa: E402
from openpyxl import load_workbook  # noqa: E402

import backend.main as main  # noqa: E402
from backend import workspaces as W  # noqa: E402
from backend.ingest.template import build_workbook  # noqa: E402


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(W, "DATA_DIR", str(tmp_path))
    return TestClient(main.app)


def _upload(c, content=None, name="school.xlsx"):
    return c.post(f"/api/v2/workspaces?filename={name}", content=content or build_workbook(),
                  headers={"Content-Type": "application/octet-stream"})


def _wait(c, job_id, timeout=60):
    t0 = time.time()
    while time.time() - t0 < timeout:
        j = c.get(f"/api/v2/jobs/{job_id}").json()
        if j["state"] not in ("queued", "running"):
            return j
        time.sleep(0.2)
    raise AssertionError("job did not finish")


def test_template_round_trips(client):
    r = client.get("/api/v2/template.xlsx")
    assert r.status_code == 200
    ws = load_workbook(io.BytesIO(r.content)).active
    assert [c.value for c in ws[1]] == ["Class", "Teacher", "Subject"]


def test_upload_review_solve_export(client):
    s = _upload(client).json()
    ws_id = s["workspace"]["id"]
    assert s["counts"]["scheduled_classes"] == 6 and s["counts"]["joint_lessons"] == 3
    assert s["errors"] == [] and s["quota_table"]["6-8"]
    assert all(c["total"] == 48 for c in s["classes"])

    job = client.post(f"/api/v2/workspaces/{ws_id}/solve", json={"seconds": 10}).json()
    done = _wait(client, job["id"])
    assert done["state"] == "done", done
    assert done["result"]["verified"] and done["trace"]

    tt = client.get(f"/api/v2/workspaces/{ws_id}/timetable").json()
    assert tt["timetable"]["success"] and tt["timetable"]["assignments"]
    assert client.get(f"/api/v2/workspaces/{ws_id}").json()["has_timetable"]

    x = client.get(f"/api/v2/workspaces/{ws_id}/export.xlsx")
    assert x.status_code == 200
    assert load_workbook(io.BytesIO(x.content)).sheetnames == ["Report", "Classes", "Teachers"]


def test_quota_change_is_rechecked_and_clears_timetable(client):
    ws_id = _upload(client).json()["workspace"]["id"]
    q = client.get(f"/api/v2/workspaces/{ws_id}/quotas").json()
    q["band_quotas"]["6-8"]["ENG"] = 12
    s = client.put(f"/api/v2/workspaces/{ws_id}/quotas", json=q).json()
    assert any("week only has 48" in e for e in s["errors"])
    job = client.post(f"/api/v2/workspaces/{ws_id}/solve", json={"seconds": 5}).json()
    done = _wait(client, job["id"])
    assert done["state"] == "failed" and done["result"]["status"] == "INVALID_INPUT"


def test_invalid_quota_values_rejected(client):
    ws_id = _upload(client).json()["workspace"]["id"]
    q = client.get(f"/api/v2/workspaces/{ws_id}/quotas").json()
    q["band_quotas"]["6-8"]["ENG"] = -3
    r = client.put(f"/api/v2/workspaces/{ws_id}/quotas", json=q)
    assert r.status_code == 400 and "whole number" in r.json()["detail"]


def test_stop_keeps_best_solution(client):
    ws_id = _upload(client).json()["workspace"]["id"]
    job = client.post(f"/api/v2/workspaces/{ws_id}/solve", json={"seconds": 120}).json()
    client.post(f"/api/v2/jobs/{job['id']}/stop")
    done = _wait(client, job["id"], timeout=30)
    assert done["state"] in ("done", "failed")
    assert done["elapsed"] < 30


@pytest.mark.parametrize("content,msg", [(b"hello", "doesn't look like an .xlsx"), (b"", "No file")])
def test_bad_uploads(client, content, msg):
    r = client.post("/api/v2/workspaces?filename=x.xlsx", content=content,
                    headers={"Content-Type": "application/octet-stream"})
    assert r.status_code == 400 and msg in r.json()["detail"]


def test_wrong_columns(client):
    from openpyxl import Workbook
    wb = Workbook()
    wb.active.append(["Name", "Age"])
    wb.active.append(["x", 1])
    buf = io.BytesIO()
    wb.save(buf)
    r = _upload(client, buf.getvalue())
    assert r.status_code == 400 and "missing a 'class' column" in r.json()["detail"]


def test_unknown_and_malformed_workspace_ids(client):
    assert client.get("/api/v2/workspaces/" + "0" * 32).status_code == 404
    assert client.get("/api/v2/workspaces/../../etc").status_code == 404


def test_delete(client):
    ws_id = _upload(client).json()["workspace"]["id"]
    assert client.delete(f"/api/v2/workspaces/{ws_id}").json()["deleted"]
    assert client.get(f"/api/v2/workspaces/{ws_id}").status_code == 404


def test_new_app_is_served(client):
    r = client.get("/")
    assert r.status_code == 200 and "Timetable Studio" in r.text
    assert client.get("/classic/").status_code == 200
