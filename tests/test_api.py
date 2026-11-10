"""The web app's generate endpoint returns a verified timetable (or readable reasons)."""
import json

import pytest

fastapi = pytest.importorskip("fastapi")
from fastapi.testclient import TestClient  # noqa: E402

import backend.main as main  # noqa: E402


@pytest.fixture
def client(tmp_path, monkeypatch, small_school):
    data = tmp_path / "school.json"
    data.write_text(json.dumps(small_school))
    monkeypatch.setattr(main, "DATA_PATH", str(data))
    monkeypatch.setattr(main, "TIMETABLE_SAVE_PATH", str(tmp_path / "saved.json"))
    return TestClient(main.app), data


def test_generate_endpoint(client):
    c, _ = client
    res = c.post("/api/generate", json={"locked_assignments": []}).json()
    assert res["success"], res["violations"][:5]
    assert res["metadata"]["verified"]
    saved = c.get("/api/timetable").json()
    assert len(saved["assignments"]) == len(res["assignments"])


def test_generate_reports_reasons(client):
    c, path = client
    school = json.loads(path.read_text())
    school["lessons"][4]["periods_per_week"] = 60
    path.write_text(json.dumps(school))
    res = c.post("/api/generate", json={}).json()
    assert not res["success"]
    assert res["status"] == "INVALID_INPUT"
    assert res["violations"]


def test_frontend_is_served(client):
    c, _ = client
    r = c.get("/")
    assert r.status_code == 200 and "<html" in r.text.lower()
