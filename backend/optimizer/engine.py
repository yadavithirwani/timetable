"""Public entry point: diagnose -> solve -> verify.

    result = generate_timetable(school_data, locked_assignments=None)

Every returned timetable has been re-checked by an independent verifier
(backend/optimizer/verify.py). If the verifier finds a problem the result is
marked unsuccessful rather than silently shipping a broken timetable.
"""
from __future__ import annotations

import json
import os
from typing import Any, Callable, Dict, List, Optional

from backend.optimizer import diagnostics
from backend.optimizer.solver import solve
from backend.optimizer.verify import verify

RULES_PATH = os.path.join(os.path.dirname(__file__), "..", "..", "config", "rules.json")


def load_rules(path: str = RULES_PATH) -> Dict[str, Any]:
    try:
        with open(path) as f:
            return json.load(f)
    except FileNotFoundError:
        return {}


def generate_timetable(school_data: Dict[str, Any], locked_assignments: Optional[List[Dict]] = None,
                       rules: Optional[Dict[str, Any]] = None, time_limit: Optional[float] = None,
                       on_phase: Optional[Callable[[str], None]] = None, on_progress=None,
                       should_stop=None, forbidden: Optional[List[Dict]] = None,
                       baseline: Optional[Dict[str, List]] = None, stability_weight: int = 100) -> Dict[str, Any]:
    rules = load_rules() if rules is None else rules
    phase = on_phase or (lambda _p: None)

    phase("checking")
    diag = diagnostics.check(school_data, rules)
    if diag["errors"]:
        return {"success": False, "status": "INVALID_INPUT", "assignments": [], "score": 0.0,
                "violations": diag["errors"], "warnings": diag["warnings"],
                "metadata": {"stage": "diagnostics"}}

    phase("solving")
    result = solve(school_data, rules, locked=locked_assignments, time_limit=time_limit,
                   on_progress=on_progress, should_stop=should_stop, forbidden=forbidden,
                   baseline=baseline, stability_weight=stability_weight)
    result["warnings"] = diag["warnings"] + school_data.get("import_warnings", [])
    if not result["success"]:
        return result

    phase("verifying")
    check = verify(school_data, result["assignments"], rules, locked_assignments, forbidden)
    result["metadata"]["quality"] = check["quality"]
    result["metadata"]["verified"] = check["valid"]
    result["metadata"]["total_assignments"] = len(result["assignments"])
    if not check["valid"]:
        result["success"] = False
        result["status"] = "VERIFICATION_FAILED"
        result["violations"] = check["violations"]
    return result
