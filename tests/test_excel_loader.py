import json
import os

import pytest

from backend.ingest.excel_loader import DEFAULT_QUOTAS, build_school, normalise_class_name


@pytest.mark.parametrize("raw,expected", [
    (".9A", ("9 A", 9, "A")),
    (".10 A", ("10 A", 10, "A")),
    ("  12   D ", ("12 D", 12, "D")),
    ("NURSERY B", ("NURSERY B", -1, "B")),
    ("PRE PRIMARY C", ("PRE PRIMARY C", 0, "C")),
    ("nan", None),
])
def test_class_names(raw, expected):
    assert normalise_class_name(raw) == expected


@pytest.fixture
def quotas():
    with open(DEFAULT_QUOTAS) as f:
        return json.load(f)


def _rows(*items):
    return [{"class": c, "teachers": t.split(","), "subject": s} for c, t, s in items]


def test_multi_teacher_row_needs_all_teachers(quotas):
    s = build_school(_rows(("1 A", "PREETI,KRISI", "COMPUTERS"), ("1 A", "PREETI", "CLASS TEACHER")), quotas)
    comp = next(l for l in s["lessons"] if l["subject_id"] == "sub_COMPUTERS")
    assert comp["teacher_ids"] == ["t_KRISI", "t_PREETI"]


def test_joint_elective_merged_across_sections(quotas):
    s = build_school(_rows(
        ("9A", "A,B,C", "VPA"), ("9 B", "C,B,A", "VPA"),     # same set, different order -> joint
        ("9 C", "A,B,D", "VPA"),                               # different set -> separate
        ("10 A", "A,B,C", "VPA"),                              # different grade -> separate
    ), quotas)
    vpa = [l for l in s["lessons"] if l["subject_id"] == "sub_VPA"]
    assert sorted(len(l["class_ids"]) for l in vpa) == [1, 1, 2]
    assert ["c_9A", "c_9B"] in [l["class_ids"] for l in vpa]


def test_single_teacher_same_subject_not_merged(quotas):
    s = build_school(_rows(("11 B", "GURLEEN", "ENG"), ("11 C", "GURLEEN", "ENG")), quotas)
    assert len([l for l in s["lessons"] if l["subject_id"] == "sub_ENG"]) == 2


def test_nursery_is_self_contained(quotas):
    s = build_school(_rows(("NURSERY A", "DIVYA", "CLASS TEACHER")), quotas)
    c = s["classes"][0]
    assert c["self_contained"] and c["class_teacher_id"] == "t_DIVYA"
    assert s["lessons"] == []


def test_gap_filled_by_class_teacher_not_fake_teacher(quotas):
    s = build_school(_rows(("6 A", "RAVI", "CLASS TEACHER"), ("6 A", "ASHA", "ENG")), quotas)
    total = sum(l["periods_per_week"] for l in s["lessons"])
    assert total == 48
    assert all(t["id"] != "t_SELFSTUDY" for t in s["teachers"])
    act = next(l for l in s["lessons"] if l["subject_id"] == "sub_CTACTIVITY")
    assert act["teacher_ids"] == ["t_RAVI"]


def test_missing_quota_warns(quotas):
    s = build_school(_rows(("6 A", "RAVI", "CLASS TEACHER"), ("6 A", "ASHA", "UNDERWATER BASKETS")), quotas)
    assert any("UNDERWATER BASKETS" in w for w in s["import_warnings"])


def test_class_teacher_pinned_to_period_one(quotas):
    s = build_school(_rows(("6 A", "RAVI", "CLASS TEACHER"), ("6 A", "ASHA", "ENG")), quotas)
    ct = next(l for l in s["lessons"] if l["subject_id"] == "sub_CLASSTEACHER")
    assert ct["fixed_period"] == 1 and ct["periods_per_week"] == 6


EXCEL = os.path.join(os.path.dirname(__file__), "..", "config", "SHIKHA_CLASSWISE.xlsx")


@pytest.mark.skipif(not os.path.exists(EXCEL), reason="real school Excel not present")
def test_real_excel_loads_cleanly():
    from backend.ingest.excel_loader import load_school_from_excel
    s = load_school_from_excel(EXCEL)
    assert len(s["classes"]) == 56
    assert s["import_warnings"] == []
    loads = {}
    for l in s["lessons"]:
        for c in l["class_ids"]:
            loads[c] = loads.get(c, 0) + l["periods_per_week"]
    assert set(loads.values()) == {48}
