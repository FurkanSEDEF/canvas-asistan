"""Validate deliverables against the analysis. Reports what was checked AND what could not be.

Honest by design: keyword coverage is a hint, not proof of correctness; code is only
syntax-checked unless you run it yourself.
"""
from __future__ import annotations

import ast
import json
import re
from pathlib import Path

STOP = set("must should shall need to the a an and or of for with that this use include provide submit".split())


def validate(ws: Path) -> dict:
    analysis = json.loads((ws / "analysis.json").read_text(encoding="utf-8"))
    files = [p for p in (ws / "deliverables").rglob("*") if p.is_file()]
    findings, text_blob = [], ""
    if not files:
        findings.append({"level": "fail", "msg": "No deliverables found in deliverables/."})
    for f in files:
        if f.stat().st_size == 0:
            findings.append({"level": "fail", "msg": f"{f.name} is empty."})
            continue
        if f.suffix == ".py":
            try:
                ast.parse(f.read_text(encoding="utf-8"))
                findings.append({"level": "ok", "msg": f"{f.name}: valid Python syntax (not executed)."})
            except SyntaxError as e:
                findings.append({"level": "fail", "msg": f"{f.name}: syntax error line {e.lineno}."})
        if f.suffix.lower() in {".py", ".m", ".java", ".c", ".cpp", ".ino", ".md", ".txt", ".tex", ".csv"}:
            text_blob += f.read_text(encoding="utf-8", errors="replace").lower()
        else:
            findings.append({"level": "unverified", "msg": f"{f.name}: content not machine-checkable; review manually."})
    uncovered = []
    for req in analysis["requirements"]:
        words = [w for w in re.findall(r"[a-z][a-z0-9+#]{3,}", req.lower()) if w not in STOP]
        if words and text_blob and sum(w in text_blob for w in words) / len(words) < 0.4:
            uncovered.append(req)
    for r in uncovered:
        findings.append({"level": "warn", "msg": f"Requirement possibly not addressed: {r}"})
    for n in analysis["needs_from_user"]:
        findings.append({"level": "todo", "msg": f"Needs you: {n}"})
    passed = not any(x["level"] == "fail" for x in findings)
    report = {"passed_automatic_checks": passed, "findings": findings,
              "disclaimer": "Automatic checks cannot confirm correctness or rubric score. Review before submitting."}
    (ws / "validation.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    return report
