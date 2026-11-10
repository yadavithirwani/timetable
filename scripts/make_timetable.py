"""One command from the school's Excel export to a printable timetable workbook.

    venv/bin/python scripts/make_timetable.py                       # uses config/SHIKHA_CLASSWISE.xlsx
    venv/bin/python scripts/make_timetable.py --excel other.xlsx --seconds 120

Steps: read Excel -> apply period quotas -> sanity-check -> solve -> independently
verify -> write output/timetable.xlsx (+ refresh config/my_school_config.json and
backend/data/saved_timetable.json so the web app shows the same timetable).
"""
import argparse
import json
import os
import sys

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, ROOT)

from backend.export.excel_export import export  # noqa: E402
from backend.ingest.excel_loader import DEFAULT_EXCEL, DEFAULT_QUOTAS, load_school_from_excel  # noqa: E402
from backend.optimizer.engine import generate_timetable, load_rules  # noqa: E402


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--excel", default=DEFAULT_EXCEL)
    ap.add_argument("--quotas", default=DEFAULT_QUOTAS)
    ap.add_argument("--seconds", type=float, default=None, help="optimisation time (default from rules.json)")
    ap.add_argument("--out", default=os.path.join(ROOT, "output", "timetable.xlsx"))
    args = ap.parse_args()

    print(f"1/4 Reading {os.path.basename(args.excel)} ...")
    school = load_school_from_excel(args.excel, args.quotas)
    solvable = [c for c in school["classes"] if not c.get("self_contained")]
    print(f"    {len(school['classes'])} classes ({len(solvable)} to schedule), "
          f"{len(school['teachers'])} teachers, {len(school['lessons'])} lessons")
    for w in school["import_warnings"]:
        print("    ! " + w)
    with open(os.path.join(ROOT, "config", "my_school_config.json"), "w") as f:
        json.dump(school, f, indent=2)

    rules = load_rules()
    secs = args.seconds or rules.get("solver", {}).get("time_limit_seconds", 60)
    print(f"2/4 Checking data and building timetable (up to {secs:.0f}s) ...")
    res = generate_timetable(school, rules=rules, time_limit=secs)

    lines = [f"Status: {res['status']}"]
    if not res["success"]:
        print("\nCould not build a timetable. Reasons:")
        for v in res["violations"]:
            print("  - " + v)
        sys.exit(1)

    q = res["metadata"]["quality"]
    print("3/4 Independent check: " + ("PASSED - no clashes, all quotas met" if res["metadata"]["verified"]
                                       else "FAILED"))
    lines += [
        "Independent verification: PASSED (no teacher/class/room clashes, every lesson has its exact periods,",
        "  class teacher in period 1 every day, joint electives in sync, lab practicals as double periods)",
        "",
        f"Solve time: {res['metadata']['solve_duration_seconds']}s",
        f"Free class periods: {q['free_class_periods']}",
        f"Same subject twice in a day: {q['same_subject_twice_a_day']}",
        f"Teacher-days with more than {rules['teacher_rules'].get('max_consecutive_periods', '-')} periods in a row: "
        f"{q['teacher_days_over_max_run']}",
        f"Main subjects after period {rules['class_rules'].get('heavy_subjects_not_after_period')}: "
        f"{q['heavy_subject_periods_late']}",
        "",
        "Assumptions to confirm (edit config/period_quotas.json and re-run):",
        "  - periods per week per subject come from config/period_quotas.json, not from the Excel",
        "  - multi-teacher rows mean all listed teachers are present; identical rows across sections of a",
        "    grade are taught together (joint lesson)",
    ] + [f"  - {w}" for w in res.get("warnings", [])]
    for l in lines[2:9]:
        print("    " + l)

    os.makedirs(os.path.dirname(args.out), exist_ok=True)
    export(school, res, args.out, lines)
    with open(os.path.join(ROOT, "backend", "data", "saved_timetable.json"), "w") as f:
        json.dump(res, f)
    print(f"4/4 Saved {args.out}")


if __name__ == "__main__":
    main()
