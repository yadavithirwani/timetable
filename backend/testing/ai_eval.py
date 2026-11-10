"""Evaluate the chat assistant with a real LLM.

Unit tests (tests/test_chat_agent.py) prove the plumbing with a scripted fake model.
This checks the part a fake can't: does a real model turn the way a busy timetable
in-charge actually types ("9a ka mon 2nd aur wed 4th swap kar do") into the right
tool calls?

Cases are built from the solved timetable itself (real slots, subjects, teachers), so
every check has a known right answer and is graded by code, never by another LLM:

* swap / move / avoid -> the proposed timetable (not yet applied) has the change
* cover               -> plan_cover was called for the right teacher and day
* free-teacher query  -> find_free_teachers was called for the right slot
* impossible request  -> no proposal, and the reply doesn't pretend it worked
* ambiguous class     -> no proposal; the model asks which class
* every case          -> the saved timetable is unchanged (only Apply changes it)

The LLM is also used to *generate* extra paraphrases of each request (messy,
abbreviated, Hinglish), which are graded by the same checkers.

    GEMINI_API_KEY=... venv/bin/python scripts/ai_eval.py --paraphrases 3
"""
from __future__ import annotations

import json
import random
import re
import time
from dataclasses import dataclass
from typing import Callable, Dict, List, Optional

from backend import workspaces as W
from backend.ai import chat_agent as A

CLAIMS_DONE = re.compile(r"\b(has been|have been|is now|i've|i have)\s+(applied|saved|updated|changed|made|swapped|moved)\b", re.I)


@dataclass
class Case:
    intent: str
    text: str
    check: Callable[[Dict], Optional[str]]   # returns None if OK, else what went wrong
    expected: Optional[tuple] = None          # (tool, args) a correct agent would call; used to test the checkers


def _names(s):
    return ({x["id"]: x["name"] for x in s["subjects"]}, {t["id"]: t["name"] for t in s["teachers"]},
            {c["id"]: c["name"] for c in s["classes"]})


def _proposed(out) -> Optional[List[Dict]]:
    p = out.get("proposal")
    if not p or not p.get("ok"):
        return None
    return W._proposals[p["proposal_id"]]["timetable"]["assignments"]


def _subject_at(asg, cid, day, period):
    return next((a["subject_id"] for a in asg if a["class_id"] == cid and a["day"] == day and a["period"] == period), None)


def _ordinal(n):
    return {1: "1st", 2: "2nd", 3: "3rd"}.get(n, f"{n}th")


def build_cases(ws_id: str, seed: int = 0) -> List[Case]:
    rng = random.Random(seed)
    s, asg = W.school(ws_id), W.timetable(ws_id)["assignments"]
    subj, tname, cname = _names(s)
    lessons = {l["id"]: l for l in s["lessons"]}
    asg = [a for a in asg if a["lesson_id"] in lessons]   # skip self-contained (HOME_) classes
    days = s["days"]
    movable = [a for a in asg if not lessons[a["lesson_id"]].get("fixed_period") and not lessons[a["lesson_id"]].get("lab")
               and len(lessons[a["lesson_id"]]["class_ids"]) == 1 and subj[a["subject_id"]] != "CT ACTIVITY"]
    cases: List[Case] = []

    # swap
    a = rng.choice(movable)
    b = rng.choice([x for x in movable if x["class_id"] == a["class_id"] and x["subject_id"] != a["subject_id"]
                    and x["day"] != a["day"]])
    cid = a["class_id"]
    sw = ("propose_changes", {"changes": [{"type": "swap", "class": cname[cid], "day1": a["day"], "period1": a["period"],
                                           "day2": b["day"], "period2": b["period"]}]})

    def chk_swap(out, a=a, b=b):
        new = _proposed(out)
        if new is None:
            return f"no valid proposal ({out.get('reply', '')[:120]})"
        if _subject_at(new, a["class_id"], b["day"], b["period"]) != a["subject_id"] or \
                _subject_at(new, a["class_id"], a["day"], a["period"]) != b["subject_id"]:
            return "proposal doesn't swap the two periods"
    cases += [
        Case("swap", f"Swap {cname[cid]}'s {a['day']} period {a['period']} with {b['day']} period {b['period']}", chk_swap, sw),
        Case("swap", f"{cname[cid].replace(' ', '').lower()} - exchange {a['day'][:3].lower()} {_ordinal(a['period'])} and "
                     f"{b['day'][:3].lower()} {_ordinal(b['period'])} pls", chk_swap, sw),
        Case("swap", f"{cname[cid]} ka {a['day']} {_ordinal(a['period'])} period aur {b['day']} {_ordinal(b['period'])} "
                     f"period swap kar do", chk_swap, sw),
    ]

    # move: one period of a subject into a slot it isn't in
    m = rng.choice(movable)
    target = rng.choice([x for x in movable if x["class_id"] == m["class_id"] and x["subject_id"] != m["subject_id"]
                         and x["day"] != m["day"]])

    mv = ("propose_changes", {"changes": [{"type": "move", "class": cname[m["class_id"]], "subject": subj[m["subject_id"]],
                                           "to_day": target["day"], "to_period": target["period"]}]})

    def chk_move(out, m=m, t=target):
        new = _proposed(out)
        if new is None:
            return f"no valid proposal ({out.get('reply', '')[:120]})"
        if _subject_at(new, m["class_id"], t["day"], t["period"]) != m["subject_id"]:
            return "subject not in the requested slot"
    cases += [
        Case("move", f"Put {subj[m['subject_id']].lower()} for {cname[m['class_id']]} on {target['day']} in period "
                     f"{target['period']}", chk_move, mv),
        Case("move", f"can {cname[m['class_id']]} have {subj[m['subject_id']]} {target['day'][:3]} {_ordinal(target['period'])} "
                     f"period instead", chk_move, mv),
    ]

    # avoid: whatever is in a slot shouldn't be there
    v = rng.choice(movable)

    def chk_avoid(out, v=v):
        new = _proposed(out)
        if new is None:
            return f"no valid proposal ({out.get('reply', '')[:120]})"
        if _subject_at(new, v["class_id"], v["day"], v["period"]) == v["subject_id"]:
            return "subject still in that slot"
    cases.append(Case("avoid", f"{cname[v['class_id']]} shouldn't have {subj[v['subject_id']]} in period {v['period']} "
                               f"on {v['day']}", chk_avoid,
                      ("propose_changes", {"changes": [{"type": "avoid", "class": cname[v["class_id"]], "day": v["day"],
                                                        "period": v["period"], "subject": subj[v["subject_id"]]}]})))

    # cover for an absent teacher (must NOT change the timetable)
    busy_day = rng.choice(days)
    t = rng.choice(sorted({x["teacher_id"] for x in asg if x["day"] == busy_day and x["teacher_id"]}))

    def chk_cover(out, t=t, d=busy_day):
        c = out.get("cover")
        if not c:
            return f"plan_cover not used ({[x['tool'] for x in out['tool_log']]})"
        if c["teacher"] != tname[t] or c["day"] != d:
            return f"cover for {c['teacher']} {c['day']}, wanted {tname[t]} {d}"
        if out.get("proposal"):
            return "proposed a timetable change for a one-day absence"
    first = tname[t].split()[0].title()
    cv = ("plan_cover", {"teacher": tname[t], "day": busy_day})
    cases += [
        Case("cover", f"{tname[t].title()} is on leave on {busy_day}. Who can take their classes?", chk_cover, cv),
        Case("cover", f"{first} absent {busy_day[:3].lower()}, arrangement please", chk_cover, cv),
    ]

    # free teachers
    fd, fp = rng.choice(days), rng.randint(2, s["periods_per_day"])

    def chk_free(out, d=fd, p=fp):
        calls = [x for x in out["tool_log"] if x["tool"] == "find_free_teachers"]
        if not calls:
            return f"find_free_teachers not used ({[x['tool'] for x in out['tool_log']]})"
        if int(calls[-1]["args"].get("period", -1)) != p or not str(calls[-1]["args"].get("day", "")).lower().startswith(d[:3].lower()):
            return f"asked about {calls[-1]['args']}"
    cases.append(Case("free", f"which teachers are free {fd} {_ordinal(fp)} period?", chk_free,
                      ("find_free_teachers", {"day": fd, "period": fp})))

    # impossible: the class-teacher period is fixed to period 1
    ci = rng.choice([c for c in s["classes"] if not c.get("self_contained")])

    def chk_impossible(out):
        if _proposed(out) is not None and out["proposal"]["diff"]["moved_periods"] > 0:
            return "made a proposal for an impossible request"
        if CLAIMS_DONE.search(out.get("reply", "")):
            return f"claims success: {out['reply'][:120]}"
    cases.append(Case("impossible", f"Move {ci['name']}'s class teacher period to period 6 on {days[0]}", chk_impossible,
                      ("propose_changes", {"changes": [{"type": "move", "class": ci["name"], "subject": "CLASS TEACHER",
                                                        "to_day": days[0], "to_period": 6}]})))

    # ambiguous class: a grade with several sections
    grades = {}
    for c in s["classes"]:
        if not c.get("self_contained"):
            grades.setdefault(c.get("grade"), []).append(c)
    g = next((k for k, v in grades.items() if len(v) > 1), None)
    if g is not None:
        def chk_ambiguous(out):
            if out.get("proposal"):
                return "guessed a section instead of asking"
            if "?" not in out.get("reply", ""):
                return f"didn't ask which section: {out.get('reply', '')[:120]}"
        cases.append(Case("ambiguous", f"swap class {g} {days[0]} 2nd and {days[1]} 3rd", chk_ambiguous, None))
    return cases


def paraphrase(llm_key: str, text: str, n: int, model: Optional[str] = None) -> List[str]:
    """Ask Gemini for n messy rewrites of a request (keeping every name, day and period)."""
    from google import genai
    from google.genai import types
    client = genai.Client(api_key=llm_key)
    prompt = (f"Rewrite this request to a school timetable assistant {n} different ways, the way a busy Indian school "
              f"timetable coordinator might type it on a phone: short, informal, abbreviations, occasional Hinglish or "
              f"typos. Keep every class, subject, teacher, day and period meaning exactly the same. "
              f"Return a JSON list of strings only.\n\nRequest: {text}")
    r = client.models.generate_content(model=model or "gemini-2.5-flash", contents=prompt,
                                       config=types.GenerateContentConfig(response_mime_type="application/json",
                                                                          temperature=0.9))
    try:
        out = json.loads(r.text)
        return [str(x) for x in out if isinstance(x, str)][:n]
    except Exception:
        return []


def run(ws_id: str, llm: A.LLM, cases: List[Case], log: Callable[[str], None] = print) -> Dict:
    before = W.timetable(ws_id)
    results = []
    for c in cases:
        t0 = time.time()
        try:
            out = A.chat(ws_id, c.text, llm=llm)
            problem = c.check(out)
            if problem is None and CLAIMS_DONE.search(out.get("reply", "")):
                problem = f"claims the change is done: {out['reply'][:120]}"
        except Exception as e:  # provider error etc.
            out, problem = {"reply": "", "tool_log": []}, f"error: {type(e).__name__}: {e}"
        if W.timetable(ws_id) != before:
            problem = (problem or "") + " | SAVED TIMETABLE CHANGED WITHOUT APPLY"
            W._write(ws_id, "timetable.json", before)
        results.append({"intent": c.intent, "text": c.text, "ok": problem is None, "problem": problem,
                        "tools": [x["tool"] for x in out.get("tool_log", [])], "reply": out.get("reply", ""),
                        "seconds": round(time.time() - t0, 1)})
        log(f"{'PASS' if problem is None else 'FAIL'} [{c.intent}] {c.text}" + (f"\n      -> {problem}" if problem else ""))
    by = {}
    for r in results:
        k = by.setdefault(r["intent"], [0, 0])
        k[0] += r["ok"]
        k[1] += 1
    return {"results": results, "by_intent": by, "passed": sum(r["ok"] for r in results), "total": len(results)}
