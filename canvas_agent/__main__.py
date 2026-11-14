"""CLI: python -m canvas_agent <command>"""
from __future__ import annotations

import argparse
import json
import sys

from .app import App
from .config import Config
from .validation import validate


def main(argv=None) -> int:
    p = argparse.ArgumentParser(prog="canvas_agent")
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("check", help="verify token + which Canvas data you may read")
    sub.add_parser("sync", help="one sync now (use with Task Scheduler/cron)")
    r = sub.add_parser("run", help="run the monitoring loop")
    r.add_argument("--minutes", type=int)
    n = sub.add_parser("notifications")
    n.add_argument("--unread", action="store_true")
    n.add_argument("--mark-read", action="store_true")
    a = sub.add_parser("analyze", help="analyze one assignment by course + assignment id")
    a.add_argument("course_id")
    a.add_argument("assignment_id")
    v = sub.add_parser("validate", help="validate deliverables in a workspace folder")
    v.add_argument("workspace")
    sub.add_parser("serve", help="local notification center UI")
    sub.add_parser("status")
    ap = sub.add_parser("approve")
    ap.add_argument("id", type=int)
    ap.add_argument("--reject", action="store_true")
    args = p.parse_args(argv)

    cfg = Config.from_env()
    if args.cmd == "validate":
        print(json.dumps(validate(__import__("pathlib").Path(args.workspace)), indent=2))
        return 0
    if not cfg.base_url or not cfg.token:
        print("Set CANVAS_BASE_URL (e.g. https://school.instructure.com) and CANVAS_TOKEN first.", file=sys.stderr)
        return 2
    app = App(cfg)
    if args.cmd == "check":
        for k, v_ in app.client.probe_permissions().items():
            print(f"{k:14} {'OK' if v_['available'] else 'UNAVAILABLE: ' + v_['reason']}")
    elif args.cmd == "sync":
        print(json.dumps(app.sync_once(), indent=2))
    elif args.cmd == "run":
        app.run_forever(args.minutes)
    elif args.cmd == "notifications":
        for x in app.notifier.list(args.unread):
            print(f"#{x['id']} [{x['priority']}] {x['course_name']} | {x['event_type']} | {x['what_changed']} | {x['when_text']} | {x['url']}")
        if args.mark_read:
            app.notifier.mark_read()
    elif args.cmd == "analyze":
        res = app.client.get(f"/courses/{args.course_id}/assignments/{args.assignment_id}")
        if not res.ok:
            print("Assignment unavailable:", res.unavailable_reason)
            return 1
        c = app.client.get(f"/courses/{args.course_id}")
        an, ws = app.analyze_assignment(res.data, c.data.get("name", args.course_id) if c.ok else args.course_id)
        print(f"Workspace: {ws}\nAutomation: {an.automation}\nSee plan.md; put drafts in deliverables/ then run `validate`.")
    elif args.cmd == "serve":
        from .web import serve
        serve(app)
    elif args.cmd == "status":
        print(json.dumps({"heartbeat": app.store.get_meta("heartbeat"), "last_sync": app.store.get_meta("last_sync"),
                          "sources": app.store.statuses()}, indent=2, default=str))
    elif args.cmd == "approve":
        app.store.decide_approval(args.id, not args.reject)
        print("recorded")
    return 0


if __name__ == "__main__":
    sys.exit(main())
