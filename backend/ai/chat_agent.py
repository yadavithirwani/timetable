"""Chat assistant that edits a timetable through a few safe tools.

The language model never touches the timetable. It can only:

* look things up (``show_timetable``, ``find_free_teachers``, ``plan_cover``), and
* ``propose_changes`` - a list of move / swap / avoid / teacher_unavailable requests
  that go to the solver (backend/editing.py). The solver finds the smallest change
  that keeps every school rule, the verifier re-checks it, and the user sees a
  preview with Apply / Discard. Nothing is saved until they click Apply.

The model provider sits behind a tiny interface (``LLM.chat``) so tests can drive
the agent with a scripted fake and the real one can be Gemini (or anything else).
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional

from backend import editing
from backend import setup_edit
from backend import workspaces as W

MAX_STEPS = 6


# --------------------------------------------------------------------------- provider interface
@dataclass
class ToolCall:
    name: str
    args: Dict[str, Any]
    id: Optional[str] = None


@dataclass
class Reply:
    text: str = ""
    tool_calls: List[ToolCall] = field(default_factory=list)
    raw: Any = None   # provider-specific content, sent back verbatim inside one turn


class LLM:
    """messages: [{"role": "user"|"assistant", "text": ...} | {"role": "tool", "name", "id", "result"}
    | {"role": "assistant", "reply": Reply}]"""

    def chat(self, system: str, messages: List[Dict], tools: List[Dict]) -> Reply:  # pragma: no cover
        raise NotImplementedError


class GeminiLLM(LLM):
    def __init__(self, api_key: str, model: Optional[str] = None):
        from google import genai
        self.client = genai.Client(api_key=api_key)
        self.model = model or os.environ.get("GEMINI_MODEL", "gemini-2.5-flash")

    def chat(self, system, messages, tools):
        from google.genai import types
        contents = []
        for m in messages:
            if m["role"] == "user":
                contents.append(types.Content(role="user", parts=[types.Part(text=m["text"])]))
            elif m["role"] == "assistant" and m.get("reply") is not None and m["reply"].raw is not None:
                contents.append(m["reply"].raw)
            elif m["role"] == "assistant":
                contents.append(types.Content(role="model", parts=[types.Part(text=m.get("text") or "")]))
            elif m["role"] == "tool":
                part = types.Part.from_function_response(name=m["name"], response={"result": m["result"]})
                if m.get("id"):
                    part.function_response.id = m["id"]
                if contents and contents[-1].role == "user" and contents[-1].parts and \
                        contents[-1].parts[0].function_response is not None:
                    contents[-1].parts.append(part)   # parallel calls answered together
                else:
                    contents.append(types.Content(role="user", parts=[part]))
        cfg = types.GenerateContentConfig(
            system_instruction=system,
            tools=[types.Tool(function_declarations=[
                types.FunctionDeclaration(name=t["name"], description=t["description"],
                                          parameters_json_schema=t["parameters"]) for t in tools])],
            automatic_function_calling=types.AutomaticFunctionCallingConfig(disable=True),
            temperature=0.2,
        )
        resp = self.client.models.generate_content(model=self.model, contents=contents, config=cfg)
        cand = resp.candidates[0].content if resp.candidates else None
        calls = [ToolCall(name=fc.name, args=dict(fc.args or {}), id=fc.id) for fc in (resp.function_calls or [])]
        text = "".join(p.text for p in (cand.parts if cand and cand.parts else []) if p.text and not p.thought)
        return Reply(text=text, tool_calls=calls, raw=cand)


class ScriptedLLM(LLM):
    """Test double: each step is a function (messages) -> Reply, consumed in order."""

    def __init__(self, steps: List[Callable[[List[Dict]], Reply]]):
        self.steps = list(steps)
        self.seen: List[List[Dict]] = []

    def chat(self, system, messages, tools):
        self.seen.append(list(messages))
        if not self.steps:
            return Reply(text="(no more scripted replies)")
        return self.steps.pop(0)(messages)


def default_llm(api_key: Optional[str] = None) -> Optional[LLM]:
    key = api_key or os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY")
    return GeminiLLM(key) if key else None


# --------------------------------------------------------------------------- tools
CHANGE_SCHEMA = {
    "type": "object",
    "properties": {
        "type": {"type": "string", "enum": ["move", "swap", "avoid", "teacher_unavailable"]},
        "class": {"type": "string", "description": "Class name, e.g. '9 A'"},
        "subject": {"type": "string"},
        "teacher": {"type": "string"},
        "to_day": {"type": "string"}, "to_period": {"type": "integer"},
        "from_day": {"type": "string"}, "from_period": {"type": "integer"},
        "day1": {"type": "string"}, "period1": {"type": "integer"},
        "day2": {"type": "string"}, "period2": {"type": "integer"},
        "day": {"type": "string"}, "period": {"type": "integer"},
        "periods": {"type": "array", "items": {"type": "integer"}},
    },
    "required": ["type"],
}

TOOLS = [
    {"name": "show_timetable",
     "description": "Show the current timetable of one class or one teacher (optionally a single day). "
                    "Use it to see what is where before proposing a change.",
     "parameters": {"type": "object", "properties": {
         "kind": {"type": "string", "enum": ["class", "teacher"]},
         "name": {"type": "string"}, "day": {"type": "string"}}, "required": ["kind", "name"]}},
    {"name": "find_free_teachers",
     "description": "Teachers with no lesson at a given day and period, least busy first. "
                    "Optionally only those who teach a subject.",
     "parameters": {"type": "object", "properties": {
         "day": {"type": "string"}, "period": {"type": "integer"}, "subject": {"type": "string"}},
         "required": ["day", "period"]}},
    {"name": "plan_cover",
     "description": "A teacher is absent (today / one day): suggest a substitute for each of their periods. "
                    "Does NOT change the timetable - use this for day-to-day absences.",
     "parameters": {"type": "object", "properties": {
         "teacher": {"type": "string"}, "day": {"type": "string"},
         "periods": {"type": "array", "items": {"type": "integer"}}}, "required": ["teacher", "day"]}},
    {"name": "propose_changes",
     "description": (
         "Ask the solver to change the timetable. It moves as little as possible and keeps every school rule; "
         "the user then sees a preview and decides to Apply or Discard. Put all changes for one request in one call. "
         "Change types: "
         "move {class, subject, to_day, to_period, optional from_day/from_period, optional teacher} - put one "
         "period of the subject in that slot; "
         "swap {class, day1, period1, day2, period2} - exchange two periods of one class; "
         "avoid {class, day, period, optional subject} - keep that slot free of the subject (or of whatever is "
         "there now); "
         "teacher_unavailable {teacher, day, optional periods} - permanently keep the teacher free then "
         "(every week, the timetable is rearranged)."),
     "parameters": {"type": "object", "properties": {
         "changes": {"type": "array", "items": CHANGE_SCHEMA}}, "required": ["changes"]}},
    {"name": "show_setup",
     "description": "Read the school setup: topic 'periods' (periods per week per grade group, optionally name = a "
                    "grade like '7' or group like '6-8'), 'class' (one class's periods, name = class), 'teacher' "
                    "(when a teacher is not available), 'labs', or 'week' (days, periods per day, grade groups).",
     "parameters": {"type": "object", "properties": {
         "topic": {"type": "string", "enum": ["periods", "class", "teacher", "labs", "week"]},
         "name": {"type": "string"}}, "required": ["topic"]}},
    {"name": "propose_setup",
     "description": (
         "Change the school SETUP (the data the timetable is built from). Previewed and checked; the user presses "
         "Apply, after which the timetable must be generated again. Change types: "
         "set_periods {subject, periods, grades (a grade or group like '6-8') OR class (one class only)}; "
         "teacher_availability {teacher, day, optional periods (default whole day), available: false to block / "
         "true to free again} - every week; "
         "lab {lab, optional rooms, optional add_subjects, optional remove_subjects, optional remove_lab}; "
         "week {optional days (list of day names), optional periods_per_day}."),
     "parameters": {"type": "object", "properties": {"changes": {"type": "array", "items": {
         "type": "object", "properties": {
             "type": {"type": "string", "enum": ["set_periods", "teacher_availability", "lab", "week"]},
             "subject": {"type": "string"}, "periods": {"description": "number, or list of period numbers"},
             "grades": {"type": "string"}, "class": {"type": "string"}, "teacher": {"type": "string"},
             "day": {"type": "string"}, "available": {"type": "boolean"}, "lab": {"type": "string"},
             "rooms": {"type": "integer"}, "add_subjects": {"type": "array", "items": {"type": "string"}},
             "remove_subjects": {"type": "array", "items": {"type": "string"}}, "remove_lab": {"type": "boolean"},
             "days": {"type": "array", "items": {"type": "string"}}, "periods_per_day": {"type": "integer"}},
         "required": ["type"]}}}, "required": ["changes"]}},
]


def system_prompt(school: Dict) -> str:
    classes = ", ".join(c["name"] for c in school["classes"] if not c.get("self_contained"))
    teachers = ", ".join(sorted(t["name"] for t in school["teachers"]))
    subjects = ", ".join(sorted(x["name"] for x in school["subjects"]))
    return f"""You help a school timetable in-charge adjust the weekly timetable. Be brief and friendly; they are not technical.

Week: {', '.join(school['days'])}; periods 1-{school['periods_per_day']} each day. Period 1 is the class teacher's period.
Classes: {classes}
Teachers: {teachers}
Subjects: {subjects}

How to work:
- Always use the exact class / teacher / subject names above. If a name is ambiguous, ask.
- Look before you change: call show_timetable when the request depends on what is currently where.
- "Swap X and Y" for one class -> one swap change. "Move maths to Friday 4th" -> move. "No games in the last period
  on Friday" -> avoid. "Mrs X can't come on Wednesdays any more" -> teacher_unavailable.
- "X is absent today/tomorrow", "who can take X's classes" -> plan_cover (this does not change the timetable).
- After propose_changes succeeds, summarise what moves in plain words and say they can press Apply to keep it or
  Discard. NEVER say a change has been made or saved - only Apply does that.
- If the solver says a change is impossible, explain the reason simply and suggest an alternative.
- Setup vs timetable: periods per week, labs, the school week and a teacher's regular availability are SETUP
  (propose_setup; the timetable is rebuilt afterwards). Moving or swapping particular periods is a timetable change
  (propose_changes). If a timetable already exists and a teacher can't come on a day from now on, prefer
  propose_changes teacher_unavailable (it keeps the rest of the timetable); use propose_setup when setting things up
  or when they explicitly want the setting changed.
- "Maths should be 7 periods in class 7" is ambiguous between the whole grade group and one class: if they name a
  section (7 A) use class; if they say a grade or group, use grades; otherwise ask.
- After propose_setup, say what changes and any new problems it reports, and that Apply saves it.
- Don't invent periods, teachers or rules. If asked something you can't do with these tools, say so.
"""


@dataclass
class TurnResult:
    reply: str
    proposal: Optional[Dict] = None
    cover: Optional[Dict] = None
    setup: Optional[Dict] = None
    view: Optional[Dict] = None
    tool_log: List[Dict] = field(default_factory=list)


class Agent:
    def __init__(self, ws_id: str, llm: LLM, edit_seconds: float = 20):
        self.ws_id = ws_id
        self.llm = llm
        self.edit_seconds = edit_seconds
        self.school = W.school(ws_id)

    # each tool returns (result for the model, side output for the UI)
    def _tool(self, name: str, args: Dict, out: TurnResult) -> Any:
        s = self.school
        tt = W.timetable(self.ws_id) or {}
        asg = tt.get("assignments", []) if tt.get("success") else []
        try:
            if name == "show_timetable":
                if not asg:
                    return {"error": "There is no timetable yet."}
                kind = args.get("kind", "class")
                if kind == "teacher":
                    t = editing.resolve_teacher(s, args.get("name"))
                    grid, label = editing.teacher_grid(s, asg, t["id"]), t["name"]
                else:
                    c = editing.resolve_class(s, args.get("name"))
                    grid, label = editing.class_grid(s, asg, c["id"]), c["name"]
                if args.get("day"):
                    d = editing.resolve_day(s, args["day"])
                    grid = {d: grid[d]}
                out.view = {"kind": kind, "name": label}
                return {"name": label, "timetable": {d: {f"P{p}": v for p, v in row.items()} for d, row in grid.items()}}
            if name == "find_free_teachers":
                if not asg:
                    return {"error": "There is no timetable yet."}
                d, p = editing.resolve_day(s, args.get("day")), editing.resolve_period(s, args.get("period"))
                sid = editing.resolve_subject(s, args["subject"])["id"] if args.get("subject") else None
                free = editing.free_teachers(s, asg, d, p, sid)
                return {"day": d, "period": p, "free_teachers": free[:15], "total_free": len(free)}
            if name == "plan_cover":
                if not asg:
                    return {"error": "There is no timetable yet."}
                t = editing.resolve_teacher(s, args.get("teacher"))
                d = editing.resolve_day(s, args.get("day"))
                periods = [editing.resolve_period(s, p) for p in args.get("periods") or []] or None
                plan = editing.substitutes(s, asg, t["id"], d, periods)
                out.cover = {"teacher": t["name"], "day": d, "periods": plan}
                return {"teacher": t["name"], "day": d,
                        "cover": [{"period": x["period"], "class": " + ".join(x["classes"]), "subject": x["subject"],
                                   "substitute": (x["suggested"] or {}).get("teacher"),
                                   "other_options": [a["teacher"] for a in x["alternatives"][:3]]} for x in plan]
                        or "They have no lessons that day."}
            if name == "propose_changes":
                changes = args.get("changes") or []
                if isinstance(changes, dict):
                    changes = [changes]
                res = W.propose_edit(self.ws_id, changes, seconds=self.edit_seconds)
                if res.get("ok"):
                    out.proposal = res
                    return {"ok": True, "summary": res["message"], "requests": res["requests"],
                            "changes": [c["text"] for c in res["diff"]["changes"][:25]],
                            "note": "Not applied yet: the user must press Apply."}
                return {"ok": False, "message": res.get("message"), "reasons": res.get("reasons", [])}
            if name == "show_setup":
                return setup_edit.describe(W._read(self.ws_id, "rows.json"), W.get_quotas(self.ws_id), s,
                                           args.get("topic"), args.get("name"))
            if name == "propose_setup":
                changes = args.get("changes") or []
                if isinstance(changes, dict):
                    changes = [changes]
                res = W.propose_setup(self.ws_id, changes)
                if res.get("ok"):
                    out.setup = res
                    return {"ok": True, "changes": res["changes"], "new_problems": res["new_errors"],
                            "problems_fixed": res["fixed_errors"],
                            "note": "Not saved yet: the user must press Apply." +
                                    (" Applying clears the current timetable; it must be generated again."
                                     if res["clears_timetable"] else "")}
                return {"ok": False, "message": res.get("message")}
            return {"error": f"Unknown tool {name}"}
        except editing.EditError as e:
            return {"error": str(e)}

    def run(self, message: str, history: Optional[List[Dict]] = None) -> TurnResult:
        out = TurnResult(reply="")
        msgs: List[Dict] = [{"role": m["role"], "text": m.get("text", "")} for m in (history or [])[-12:]
                            if m.get("role") in ("user", "assistant") and m.get("text")]
        msgs.append({"role": "user", "text": message})
        system = system_prompt(self.school)
        for _ in range(MAX_STEPS):
            r = self.llm.chat(system, msgs, TOOLS)
            if not r.tool_calls:
                out.reply = r.text.strip() or "Done."
                return out
            msgs.append({"role": "assistant", "reply": r, "text": r.text})
            for call in r.tool_calls:
                result = self._tool(call.name, call.args, out)
                out.tool_log.append({"tool": call.name, "args": call.args,
                                     "ok": not (isinstance(result, dict) and (result.get("error") or result.get("ok") is False))})
                msgs.append({"role": "tool", "name": call.name, "id": call.id,
                             "result": json.loads(json.dumps(result, default=str))})
        out.reply = "Sorry, I couldn't finish that. Could you say it a different way?"
        return out


def chat(ws_id: str, message: str, history=None, llm: Optional[LLM] = None, api_key: Optional[str] = None) -> Dict:
    llm = llm or default_llm(api_key)
    if llm is None:
        raise W.WorkspaceError("The assistant needs a Gemini API key. Set GEMINI_API_KEY on the server "
                               "(or add one in the assistant's settings).", 503)
    r = Agent(ws_id, llm).run(message, history)
    return {"reply": r.reply, "proposal": r.proposal, "cover": r.cover, "setup": r.setup, "view": r.view,
            "tool_log": r.tool_log}
