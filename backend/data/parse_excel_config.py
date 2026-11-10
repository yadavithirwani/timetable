"""Kept for backwards compatibility - the importer now lives in backend/ingest/excel_loader.py.

The previous version of this file padded every class with a fictional "SELF STUDY"
teacher (41% of all periods on the real data) and treated multi-teacher rows as
"either teacher", which produced timetables that looked valid but weren't usable.
"""
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

from backend.ingest.excel_loader import main  # noqa: E402


def parse_excel_schedules():
    main([])


if __name__ == "__main__":
    main()
