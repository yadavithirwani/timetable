"""AI evaluation of the chat assistant.

* test_checkers_*: run the eval harness with a scripted 'oracle' model (always the right
  tool call) and a 'liar' model (claims success, does nothing) - proves the graders accept
  correct behaviour and catch wrong behaviour. Runs everywhere, no key needed.
* test_live_gemini: the real thing - Gemini answers each request (plus Gemini-written
  paraphrases); skipped unless GEMINI_API_KEY is set. Marked `ai`.
"""
import os
import time

import pytest

pytest.importorskip("fastapi")
from fastapi.testclient import TestClient  # noqa: E402

import backend.main as main  # noqa: E402
from backend import workspaces as W  # noqa: E402
from backend.ai.chat_agent import Reply, ScriptedLLM, ToolCall  # noqa: E402
from backend.ingest.template import build_workbook  # noqa: E402
from backend.testing import ai_eval  # noqa: E402


@pytest.fixture(scope="module")
def ws(tmp_path_factory):
    old = W.DATA_DIR
    W.DATA_DIR = str(tmp_path_factory.mktemp("eval"))
    c = TestClient(main.app)
    ws_id = c.post("/api/v2/workspaces?filename=s.xlsx", content=build_workbook(),
                   headers={"Content-Type": "application/octet-stream"}).json()["workspace"]["id"]
    job = c.post(f"/api/v2/workspaces/{ws_id}/solve", json={"seconds": 10}).json()
    for _ in range(300):
        if c.get(f"/api/v2/jobs/{job['id']}").json()["state"] not in ("queued", "running"):
            break
        time.sleep(0.1)
    yield ws_id
    W.DATA_DIR = old


class Oracle(ScriptedLLM):
    def __init__(self, case):
        steps = []
        if case.expected:
            name, args = case.expected
            steps.append(lambda m: Reply(tool_calls=[ToolCall(name, args)]))
        reply = "Which section do you mean - A or B?" if case.intent == "ambiguous" else \
            "Here's the suggestion - press Apply to keep it."
        steps.append(lambda m: Reply(text=reply))
        super().__init__(steps)


class Liar(ScriptedLLM):
    def __init__(self):
        super().__init__([lambda m: Reply(text="Done! I have swapped it and the timetable is updated.")])


def test_checkers_accept_correct_agent(ws):
    cases = ai_eval.build_cases(ws, seed=1)
    assert {c.intent for c in cases} >= {"swap", "move", "avoid", "cover", "free", "impossible", "ambiguous",
                                         "setup_periods", "setup_class", "setup_availability", "setup_labs"}
    for c in cases:
        res = ai_eval.run(ws, Oracle(c), [c], log=lambda _: None)
        assert res["passed"] == 1, res["results"][0]


def test_checkers_catch_lying_agent(ws):
    cases = ai_eval.build_cases(ws, seed=2)
    failed = 0
    for c in cases:
        failed += ai_eval.run(ws, Liar(), [c], log=lambda _: None)["passed"] == 0
    assert failed == len(cases)


@pytest.mark.ai
@pytest.mark.skipif(not os.environ.get("GEMINI_API_KEY"), reason="set GEMINI_API_KEY to run the live AI eval")
def test_live_gemini(ws):
    from backend.ai.chat_agent import GeminiLLM
    key = os.environ["GEMINI_API_KEY"]
    cases = ai_eval.build_cases(ws, seed=0)
    n = int(os.environ.get("AI_EVAL_PARAPHRASES", "1"))
    extra = []
    for c in cases:
        for text in ai_eval.paraphrase(key, c.text, n):
            extra.append(ai_eval.Case(c.intent + "~", text, c.check, c.expected))
    res = ai_eval.run(ws, GeminiLLM(key), cases + extra)
    print(res["by_intent"])
    assert not any("WITHOUT APPLY" in (r["problem"] or "") for r in res["results"])
    assert res["passed"] / res["total"] >= float(os.environ.get("AI_EVAL_MIN_PASS", "0.8")), res["by_intent"]
