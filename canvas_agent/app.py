"""Wiring + scheduler. The only place the independent modules meet."""
from __future__ import annotations

import random
import signal
import time

from .analysis import AnalysisService
from .canvas_client import CanvasClient
from .config import Config
from .files import download_attachments
from .monitor import CanvasMonitor
from .notifications import NotificationCenter
from .storage import Store

LOCAL_WARNING = (
    "NOTE: monitoring runs only while this process is running. If your computer sleeps, shuts down or\n"
    "this window closes, Canvas is NOT checked. Changes made meanwhile are caught on the next sync\n"
    "(state is compared to the last stored snapshot), but you are notified late. For always-on\n"
    "monitoring deploy it on a server/VM/container (see README)."
)


class App:
    def __init__(self, config: Config, client: CanvasClient | None = None):
        self.config = config
        self.store = Store(config.db_path)
        self.client = client or CanvasClient(config.base_url, config.token, self.store)
        self.monitor = CanvasMonitor(self.client, self.store, config.announcement_lookback_days)
        self.notifier = NotificationCenter(self.store, config)
        self.analysis = AnalysisService(config.data_dir / "workspaces")

    def sync_once(self) -> dict:
        events, problems = self.monitor.sync()
        new = self.notifier.drain_outbox()
        for ev in new:  # analysis is triggered by events but is a separate module
            if ev.type in ("new_assignment", "new_project") and "assignment" in ev.details:
                try:
                    self.analyze_assignment(ev.details["assignment"], ev.course_name)
                except Exception as e:
                    self.notifier.add_system("Assignment analysis failed", f"{ev.title}: {type(e).__name__}")
        delivery = self.notifier.deliver_pending()
        return {"events_detected": len(events), "new_notifications": len(new), "problems": problems,
                "delivery": delivery, "api_requests": self.client.requests_made, "cache_hits": self.client.cache_hits}

    def analyze_assignment(self, raw: dict, course_name: str):
        ws = self.analysis.workspace(course_name, str(raw["id"]), raw.get("name", "assignment"))
        paths, problems = download_attachments(self.client, raw, ws / "attachments", self.config.max_download_mb)
        a = self.analysis.run(raw, course_name, paths, problems, ws)
        self.notifier.add_system(
            f"Analysis ready: {a.name}",
            f"{course_name}: type {', '.join(a.types)}, automation '{a.automation}'. Review {ws / 'plan.md'}",
            event_type="analysis_ready")
        return a, ws

    # ------------------------------------------------------------------ scheduler
    def run_forever(self, interval_min: int | None = None) -> None:
        interval = (interval_min or self.config.poll_minutes) * 60
        print(LOCAL_WARNING, flush=True)
        stop = {"flag": False}
        signal.signal(signal.SIGINT, lambda *_: stop.update(flag=True))
        last = self.store.get_meta("last_sync")
        if last and time.time() - last > interval * 3:
            gap_h = (time.time() - last) / 3600
            self.notifier.add_system(
                "Monitoring was offline",
                f"No sync for ~{gap_h:.1f}h. Changes in that period are detected now, but their exact timing is unknown.")
        while not stop["flag"]:
            try:
                self.store.set_meta("heartbeat", time.time())
                r = self.sync_once()
                print(time.strftime("%H:%M:%S"), r, flush=True)
            except Exception as e:  # keep the daemon alive; surface the failure
                self.store.set_status("sync", "error", f"{type(e).__name__}")
                print("sync failed:", type(e).__name__, flush=True)
            for _ in range(int(interval + random.uniform(0, 30))):
                if stop["flag"]:
                    break
                time.sleep(1)
