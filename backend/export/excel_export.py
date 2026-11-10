"""Write a generated timetable to a printable Excel workbook.

Sheets: Report (checks + quality), Classes (one grid per class), Teachers (one grid per teacher).
"""
from __future__ import annotations

from collections import defaultdict
from typing import Any, Dict, List

from openpyxl import Workbook
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side

THIN = Side(style="thin", color="999999")
BOX = Border(left=THIN, right=THIN, top=THIN, bottom=THIN)
HEAD = PatternFill("solid", fgColor="DDE3EA")
WRAP = Alignment(wrap_text=True, vertical="center", horizontal="center")


def _grid(ws, row, title, days, P, cells):
    ws.cell(row=row, column=1, value=title).font = Font(bold=True, size=13)
    row += 1
    ws.cell(row=row, column=1, value="Day").fill = HEAD
    for p in range(1, P + 1):
        c = ws.cell(row=row, column=p + 1, value=f"P{p}")
        c.fill, c.font, c.alignment, c.border = HEAD, Font(bold=True), WRAP, BOX
    for d in days:
        row += 1
        c = ws.cell(row=row, column=1, value=d)
        c.fill, c.font, c.border = HEAD, Font(bold=True), BOX
        for p in range(1, P + 1):
            c = ws.cell(row=row, column=p + 1, value=cells.get((d, p), ""))
            c.alignment, c.border = WRAP, BOX
        ws.row_dimensions[row].height = 42
    return row + 2


def export(school: Dict[str, Any], result: Dict[str, Any], path: str, report_lines: List[str] = None) -> str:
    days, P = school["days"], school["periods_per_day"]
    subj = {s["id"]: s["name"] for s in school["subjects"]}
    tname = {t["id"]: t["name"].title() for t in school["teachers"]}
    cname = {c["id"]: c["name"] for c in school["classes"]}

    by_class = defaultdict(lambda: defaultdict(set))
    by_teacher = defaultdict(lambda: defaultdict(set))
    lesson_classes = defaultdict(set)
    for a in result["assignments"]:
        lesson_classes[a["lesson_id"], a["day"], a["period"]].add(a["class_id"])
    for a in result["assignments"]:
        key = (a["day"], a["period"])
        by_class[a["class_id"]][key].add((subj.get(a["subject_id"], a["subject_id"]), tname.get(a["teacher_id"], "")))
        classes = " + ".join(sorted(cname[c] for c in lesson_classes[a["lesson_id"], a["day"], a["period"]]))
        by_teacher[a["teacher_id"]][key].add((subj.get(a["subject_id"], a["subject_id"]), classes))

    def fmt(entries):
        out = {}
        for k, items in entries.items():
            s = sorted({x for x, _ in items})
            who = sorted({y for _, y in items if y})
            out[k] = " / ".join(s) + ("\n" + ", ".join(who) if who else "")
        return out

    wb = Workbook()
    rep = wb.active
    rep.title = "Report"
    rep.column_dimensions["A"].width = 110
    for i, line in enumerate(report_lines or [], start=1):
        rep.cell(row=i, column=1, value=line)

    ws = wb.create_sheet("Classes")
    ws.column_dimensions["A"].width = 12
    for p in range(1, P + 1):
        ws.column_dimensions[chr(ord("A") + p)].width = 20
    row = 1
    for c in sorted(school["classes"], key=lambda c: (c["grade"], c["section"])):
        row = _grid(ws, row, f"Class {c['name']}", days, P, fmt(by_class[c["id"]]))

    ws = wb.create_sheet("Teachers")
    ws.column_dimensions["A"].width = 12
    for p in range(1, P + 1):
        ws.column_dimensions[chr(ord("A") + p)].width = 20
    row = 1
    for t in sorted(school["teachers"], key=lambda t: t["name"]):
        if t["id"] in by_teacher:
            n = sum(1 for _ in by_teacher[t["id"]])
            row = _grid(ws, row, f"{t['name'].title()}  ({n} periods/week)", days, P, fmt(by_teacher[t["id"]]))

    wb.save(path)
    return path
