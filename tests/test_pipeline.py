import json
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from urllib.parse import urlparse

from canvas_agent.app import App
from canvas_agent.config import Config
from canvas_agent.notifications import format_message
from canvas_agent.validation import validate

STATE = {}


def fresh_state():
    STATE.clear()
    STATE.update(
        deny=set(),
        courses=[{"id": 1, "name": "Circuits I", "enrollments": [
            {"type": "student", "computed_current_score": 90.0, "computed_current_grade": "A-"}]}],
        assignments=[{"id": 10, "name": "Homework 1", "due_at": "2026-11-20T23:59:00Z", "points_possible": 10,
                      "html_url": "http://x/a10", "description": "<p>Solve problems.</p>", "submission": {"score": None}}],
        announcements=[], files=[], events=[])


class Fake(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def do_GET(self):
        path = urlparse(self.path).path.replace("/api/v1", "")
        key = path.split("/")[-1] if "/courses/1/" in path else path.strip("/")
        if key in STATE["deny"]:
            self.send_response(403)
            self.end_headers()
            return
        data = {"users/self": {"id": 1}, "courses": STATE["courses"], "assignments": STATE["assignments"],
                "announcements": STATE["announcements"], "files": STATE["files"],
                "calendar_events": STATE["events"]}.get(key)
        if data is None:
            self.send_response(404)
            self.end_headers()
            return
        body = json.dumps(data).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(body)


class PipelineTest(unittest.TestCase):
    def setUp(self):
        fresh_state()
        self.srv = HTTPServer(("127.0.0.1", 0), Fake)
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()
        self.tmp = tempfile.TemporaryDirectory()
        cfg = Config(base_url=f"http://127.0.0.1:{self.srv.server_port}", token="t", data_dir=Path(self.tmp.name))
        self.app = App(cfg)

    def tearDown(self):
        self.srv.shutdown()
        self.app.store.conn.close()
        self.tmp.cleanup()

    def types(self):
        return sorted(n["event_type"] for n in self.app.notifier.list() if n["event_type"] != "analysis_ready")

    def test_full_flow(self):
        r = self.app.sync_once()
        self.assertEqual(r["new_notifications"], 0, "baseline sync must be silent")

        STATE["assignments"][0]["due_at"] = "2026-11-25T23:59:00Z"
        STATE["assignments"][0]["submission"] = {"score": 8.5}
        STATE["assignments"].append({"id": 11, "name": "Arduino Project 2", "due_at": "2026-12-01T23:59:00Z",
                                     "points_possible": 100, "html_url": "http://x/a11",
                                     "description": "<p>You must build the circuit on a breadboard and write an Arduino sketch. Submit a report.</p>",
                                     "rubric": [{"description": "Correctness", "points": 60, "ratings": [{"description": "Full"}]}],
                                     "submission": {"score": None}})
        STATE["announcements"].append({"id": 5, "title": "Midterm date moved", "message": "<p>The exam time is now Friday</p>",
                                       "posted_at": "2026-11-11T10:00:00Z", "html_url": "http://x/n5"})
        STATE["files"].append({"id": 7, "display_name": "lecture1.pdf", "modified_at": "2026-11-11T09:00:00Z", "size": 5})
        STATE["courses"][0]["enrollments"][0]["computed_current_score"] = 88.0
        # first appearance of announcements/files kinds happened at baseline (empty) so these are new
        r = self.app.sync_once()
        self.assertEqual(self.types(), sorted([
            "deadline_changed", "grade_published", "new_project",
            "exam_schedule_update", "new_material", "grade_changed"]))

        # Unchanged data -> no duplicates
        before = len(self.app.notifier.list(limit=500))
        r = self.app.sync_once()
        self.assertEqual(r["new_notifications"], 0)
        self.assertEqual(len(self.app.notifier.list(limit=500)), before)

        # Analysis workspace produced, hardware limitation flagged, high priority sorted first
        ws = next((Path(self.tmp.name) / "workspaces").iterdir())
        a = json.loads((ws / "analysis.json").read_text())
        self.assertIn("hardware_lab", a["types"])
        self.assertIn(a["automation"], ("partial", "needs_user"))
        self.assertTrue(a["needs_from_user"])
        self.assertEqual(self.app.notifier.list()[0]["priority"], "high")

        # Validation: no deliverables -> fail; empty folder is honest about it
        rep = validate(ws)
        self.assertFalse(rep["passed_automatic_checks"])
        (ws / "deliverables" / "report.md").write_text("Circuit report with Arduino sketch and breadboard build.")
        self.assertTrue(validate(ws)["passed_automatic_checks"])

    def test_permission_denied_is_reported_not_invented(self):
        self.app.sync_once()
        STATE["deny"].add("announcements")
        STATE["announcements"].append({"id": 9, "title": "Hidden", "message": "", "posted_at": "x"})
        r = self.app.sync_once()
        self.assertEqual(r["new_notifications"], 0)
        self.assertTrue(any("announcements" in p for p in r["problems"]))

    def test_grade_redaction_for_external_channels(self):
        n = {"details": json.dumps({"private": True}), "what_changed": "Grade 95/100", "priority": "high",
             "course_name": "C", "event_type": "grade_published", "title": "t", "when_text": "", "url": "", "why": "w"}
        self.assertNotIn("95", format_message(n, True)[1])
        self.assertIn("95", format_message(n, False)[1])


if __name__ == "__main__":
    unittest.main()
