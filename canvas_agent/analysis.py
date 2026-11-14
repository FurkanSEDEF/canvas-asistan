"""Assignment analysis: classify, map to tools, build an execution plan, flag limitations.

Rule-based on purpose (deterministic, works offline). It never claims an assignment can be done
automatically unless nothing in the text requires hardware, live participation, or missing inputs.
"""
from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass, field
from pathlib import Path

from .files import extract_text, safe_name
from .monitor import strip_html

TYPES = [
    # (type, regex, languages/tools, deliverables)
    ("exam_or_quiz", r"\b(exam|midterm|final exam|quiz|proctor|lockdown)\b", [], []),
    ("hardware_lab", r"\b(arduino|breadboard|oscilloscope|multimeter|solder|sensor|microcontroller|hardware|lab experiment|measure(?:ment)?s?)\b",
     ["Arduino", "circuit analysis"], ["lab report", "firmware (.ino)", "circuit diagram"]),
    ("circuit_simulation", r"\b(circuit|spice|ltspice|simulink|multisim|op-?amp|transistor|kirchhoff|impedance)\b",
     ["circuit analysis", "MATLAB/Simulink", "Python (numpy/scipy)"], ["simulation files", "report"]),
    ("programming", r"\b(matlab|python|java|c\+\+|c language|code|program|algorithm|implement|function|script)\b",
     ["MATLAB", "Python", "Java", "C/C++"], ["source code", "README / technical documentation"]),
    ("presentation", r"\b(presentation|slides?|powerpoint|pitch)\b", ["slide deck"], ["presentation (.pptx)"]),
    ("spreadsheet_analysis", r"\b(excel|spreadsheet|dataset|data analysis|csv|regression)\b", ["Python (pandas)", "spreadsheet"], ["spreadsheet (.xlsx)", "report"]),
    ("written_report", r"\b(report|essay|paper|write-?up|reflection|discussion|summary|documentation)\b", ["document drafting"], ["report (.docx/.pdf)"]),
]
LANG_HINTS = {"MATLAB": r"\bmatlab\b|\.m\b", "Python": r"\bpython\b|\.py\b", "Java": r"\bjava\b",
              "C/C++": r"\bc\+\+|\bc language\b|\.cpp\b", "Arduino": r"\barduino\b|\.ino\b"}
HW_RE = re.compile(r"\b(arduino|breadboard|oscilloscope|multimeter|solder|physical|in lab|in-person|lab session|build the circuit|measure)\b", re.I)
LIVE_RE = re.compile(r"\b(in class|in-class|present (?:to|in)|live demo|oral|proctor|group meeting|attendance)\b", re.I)
MISSING_RE = re.compile(r"\b(attached|provided|given|see (?:the )?(?:file|dataset|handout)|template)\b", re.I)
REQ_RE = re.compile(r"[^.!?\n]*\b(must|should|shall|need to|required?|include|submit|deliver|use|provide|explain|compare|show|plot|calculate|write|implement)\b[^.!?\n]*[.!?]?", re.I)


@dataclass
class Analysis:
    assignment_id: str
    name: str
    course: str
    url: str
    due: str
    points: float | None
    types: list[str]
    requirements: list[str]
    rubric: list[dict]
    tools: list[str]
    deliverables: list[str]
    plan: list[str]
    automation: str  # "can_draft" | "partial" | "needs_user" | "study_support_only"
    limitations: list[str] = field(default_factory=list)
    needs_from_user: list[str] = field(default_factory=list)
    attachments: list[str] = field(default_factory=list)
    languages: list[str] = field(default_factory=list)


def analyze(raw: dict, course_name: str, attachment_texts: dict[str, str], attachment_problems: list[str],
            attachments_seen: bool = True) -> Analysis:
    desc = strip_html(raw.get("description"))
    corpus = " ".join([raw.get("name") or "", desc] + list(attachment_texts.values()))
    types = [t for t, rx, *_ in TYPES if re.search(rx, corpus, re.I)] or ["unclassified"]
    tools, deliverables = [], []
    for t, _, tl, dl in TYPES:
        if t in types:
            tools += [x for x in tl if x not in tools]
            deliverables += [x for x in dl if x not in deliverables]
    langs = [k for k, rx in LANG_HINTS.items() if re.search(rx, corpus, re.I)]

    # Requirements: sentences from instructions + attachments, then rubric criteria.
    reqs = []
    for m in REQ_RE.finditer(corpus):
        s = m.group(0).strip()
        if 15 < len(s) < 300 and s not in reqs:
            reqs.append(s)
    rubric = [{"criterion": r.get("description") or r.get("long_description", ""), "points": r.get("points"),
               "ratings": [x.get("description") for x in r.get("ratings", [])]}
              for r in (raw.get("rubric") or [])]

    limits, needs = [], []
    if not desc and not attachment_texts:
        limits.append("No instruction text could be retrieved; analysis would be guesswork.")
        needs.append("Paste the assignment instructions or open them in Canvas.")
    if not raw.get("rubric"):
        limits.append("No grading rubric found in Canvas (may be in an attachment or not published).")
    for p in attachment_problems:
        limits.append(f"Attachment not processed: {p}")
        needs.append("Provide the attachment manually if it is required.")
    if MISSING_RE.search(corpus) and not attachment_texts:
        needs.append("Instructions refer to attached/provided material that could not be read.")
    if "hardware_lab" in types or HW_RE.search(corpus):
        limits.append("Involves physical hardware or measurements; AI cannot perform experiments.")
        needs.append("Build/measure on real hardware and supply your measurements, photos or data.")
    if LIVE_RE.search(corpus):
        limits.append("Contains a live/in-person component (presentation, demo, attendance).")
        needs.append("Attend or present yourself.")
    if "exam_or_quiz" in types:
        limits.append("Exam/quiz: assessed individually and often proctored; only study support is appropriate.")

    if "exam_or_quiz" in types and not ({"programming", "written_report"} & set(types)):
        automation = "study_support_only"
    elif needs and ("hardware_lab" in types or LIVE_RE.search(corpus)):
        automation = "partial"
    elif needs:
        automation = "needs_user"
    elif types == ["unclassified"]:
        automation = "needs_user"
        limits.append("Could not determine the assignment type.")
    else:
        automation = "can_draft"
    limits.append("Check your course/university policy on AI assistance before using any draft.")

    plan = ["Read the full instructions and rubric; confirm the requirement checklist below."]
    if attachment_texts:
        plan.append("Review the extracted attachment content for data, templates and constraints.")
    if "programming" in types or "circuit_simulation" in types:
        plan.append(f"Choose implementation language/tool ({', '.join(langs) or 'not specified - ask instructor/you'}).")
        plan.append("Draft code/simulation, then run it and capture outputs/plots as evidence.")
    if "hardware_lab" in types:
        plan.append("Prepare circuit analysis and firmware draft; YOU build, measure and record results.")
    if "spreadsheet_analysis" in types:
        plan.append("Load data, clean it, run the required analysis, export results.")
    plan.append(f"Produce deliverables: {', '.join(deliverables) or 'as specified in the instructions'}.")
    plan.append("Validate every deliverable against the requirement checklist and rubric.")
    plan.append("Review by you. NOTHING is submitted to Canvas automatically.")

    return Analysis(str(raw["id"]), raw.get("name", ""), course_name, raw.get("html_url", ""),
                    raw.get("due_at") or "", raw.get("points_possible"), types, reqs[:25], rubric, tools,
                    deliverables, plan, automation, limits, needs, sorted(attachment_texts), langs)


def render_markdown(a: Analysis) -> str:
    def bl(xs): return "\n".join(f"- {x}" for x in xs) or "- (none)"
    rub = "\n".join(f"- **{r['criterion']}** ({r['points']} pts): {' / '.join(map(str, r['ratings']))}" for r in a.rubric) or "- (no rubric available)"
    return f"""# {a.name} — {a.course}
Canvas: {a.url}  |  Due: {a.due or 'n/a'}  |  Points: {a.points}

**Type:** {', '.join(a.types)}  |  **Automation level:** `{a.automation}`
**Languages/tools that can help:** {', '.join(a.tools + a.languages) or 'n/a'}

## Requirement checklist
{bl(a.requirements)}

## Rubric
{rub}

## Execution plan
{chr(10).join(f'{i}. {s}' for i, s in enumerate(a.plan, 1))}

## Deliverables
{bl(a.deliverables)}

## Limitations
{bl(a.limitations)}

## Needs your involvement
{bl(a.needs_from_user)}
"""


class AnalysisService:
    """Creates a per-assignment workspace: analysis.json, plan.md, attachments/, deliverables/."""

    def __init__(self, workspace_root: Path):
        self.root = workspace_root

    def workspace(self, course_name: str, a_id: str, name: str) -> Path:
        p = self.root / f"{safe_name(course_name)}__{a_id}__{safe_name(name)}"
        (p / "attachments").mkdir(parents=True, exist_ok=True)
        (p / "deliverables").mkdir(exist_ok=True)
        return p

    def run(self, raw: dict, course_name: str, attachment_paths: list[Path], problems: list[str],
            ws: Path) -> Analysis:
        texts, ex_problems = {}, list(problems)
        for p in attachment_paths:
            t, note = extract_text(p)
            if t.strip():
                texts[p.name] = t
            elif note:
                ex_problems.append(f"{p.name}: {note}")
        a = analyze(raw, course_name, texts, ex_problems)
        (ws / "analysis.json").write_text(json.dumps(asdict(a), indent=2), encoding="utf-8")
        (ws / "plan.md").write_text(render_markdown(a), encoding="utf-8")
        return a
