"""DEMO: sahte (uydurma) bir Canvas ile sistemin uçtan uca çalıştığını gösterir.
Gerçek Canvas'a bağlanmaz, token gerekmez. Veriler tamamen örnektir.
Kullanım: python demo.py          (sonunda http://127.0.0.1:8765 adresinde arayüz açılır)
"""
import shutil
import sys
import threading
from http.server import HTTPServer
from pathlib import Path

ROOT = Path(__file__).parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))
from test_pipeline import STATE, Fake, fresh_state  # noqa: E402

from canvas_agent.app import App  # noqa: E402
from canvas_agent.config import Config  # noqa: E402
from canvas_agent.web import serve  # noqa: E402

data = ROOT / "demo_data"
shutil.rmtree(data, ignore_errors=True)
fresh_state()
srv = HTTPServer(("127.0.0.1", 0), Fake)
threading.Thread(target=srv.serve_forever, daemon=True).start()
app = App(Config(base_url=f"http://127.0.0.1:{srv.server_port}", token="demo", data_dir=data))

print("\n=== 1) İlk senkronizasyon (mevcut durum kaydedilir, bildirim YOK) ===")
r = app.sync_once()
print("Yeni bildirim:", r["new_notifications"])

print("\n=== 2) Canvas'ta değişiklikler oluyor (simülasyon) ===")
STATE["assignments"][0]["due_at"] = "2026-11-25T23:59:00Z"
STATE["assignments"][0]["submission"] = {"score": 8.5}
STATE["assignments"].append({
    "id": 11, "name": "Arduino Project 2", "due_at": "2026-12-01T23:59:00Z", "points_possible": 100,
    "html_url": "http://canvas.example/a11",
    "description": "<p>You must build the circuit on a breadboard and write an Arduino sketch. Submit a report.</p>",
    "rubric": [{"description": "Correctness", "points": 60, "ratings": [{"description": "Full"}]}],
    "submission": {"score": None}})
STATE["announcements"].append({"id": 5, "title": "Midterm date moved", "message": "<p>The exam time is now Friday</p>",
                               "posted_at": "2026-11-11T10:00:00Z", "html_url": "http://canvas.example/n5"})
STATE["files"].append({"id": 7, "display_name": "lecture1.pdf", "modified_at": "2026-11-11T09:00:00Z", "size": 5})

print("\n=== 3) İkinci senkronizasyon: sistem değişiklikleri yakalar ===")
r = app.sync_once()
print("Yeni bildirim:", r["new_notifications"])
for n in app.notifier.list():
    print(f"  [{n['priority']:6}] {n['course_name']} | {n['event_type']} | {n['what_changed']}")

print("\n=== 4) Üçüncü senkronizasyon: değişiklik yok -> tekrar bildirim YOK ===")
print("Yeni bildirim:", app.sync_once()["new_notifications"])

ws = next((data / "workspaces").iterdir())
print("\n=== 5) Yeni proje otomatik analiz edildi:", ws.name, "===")
print((ws / "plan.md").read_text(encoding="utf-8"))

serve(app)
