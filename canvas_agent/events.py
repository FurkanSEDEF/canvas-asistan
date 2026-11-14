"""Event model shared by monitoring -> notifications/analysis. No logic that talks to Canvas here."""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from datetime import datetime

PRIORITY_RANK = {"low": 0, "normal": 1, "high": 2}

EVENT_PRIORITY = {
    "grade_published": "high",
    "grade_changed": "high",
    "deadline_changed": "high",
    "new_project": "high",
    "exam_schedule_update": "high",
    "new_assignment": "normal",
    "new_announcement": "normal",
    "new_material": "low",
    "assignment_updated": "low",
    "analysis_ready": "normal",
    "system": "normal",
}

WHY = {
    "grade_published": "A new grade is available; check it against your expectations and the rubric.",
    "grade_changed": "An existing grade changed (regrade, correction, or late-policy adjustment).",
    "deadline_changed": "The due date moved; your schedule and planning may need to change.",
    "new_project": "Projects usually carry large weight and need early planning.",
    "exam_schedule_update": "Exam timing or logistics changed or were announced.",
    "new_assignment": "New coursework to plan for.",
    "new_announcement": "The instructor posted new course information.",
    "new_material": "New or updated course material or files.",
    "assignment_updated": "The assignment text or point value changed; re-read the requirements.",
    "analysis_ready": "A draft analysis and execution plan is ready for your review.",
    "system": "Monitoring service status.",
}


def fmt_dt(iso: str | None) -> str:
    """Canvas gives UTC ISO timestamps; show them in the local timezone."""
    if not iso:
        return ""
    try:
        dt = datetime.fromisoformat(iso.replace("Z", "+00:00"))
        return dt.astimezone().strftime("%a %Y-%m-%d %H:%M %Z")
    except ValueError:
        return iso


@dataclass
class Event:
    type: str
    course_id: str
    course_name: str
    title: str
    what_changed: str
    when: str = ""
    url: str = ""
    details: dict = field(default_factory=dict)  # raw data for downstream modules; not shown in UI
    key: str = ""  # stable identity of the changed object, for the fingerprint
    old: object = None
    new: object = None

    def to_json(self) -> str:
        return json.dumps(self.__dict__, default=str)

    @classmethod
    def from_json(cls, s: str) -> "Event":
        return cls(**json.loads(s))

    @property
    def priority(self) -> str:
        return EVENT_PRIORITY.get(self.type, "normal")

    @property
    def why(self) -> str:
        return WHY.get(self.type, "")

    @property
    def fingerprint(self) -> str:
        raw = json.dumps([self.type, self.course_id, self.key, self.old, self.new], sort_keys=True, default=str)
        return hashlib.sha256(raw.encode()).hexdigest()
