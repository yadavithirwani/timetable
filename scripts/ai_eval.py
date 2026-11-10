"""Live evaluation of the timetable chat assistant (needs GEMINI_API_KEY).

    GEMINI_API_KEY=... venv/bin/python scripts/ai_eval.py [--excel config/SHIKHA_CLASSWISE.xlsx]
                                                          [--paraphrases 3] [--seed 0]

Uploads the school into a throwaway workspace, solves it, builds requests from the real
timetable, asks Gemini to write messy paraphrases, runs every request through the
assistant and grades the result with code. Prints a markdown scorecard and writes
output/ai_eval.json. Nothing is ever applied.
"""
import argparse
import json
import os
import sys
import tempfile
import time

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from backend import workspaces as W  # noqa: E402
from backend.ai.chat_agent import GeminiLLM  # noqa: E402
from backend.ingest.template import build_workbook  # noqa: E402
from backend.testing import ai_eval  # noqa: E402


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--excel", help="class-wise .xlsx (default: the built-in sample school)")
    ap.add_argument("--paraphrases", type=int, default=2)
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--solve-seconds", type=float, default=30)
    a = ap.parse_args()
    key = os.environ.get("GEMINI_API_KEY")
    if not key:
        sys.exit("Set GEMINI_API_KEY first.")

    W.DATA_DIR = tempfile.mkdtemp(prefix="tt-eval-")
    content = open(a.excel, "rb").read() if a.excel else build_workbook()
    ws_id = W.create(content, os.path.basename(a.excel or "sample.xlsx"))["workspace"]["id"]
    job = W.start_job(ws_id, a.solve_seconds)
    while W.get_job(job["id"])["state"] in ("queued", "running"):
        time.sleep(0.5)
    if not (W.timetable(ws_id) or {}).get("success"):
        sys.exit(f"Could not solve the school: {W.get_job(job['id'])}")

    cases = ai_eval.build_cases(ws_id, seed=a.seed)
    extra = [ai_eval.Case(c.intent + "~", t, c.check, c.expected)
             for c in cases for t in ai_eval.paraphrase(key, c.text, a.paraphrases)]
    res = ai_eval.run(ws_id, GeminiLLM(key), cases + extra)

    print("\n| Intent | Hand-written | Gemini paraphrases |\n|---|---|---|")
    for intent in sorted({k.rstrip("~") for k in res["by_intent"]}):
        h = res["by_intent"].get(intent, [0, 0])
        p = res["by_intent"].get(intent + "~", [0, 0])
        print(f"| {intent} | {h[0]}/{h[1]} | {p[0]}/{p[1]} |")
    print(f"\n**{res['passed']}/{res['total']} passed** "
          f"(median {sorted(r['seconds'] for r in res['results'])[len(res['results']) // 2]}s per request)")
    os.makedirs("output", exist_ok=True)
    with open("output/ai_eval.json", "w") as f:
        json.dump(res, f, indent=2)


if __name__ == "__main__":
    main()
