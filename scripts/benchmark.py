"""Benchmark the pipeline on planted schools of growing size and on the real school.

    venv/bin/python scripts/benchmark.py              # 30 s per instance
    venv/bin/python scripts/benchmark.py --seconds 60 --out article/benchmarks.md

Every row is independently verified; "forced doubles" is the minimum number of
same-subject-twice-a-day occurrences implied by the quotas (a lesson with 8
periods in a 6-day week must double up at least twice), so it shows how close the
soft-constraint optimisation got to the best possible.
"""
import argparse
import os
import sys
from collections import Counter

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, ROOT)

from backend.optimizer.engine import generate_timetable, load_rules  # noqa: E402
from backend.testing.synthetic import generate  # noqa: E402


def forced_doubles(school, rules):
    D = len(school["days"])
    double_labs = rules.get("class_rules", {}).get("lab_practicals_as_double_period")
    return sum(max(0, l["periods_per_week"] - D) for l in school["lessons"]
               if not (double_labs and l.get("lab") and l["periods_per_week"] % 2 == 0) and not l.get("fixed_period"))


def row(name, school, rules, secs):
    res = generate_timetable(school, rules=rules, time_limit=secs)
    m = res["metadata"]
    load = Counter()
    for l in school["lessons"]:
        for t in l["teacher_ids"]:
            load[t] += l["periods_per_week"]
    solvable = [c for c in school["classes"] if not c.get("self_contained")]
    base = [name, len(solvable), len(load), len(school["lessons"]), max(load.values())]
    if not res["success"]:
        return base + [res["status"], "-", "-", "-", "-", "-", "-"]
    q = m["quality"]
    return base + [
        res["status"], f"{m['first_solution_seconds']:.1f}s", f"{m['solve_duration_seconds']:.0f}s",
        "yes" if m["verified"] else "NO", q["free_class_periods"],
        f"{q['same_subject_twice_a_day']} / {forced_doubles(school, rules)}", q["teacher_days_over_max_run"],
    ]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--seconds", type=float, default=30)
    ap.add_argument("--out", default=None, help="also write the markdown table to this file")
    args = ap.parse_args()
    rules = load_rules()

    cases = [(f"planted {g}x{s}", dict(grades=g, sections=s, seed=0, teacher_cap=cap))
             for g, s, cap in [(2, 2, 36), (4, 4, 40), (8, 4, 42), (12, 4, 44)]]
    header = ["instance", "classes", "teachers", "lessons", "max load", "status", "first solution",
              "time", "verified", "free periods", "doubles / forced", "long runs"]
    rows = []
    for name, kw in cases:
        print(f"running {name} ...", flush=True)
        rows.append(row(name, generate(**kw), rules, args.seconds))

    excel = os.path.join(ROOT, "config", "SHIKHA_CLASSWISE.xlsx")
    if os.path.exists(excel):
        from backend.ingest.excel_loader import load_school_from_excel
        print("running real school ...", flush=True)
        rows.append(row("real school", load_school_from_excel(excel), rules, args.seconds))

    md = ["| " + " | ".join(header) + " |", "|" + "---|" * len(header)]
    md += ["| " + " | ".join(str(x) for x in r) + " |" for r in rows]
    print("\n".join(md))
    if args.out:
        with open(args.out, "w") as f:
            f.write(f"Benchmark ({args.seconds:.0f}s limit per instance)\n\n" + "\n".join(md) + "\n")


if __name__ == "__main__":
    main()
