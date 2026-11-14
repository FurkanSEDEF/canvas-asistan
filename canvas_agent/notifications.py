"""Notification center: stores every notification (in-app) and fans out to optional channels.

Independent of monitoring: it only consumes the outbox that monitoring writes.
Dedup: the notifications table has a UNIQUE fingerprint, so an unchanged fact can never notify twice.
"""
from __future__ import annotations

import json
import smtplib
import time
import urllib.parse
import urllib.request
from email.message import EmailMessage

from .config import Config
from .events import EVENT_PRIORITY, PRIORITY_RANK, Event
from .storage import Store

MAX_ATTEMPTS = 5


def format_message(n: dict, redact_private: bool) -> tuple[str, str]:
    """(subject, body). Grades are redacted for external channels unless the user opted in."""
    details = json.loads(n.get("details") or "{}")
    what = n["what_changed"]
    if redact_private and details.get("private"):
        what = "A grade was posted or changed. Open Canvas to view it."
    subject = f"[{n['priority'].upper()}] {n['course_name']}: {n['event_type'].replace('_', ' ')}"
    lines = [
        f"Course: {n['course_name']}", f"Event: {n['event_type'].replace('_', ' ')}",
        f"Item: {n['title']}", f"What changed: {what}",
    ]
    if n.get("when_text"):
        lines.append(f"When: {n['when_text']}")
    if n.get("url"):
        lines.append(f"Canvas link: {n['url']}")
    lines.append(f"Why it matters: {n['why']}")
    return subject, "\n".join(lines)


class NotificationCenter:
    def __init__(self, store: Store, config: Config):
        self.store, self.config = store, config

    # ------------------------------------------------------------------ intake
    def drain_outbox(self) -> list[Event]:
        """Move monitoring events into notifications. Returns only genuinely NEW events."""
        prefs = self.config.load_prefs()
        new_events: list[Event] = []
        with self.store.tx() as c:
            rows = c.execute("SELECT id, payload FROM outbox ORDER BY id").fetchall()
            for r in rows:
                ev = Event.from_json(r["payload"])
                if ev.type in prefs["muted_event_types"]:
                    c.execute("DELETE FROM outbox WHERE id=?", (r["id"],))
                    continue
                if self._insert(c, ev, prefs):
                    new_events.append(ev)
                c.execute("DELETE FROM outbox WHERE id=?", (r["id"],))
        return new_events

    def add_system(self, title: str, what: str, event_type: str = "system") -> None:
        ev = Event(event_type, "", "Canvas Agent", title, what, key=title, new=int(time.time() // 60))
        prefs = self.config.load_prefs()
        with self.store.tx() as c:
            self._insert(c, ev, prefs)

    def _insert(self, c, ev: Event, prefs: dict) -> bool:
        cur = c.execute(
            "INSERT OR IGNORE INTO notifications(fingerprint,course_name,event_type,priority,title,"
            "what_changed,when_text,url,why,details,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
            (ev.fingerprint, ev.course_name, ev.type, ev.priority, ev.title, ev.what_changed, ev.when,
             ev.url, ev.why, json.dumps({"private": bool(ev.details.get("private"))}), time.time()))
        if cur.rowcount == 0:
            return False  # duplicate -> no second notification
        nid = cur.lastrowid
        for ch, cfg in prefs["channels"].items():
            if cfg.get("enabled") and PRIORITY_RANK[ev.priority] >= PRIORITY_RANK[cfg.get("min_priority", "high")]:
                status = "sent" if ch == "in_app" else "pending"
                c.execute("INSERT OR IGNORE INTO deliveries(notification_id,channel,status) VALUES(?,?,?)",
                          (nid, ch, status))
        return True

    # ------------------------------------------------------------------ delivery
    def deliver_pending(self) -> dict[str, int]:
        prefs = self.config.load_prefs()
        redact = not prefs.get("include_grade_values_in_external", False)
        counts = {"sent": 0, "failed": 0}
        rows = self.store.conn.execute(
            "SELECT d.notification_id, d.channel, d.attempts, n.* FROM deliveries d "
            "JOIN notifications n ON n.id=d.notification_id "
            "WHERE d.status IN ('pending','retry') ORDER BY n.id").fetchall()
        for r in rows:
            n = dict(r)
            sender = {"email": self._send_email, "telegram": self._send_telegram}.get(n["channel"])
            err = "channel not implemented" if sender is None else None
            if sender is not None:
                try:
                    sender(*format_message(n, redact))
                except Exception as e:  # report the failure; never claim delivery
                    err = f"{type(e).__name__}: {e}"[:200]
            attempts = n["attempts"] + 1
            status = "sent" if err is None else ("failed" if attempts >= MAX_ATTEMPTS else "retry")
            with self.store.tx() as c:
                c.execute("UPDATE deliveries SET status=?, attempts=?, last_error=? WHERE notification_id=? AND channel=?",
                          (status, attempts, err, n["notification_id"], n["channel"]))
            counts["sent" if err is None else "failed"] += 1
        return counts

    def _send_email(self, subject: str, body: str) -> None:
        c = self.config
        if not (c.smtp_host and c.email_to):
            raise RuntimeError("email not configured (SMTP_HOST / NOTIFY_EMAIL_TO)")
        msg = EmailMessage()
        msg["Subject"], msg["From"], msg["To"] = subject, c.smtp_user or c.email_to, c.email_to
        msg.set_content(body)
        with smtplib.SMTP(c.smtp_host, c.smtp_port, timeout=20) as s:
            s.starttls()
            if c.smtp_user:
                s.login(c.smtp_user, c.smtp_password)
            s.send_message(msg)

    def _send_telegram(self, subject: str, body: str) -> None:
        c = self.config
        if not (c.telegram_bot_token and c.telegram_chat_id):
            raise RuntimeError("telegram not configured (TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID)")
        data = urllib.parse.urlencode({"chat_id": c.telegram_chat_id, "text": f"{subject}\n\n{body}"}).encode()
        req = urllib.request.Request(f"https://api.telegram.org/bot{c.telegram_bot_token}/sendMessage", data=data)
        with urllib.request.urlopen(req, timeout=20) as r:
            if r.status != 200:
                raise RuntimeError(f"HTTP {r.status}")

    # ------------------------------------------------------------------ queries (in-app center)
    def list(self, unread_only: bool = False, limit: int = 50) -> list[dict]:
        q = "SELECT * FROM notifications" + (" WHERE read=0" if unread_only else "")
        q += " ORDER BY CASE priority WHEN 'high' THEN 0 WHEN 'normal' THEN 1 ELSE 2 END, id DESC LIMIT ?"
        return [dict(r) for r in self.store.conn.execute(q, (limit,))]

    def mark_read(self, nid: int | None = None) -> None:
        with self.store.tx() as c:
            c.execute("UPDATE notifications SET read=1" + (" WHERE id=?" if nid else ""), (nid,) if nid else ())
