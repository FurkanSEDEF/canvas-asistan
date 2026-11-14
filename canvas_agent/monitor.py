"""Change detection. Compares live Canvas data with stored snapshots and emits Events.

Knows nothing about notifications, analysis or files: it just returns Events.
Rules:
  * First sync of a (kind, course) is a silent BASELINE - otherwise you'd get hundreds of
    "new" notifications for things that already existed.
  * Snapshots and events are committed in one transaction (no lost/duplicated events on crash).
  * If a source can't be read, the snapshot is left untouched and the source is marked
    unavailable - nothing is invented and nothing is falsely reported as "removed".
"""
from __future__ import annotations

import hashlib
import re
import time
from datetime import datetime, timedelta, timezone
from html import unescape

from .canvas_client import CanvasClient
from .events import Event, fmt_dt
from .storage import Store

EXAM_RE = re.compile(r"\b(exam|midterm|final|quiz|test)\b", re.I)
PROJECT_RE = re.compile(r"\b(project|capstone|term paper|lab report)\b", re.I)
GRADE_KIND_RE = [("final", re.compile(r"\bfinal\b", re.I)), ("midterm", re.compile(r"\bmid-?term\b", re.I)),
                 ("quiz", re.compile(r"\bquiz\b", re.I)), ("exam", re.compile(r"\bexam\b", re.I))]


def strip_html(s: str | None) -> str:
    return re.sub(r"\s+", " ", unescape(re.sub(r"<[^>]+>", " ", s or ""))).strip()


def _h(s: str) -> str:
    return hashlib.sha1(s.encode("utf-8")).hexdigest()[:16]


def grade_label(name: str) -> str:
    for label, rx in GRADE_KIND_RE:
        if rx.search(name or ""):
            return label
    return "assignment"


class CanvasMonitor:
    def __init__(self, client: CanvasClient, store: Store, announcement_lookback_days: int = 30):
        self.client, self.store = client, store
        self.lookback = announcement_lookback_days
        self.capabilities: dict[str, dict] = {}

    # ------------------------------------------------------------------ public
    def sync(self) -> tuple[list[Event], list[str]]:
        """One incremental sync. Returns (events, unavailable_messages)."""
        self.capabilities = self.client.probe_permissions()
        self.store.set_meta("capabilities", self.capabilities)
        problems: list[str] = []
        if not self.capabilities.get("courses", {}).get("available"):
            msg = f"Courses unavailable: {self.capabilities.get('courses', {}).get('reason')}"
            self.store.set_status("courses", "unavailable", msg)
            return [], [msg]

        courses = self.client.get(
            "/courses", [("enrollment_state", "active"), ("include[]", "total_scores"), ("per_page", "100")]
        )
        if not courses.ok:
            msg = f"Courses unavailable: {courses.unavailable_reason}"
            self.store.set_status("courses", "unavailable", msg)
            return [], [msg]
        self.store.set_status("courses", "ok", f"{len(courses.data)} active courses")
        self.store.set_meta("course_names", {str(c["id"]): c.get("name") or c.get("course_code") or str(c["id"])
                                             for c in courses.data if isinstance(c, dict) and "id" in c})

        events: list[Event] = []
        for c in courses.data:
            if not isinstance(c, dict) or "id" not in c or c.get("access_restricted_by_date"):
                continue
            cid, cname = str(c["id"]), c.get("name") or c.get("course_code") or str(c["id"])
            events += self._course_grade(c, cid, cname)
            for kind, fn in (("assignments", self._assignments), ("announcements", self._announcements),
                             ("files", self._files), ("calendar", self._calendar)):
                if not self.capabilities.get(kind, {}).get("available"):
                    problems.append(f"{kind} unavailable ({self.capabilities.get(kind, {}).get('reason')})")
                    self.store.set_status(kind, "unavailable", self.capabilities.get(kind, {}).get("reason", ""))
                    continue
                evs, err = fn(cid, cname)
                events += evs
                if err:
                    problems.append(f"{cname}: {kind} unavailable ({err})")
                    self.store.set_status(f"{kind}:{cid}", "unavailable", err)
                else:
                    self.store.set_status(f"{kind}:{cid}", "ok")
        self.store.set_meta("last_sync", time.time())
        return events, sorted(set(problems))

    # ------------------------------------------------------------------ generic diff/commit
    def _commit(self, kind, cid, current: dict[str, dict], events: list[Event]):
        baseline_key = f"baseline:{kind}:{cid}"
        with self.store.tx() as c:
            for key, rec in current.items():
                c.execute("INSERT OR REPLACE INTO snapshots VALUES(?,?,?,?)", (kind, cid, key, _dumps(rec)))
            c.execute("INSERT OR REPLACE INTO meta VALUES(?, 'true')", (baseline_key,))
            # Outbox row is written in the SAME transaction as the new snapshot: a crash can
            # neither lose an event nor emit it twice. The notification center drains it.
            for ev in events:
                c.execute("INSERT OR IGNORE INTO outbox(fingerprint, payload) VALUES(?,?)",
                          (ev.fingerprint, ev.to_json()))

    def _is_baselined(self, kind, cid) -> bool:
        return bool(self.store.get_meta(f"baseline:{kind}:{cid}", False))

    def _run(self, kind, cid, fetched, normalize, diff):
        """fetched: Result of the list call. normalize(raw)->(key, rec). diff(old, rec, raw)->[Event]."""
        if not fetched.ok:
            return [], fetched.unavailable_reason
        prev = self.store.load_snapshots(kind, cid)
        current, raws = {}, {}
        for raw in fetched.data:
            if not isinstance(raw, dict):
                continue
            key, rec = normalize(raw)
            current[key], raws[key] = rec, raw
        events: list[Event] = []
        if self._is_baselined(kind, cid):
            for key, rec in current.items():
                if prev.get(key) != rec:  # cheap equality first: unchanged items cost nothing
                    events += diff(prev.get(key), rec, raws[key])
        self._commit(kind, cid, current, events)
        return events, ""

    # ------------------------------------------------------------------ course-level grade
    def _course_grade(self, c, cid, cname) -> list[Event]:
        enr = next((e for e in c.get("enrollments", []) if e.get("type") == "student"), None)
        if not enr:
            return []
        rec = {"score": enr.get("computed_current_score"), "grade": enr.get("computed_current_grade")}
        prev = self.store.load_snapshots("course_grade", cid).get("overall")
        evs: list[Event] = []
        if self._is_baselined("course_grade", cid) and prev != rec and rec["score"] is not None:
            evs.append(Event("grade_changed", cid, cname, "Overall course grade",
                             f"Overall grade changed: {_s(prev and prev['score'])} -> {_s(rec['score'])}"
                             f" ({rec.get('grade') or 'no letter grade'})",
                             url=f"{self.client.base}/courses/{cid}/grades",
                             key="overall", old=prev, new=rec,
                             details={"private": True, "course_level": True}))
        self._commit("course_grade", cid, {"overall": rec}, evs)
        return evs

    # ------------------------------------------------------------------ assignments (+ grades)
    def _assignments(self, cid, cname):
        res = self.client.paginate(f"/courses/{cid}/assignments",
                                   [("include[]", "submission"), ("order_by", "due_at")])

        def norm(a):
            sub = a.get("submission") or {}
            return str(a["id"]), {
                "name": a.get("name"), "due_at": a.get("due_at"), "lock_at": a.get("lock_at"),
                "points": a.get("points_possible"), "url": a.get("html_url"),
                "desc": _h(strip_html(a.get("description"))),
                "score": sub.get("score"), "grade": sub.get("grade"), "excused": sub.get("excused"),
            }

        def diff(old, new, raw):
            name, url = new["name"], new["url"] or ""
            if old is None:
                is_project = bool(PROJECT_RE.search(name or ""))
                return [Event("new_project" if is_project else "new_assignment", cid, cname, name,
                              f"New {'project' if is_project else 'assignment'} published: {name}"
                              + (f" ({new['points']} pts)" if new["points"] is not None else ""),
                              when=fmt_dt(new["due_at"]) and f"Due {fmt_dt(new['due_at'])}", url=url,
                              key=str(raw["id"]), new=new["name"], details={"assignment": raw})]
            evs = []
            if old.get("due_at") != new["due_at"]:
                evs.append(Event("deadline_changed", cid, cname, name,
                                 f"Deadline changed: {fmt_dt(old.get('due_at')) or 'none'} -> "
                                 f"{fmt_dt(new['due_at']) or 'none'}",
                                 when=fmt_dt(new["due_at"]), url=url, key=str(raw["id"]),
                                 old=old.get("due_at"), new=new["due_at"], details={"assignment": raw}))
            if old.get("score") != new["score"] and new["score"] is not None:
                label = grade_label(name)
                kind = "grade_published" if old.get("score") is None else "grade_changed"
                pts = f"/{new['points']}" if new["points"] is not None else ""
                what = (f"{label.capitalize()} grade published: {new['score']}{pts}" if kind == "grade_published"
                        else f"Grade changed: {old.get('score')} -> {new['score']}{pts}")
                evs.append(Event(kind, cid, cname, name, what, url=url, key=str(raw["id"]),
                                 old=old.get("score"), new=new["score"],
                                 details={"private": True, "grade_label": label}))
            if (old.get("desc") != new["desc"] or old.get("points") != new["points"]) and not evs:
                evs.append(Event("assignment_updated", cid, cname, name,
                                 "Instructions or point value were edited.", url=url,
                                 key=str(raw["id"]), old=old.get("desc"), new=new["desc"],
                                 details={"assignment": raw}))
            return evs

        return self._run("assignments", cid, res, norm, diff)

    # ------------------------------------------------------------------ announcements
    def _announcements(self, cid, cname):
        start = (datetime.now(timezone.utc) - timedelta(days=self.lookback)).strftime("%Y-%m-%d")
        res = self.client.paginate("/announcements", [("context_codes[]", f"course_{cid}"),
                                                      ("start_date", start), ("active_only", "true")])

        def norm(a):
            return str(a["id"]), {"title": a.get("title"), "posted_at": a.get("posted_at"),
                                  "body": _h(strip_html(a.get("message"))), "url": a.get("html_url")}

        def diff(old, new, raw):
            if old is not None:
                return []  # edited announcements are not re-notified
            title = new["title"] or ""
            body = strip_html(raw.get("message"))
            exam = bool(EXAM_RE.search(title)) and re.search(r"\b(schedule|date|time|room|location|moved|rescheduled|postponed)\b", title + " " + body, re.I)
            return [Event("exam_schedule_update" if exam else "new_announcement", cid, cname, title,
                          ("Exam-related announcement: " if exam else "New announcement: ") + title
                          + (f" — {body[:160]}…" if body else ""),
                          when=fmt_dt(new["posted_at"]), url=new["url"] or "", key=str(raw["id"]),
                          new=title, details={"announcement": raw})]

        return self._run("announcements", cid, res, norm, diff)

    # ------------------------------------------------------------------ files
    def _files(self, cid, cname):
        res = self.client.paginate(f"/courses/{cid}/files", [("sort", "updated_at"), ("order", "desc")])

        def norm(f):
            return str(f["id"]), {"name": f.get("display_name"), "modified": f.get("modified_at") or f.get("updated_at"),
                                  "size": f.get("size"), "url": f.get("url")}

        def diff(old, new, raw):
            verb = "uploaded" if old is None else "updated"
            return [Event("new_material", cid, cname, new["name"], f"Course file {verb}: {new['name']}",
                          when=fmt_dt(new["modified"]), url=f"{self.client.base}/courses/{cid}/files/{raw['id']}",
                          key=str(raw["id"]), old=old and old.get("modified"), new=new["modified"],
                          details={"file": raw})]

        return self._run("files", cid, res, norm, diff)

    # ------------------------------------------------------------------ calendar (exam schedule)
    def _calendar(self, cid, cname):
        now = datetime.now(timezone.utc)
        res = self.client.paginate("/calendar_events", [
            ("context_codes[]", f"course_{cid}"), ("type", "event"),
            ("start_date", (now - timedelta(days=7)).strftime("%Y-%m-%d")),
            ("end_date", (now + timedelta(days=180)).strftime("%Y-%m-%d"))])

        def norm(e):
            return str(e["id"]), {"title": e.get("title"), "start": e.get("start_at"), "end": e.get("end_at"),
                                  "loc": e.get("location_name"), "url": e.get("html_url")}

        def diff(old, new, raw):
            if not EXAM_RE.search(new["title"] or ""):
                return []
            if old is None:
                what = f"Exam/quiz event scheduled: {new['title']}"
            else:
                what = (f"Exam/quiz event changed: {fmt_dt(old.get('start'))} -> {fmt_dt(new['start'])}"
                        + (f" @ {new['loc']}" if new.get("loc") else ""))
            return [Event("exam_schedule_update", cid, cname, new["title"], what, when=fmt_dt(new["start"]),
                          url=new["url"] or "", key=str(raw["id"]), old=old, new=new, details={"calendar": raw})]

        return self._run("calendar", cid, res, norm, diff)


def _dumps(o):  # snapshots are compared as dicts after json round-trip
    import json
    return json.dumps(o, sort_keys=True)


def _s(v):
    return "n/a" if v is None else v
