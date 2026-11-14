"""Tiny local notification-center UI. Binds to 127.0.0.1 only (grades are private)."""
from __future__ import annotations

import html
from http.server import BaseHTTPRequestHandler, HTTPServer

from .app import App


def serve(app: App, port: int = 8765) -> None:
    class H(BaseHTTPRequestHandler):
        def do_GET(self):
            rows = app.notifier.list(limit=100)
            items = "".join(
                f"<li class='{r['priority']}'><b>[{r['priority']}] {html.escape(r['course_name'])} — "
                f"{html.escape(r['event_type'])}</b><br>{html.escape(r['what_changed'])}<br>"
                f"<small>{html.escape(r['when_text'] or '')} · {html.escape(r['why'])} "
                + (f"· <a href='{html.escape(r['url'])}'>Canvas</a>" if r["url"] else "") + "</small></li>"
                for r in rows) or "<li>No notifications yet.</li>"
            st = "".join(f"<li>{html.escape(s['source'])}: {html.escape(s['status'])} {html.escape(s['detail'] or '')}</li>"
                         for s in app.store.statuses())
            body = (f"<!doctype html><meta charset=utf-8><title>Canvas Agent</title>"
                    "<style>body{font:15px system-ui;max-width:800px;margin:2rem auto;padding:0 1rem}"
                    "li{margin:.6rem 0;list-style:none;padding:.5rem;border-left:4px solid #888}"
                    ".high{border-color:#d33}.normal{border-color:#39c}</style>"
                    f"<h1>Notifications</h1><ul>{items}</ul><h2>Data source status</h2><ul>{st}</ul>")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            self.wfile.write(body.encode())

        def log_message(self, *a):
            pass

    print(f"Notification center: http://127.0.0.1:{port}")
    HTTPServer(("127.0.0.1", port), H).serve_forever()
