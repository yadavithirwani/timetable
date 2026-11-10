"""Build a small, fictional school in the same layout as the real 'Contracts Classes' export.

Layout (first sheet):
    Class | Teacher | Subject
    6 A   |         |               <- class name on its own row starts a block
          | ASHA MEHTA | ENG
          | ASHA MEHTA,RAVI DAS | DANCE/YOGA   <- several teachers = all of them present
          ...
          (blank row between classes)

Used for the "download template" and "try a demo school" buttons, so the
deployed app never needs real staff names.
"""
from __future__ import annotations

import io
from typing import List, Tuple

from openpyxl import Workbook
from openpyxl.styles import Font, PatternFill

# subject -> teachers by section pair; quotas come from the 6-8 band of period_quotas.json
GRADES = [6, 7, 8]
SECTIONS = ["A", "B"]
SPECIALISTS = {
    "ENG": ["ANITA SHARMA", "KAVYA NAIR", "ROHIT BANSAL"],
    "MATHS": ["VIKRAM RAO", "MEERA IYER", "SAHIL GUPTA"],
    "SCIENCE": ["NEHA KAPOOR", "ARJUN MALIK", "POOJA VERMA"],
    "SST": ["DIVYA SETHI", "KARAN MEHRA"],
    "HINDI": ["SUNITA JOSHI", "PRIYA MISHRA"],
    "PUNJABI": ["GURPREET SANDHU"],
    "KB": ["SIMRAN KAUR"],
    "COMPUTERS": ["NIKHIL ARORA"],
    "COMPUTERS-PR": ["NIKHIL ARORA"],
    "LIFESKILLS/GK": ["RITU CHAWLA"],
    "MUSIC": ["AMAN BHATIA"],
    "ART": ["TANYA DUTTA"],
    "LIBRARY": ["SEEMA KHANNA"],
    "PHY EDU": ["RAJESH YADAV"],
}
CLASS_TEACHERS = {  # class teacher also teaches one main subject of their class
    "6 A": ("ANITA SHARMA", "ENG"), "6 B": ("VIKRAM RAO", "MATHS"),
    "7 A": ("NEHA KAPOOR", "SCIENCE"), "7 B": ("KAVYA NAIR", "ENG"),
    "8 A": ("MEERA IYER", "MATHS"), "8 B": ("ARJUN MALIK", "SCIENCE"),
}
DANCE_YOGA = ["ISHA MALHOTRA", "DEEPAK SAINI"]  # joint lesson for A + B of each grade
NURSERY = [("NURSERY A", "LATA BHALLA"), ("NURSERY B", "MANSI GROVER")]


def demo_rows() -> List[Tuple[str, List[Tuple[str, str]]]]:
    blocks = []
    for gi, g in enumerate(GRADES):
        for si, s in enumerate(SECTIONS):
            cname = f"{g} {s}"
            ct, ct_subject = CLASS_TEACHERS[cname]
            lines = [(ct, "CLASS TEACHER")]
            for subject, teachers in SPECIALISTS.items():
                if subject == ct_subject:
                    t = ct
                else:
                    t = teachers[(gi * len(SECTIONS) + si) % len(teachers)]
                lines.append((t, subject))
            lines.append((",".join(DANCE_YOGA), "DANCE/YOGA"))
            blocks.append((cname, lines))
    for cname, t in NURSERY:
        blocks.append((cname, [(t, "CLASS TEACHER")]))
    return blocks


def build_workbook(blocks=None) -> bytes:
    blocks = blocks if blocks is not None else demo_rows()
    wb = Workbook()
    ws = wb.active
    ws.title = "Contracts Classes"
    ws.append(["Class", "Teacher", "Subject"])
    for cell in ws[1]:
        cell.font = Font(bold=True)
        cell.fill = PatternFill("solid", fgColor="DDE3EA")
    for cname, lines in blocks:
        ws.append([cname, None, None])
        ws.cell(row=ws.max_row, column=1).font = Font(bold=True)
        for teacher, subject in lines:
            ws.append([None, teacher, subject])
        ws.append([None, None, None])
    ws.column_dimensions["A"].width = 16
    ws.column_dimensions["B"].width = 40
    ws.column_dimensions["C"].width = 20
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()
