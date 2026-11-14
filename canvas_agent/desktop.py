"""Windows masaüstü uygulaması: ayar penceresi, bildirim merkezi, sistem tepsisi, Windows bildirimleri.

Arka plan iş parçacığı Canvas'ı periyodik kontrol eder (izleme / analiz / bildirim modülleri aynen kullanılır).
Pencere kapatılınca uygulama tepsiye iner ve çalışmaya devam eder. Çıkış = tepsi menüsünden "Çıkış".
"""
from __future__ import annotations

import ctypes
import json
import os
import queue
import re
import subprocess
import sys
import threading
import time
import tkinter as tk
import urllib.parse
import webbrowser
from tkinter import messagebox, ttk

from .app import App
from .canvas_client import CanvasClient
from .events import PRIORITY_RANK
from .notifications import NotificationCenter
from .settings import APP_DIR, Settings
from .storage import Store

EVENT_TR = {
    "grade_published": "Yeni not", "grade_changed": "Not değişti", "deadline_changed": "Teslim tarihi değişti",
    "new_project": "Yeni proje", "new_assignment": "Yeni ödev", "new_announcement": "Yeni duyuru",
    "exam_schedule_update": "Sınav programı", "new_material": "Yeni ders materyali",
    "assignment_updated": "Ödev güncellendi", "analysis_ready": "Ödev analizi hazır", "system": "Sistem",
}
PRIO_TR = {"high": "Yüksek", "normal": "Normal", "low": "Düşük"}
WHY_TR = {  # Türkçe "neden önemli"
    "grade_published": "Yeni bir not yayınlandı; rubrikle karşılaştırıp kontrol edin.",
    "grade_changed": "Mevcut bir not değişti (yeniden değerlendirme ya da düzeltme olabilir).",
    "deadline_changed": "Teslim tarihi değişti; planınızı güncellemeniz gerekebilir.",
    "new_project": "Projeler genelde yüksek ağırlıklıdır; erken planlama gerekir.",
    "exam_schedule_update": "Sınav zamanı ya da yeri değişti ya da duyuruldu.",
    "new_assignment": "Planlamanız gereken yeni bir ödev var.",
    "new_announcement": "Hoca yeni bir duyuru yayınladı.",
    "new_material": "Yeni ya da güncellenmiş ders materyali var.",
    "assignment_updated": "Ödev metni ya da puanı değişti; gereksinimleri tekrar okuyun.",
    "analysis_ready": "Ödev için taslak analiz ve çalışma planı hazır; inceleyin. Hiçbir şey otomatik teslim edilmez.",
    "system": "İzleme servisi durumu.",
}
OFFLINE_NOTE = ("Not: İzleme yalnızca bilgisayar açıkken ve uygulama çalışırken yapılır. Kapalıyken olan "
                "değişiklikler bir sonraki açılışta yakalanır, ama bildirim gecikmeli gelir.")


# --------------------------------------------------------------------------- arka plan işçisi
class Worker(threading.Thread):
    def __init__(self, settings: Settings, ui_q: queue.Queue):
        super().__init__(daemon=True)
        self.settings, self.ui_q = settings, ui_q
        self.wake, self.stop_flag, self.reset_flag = threading.Event(), False, True
        self.app: App | None = None

    def run(self):
        started = True
        while not self.stop_flag:
            if not self.settings.connected:
                self.ui_q.put(("status", "Canvas'a bağlı değil."))
                self.wake.wait(5)
                self.wake.clear()
                continue
            try:
                if self.reset_flag or self.app is None:
                    self.app, self.reset_flag = App(self.settings.to_config()), False
                    last = self.app.store.get_meta("last_sync")
                    interval = int(self.settings.v["poll_minutes"]) * 60
                    if started and last and time.time() - last > interval * 3:
                        self.app.notifier.add_system(
                            "İzleme kapalıydı",
                            f"~{(time.time() - last) / 3600:.1f} saattir senkronizasyon yapılmadı. Bu sürede "
                            "olan değişiklikler şimdi yakalanıyor; kesin zamanları bilinmiyor.")
                    started = False
                before = self.app.store.conn.execute("SELECT COALESCE(MAX(id),0) m FROM notifications").fetchone()["m"]
                self.app.store.set_meta("heartbeat", time.time())
                self.ui_q.put(("status", "Canvas kontrol ediliyor…"))
                res = self.app.sync_once()
                rows = [dict(r) for r in self.app.store.conn.execute(
                    "SELECT * FROM notifications WHERE id>? ORDER BY id", (before,))]
                bad = [p for p in res["problems"] if "401" in p or "Courses unavailable" in p]
                msg = ("Token geçersiz/süresi dolmuş olabilir: " + bad[0]) if bad else \
                    f"Son kontrol: {time.strftime('%H:%M')} — Canvas bağlı"
                if res["problems"] and not bad:
                    msg += f" ({len(res['problems'])} kaynak okunamadı)"
                self.ui_q.put(("status", msg))
                self.ui_q.put(("synced", rows))
            except Exception as e:
                self.ui_q.put(("status", f"Kontrol başarısız: {type(e).__name__}. Tekrar denenecek."))
            self.wake.wait(int(self.settings.v["poll_minutes"]) * 60)
            self.wake.clear()


# --------------------------------------------------------------------------- yardımcılar
def single_instance() -> bool:
    ctypes.windll.kernel32.CreateMutexW(None, False, "Local\\CanvasAgentDesktop")
    return ctypes.windll.kernel32.GetLastError() != 183


def set_autostart(enabled: bool):
    import winreg
    exe = sys.executable if getattr(sys, "frozen", False) else None
    if not exe:
        return
    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\Microsoft\Windows\CurrentVersion\Run", 0,
                        winreg.KEY_SET_VALUE) as k:
        if enabled:
            winreg.SetValueEx(k, "CanvasAgent", 0, winreg.REG_SZ, f'"{exe}" --tray')
        else:
            try:
                winreg.DeleteValue(k, "CanvasAgent")
            except FileNotFoundError:
                pass


def normalize_url(u: str) -> str:
    u = u.strip()
    if not u:
        return ""
    if not re.match(r"https?://", u):
        u = "https://" + u
    p = urllib.parse.urlparse(u)
    return f"{p.scheme}://{p.netloc}"


def make_icon_image():
    from PIL import Image, ImageDraw
    img = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.ellipse((2, 2, 62, 62), fill=(30, 100, 200, 255))
    d.text((22, 20), "C", fill="white")
    d.arc((14, 14, 50, 50), 40, 320, fill="white", width=6)
    return img


# --------------------------------------------------------------------------- ana pencere
class MainWindow:
    def __init__(self, start_hidden: bool):
        self.settings = Settings()
        self.ui_q: queue.Queue = queue.Queue()
        self.cmd_q: queue.Queue = queue.Queue()
        self.root = tk.Tk()
        self.root.title("Canvas Asistanım")
        self.root.geometry("980x600")
        self.root.protocol("WM_DELETE_WINDOW", self.hide)
        self.store = Store(APP_DIR / "data" / "canvas_agent.sqlite3")
        self.notifier = NotificationCenter(self.store, self.settings.to_config())
        self.rows: dict[str, dict] = {}
        self._build()
        self.worker = Worker(self.settings, self.ui_q)
        self.worker.start()
        self.tray = None
        self._start_tray()
        self.refresh()
        if not self.settings.connected:
            self.root.after(300, self.connect_dialog)
        elif start_hidden:
            self.root.withdraw()
        self.root.after(500, self.poll)

    # ---- arayüz
    def _build(self):
        r = self.root
        top = ttk.Frame(r, padding=8)
        top.pack(fill="x")
        self.status = tk.StringVar(value="Başlatılıyor…")
        ttk.Label(top, textvariable=self.status).pack(side="left")
        for text, cmd in (("Ayarlar", self.settings_dialog), ("Durum", self.status_dialog),
                          ("Hepsini okundu yap", self.mark_all), ("Şimdi kontrol et", self.check_now)):
            ttk.Button(top, text=text, command=cmd).pack(side="right", padx=3)
        self.nb = ttk.Notebook(r)
        self.nb.pack(fill="both", expand=True, padx=8)
        t1 = ttk.Frame(self.nb)
        self.nb.add(t1, text="Bildirimler")
        self.ov = {}
        for key, title, cols in (
                ("due", "Yaklaşan teslimler", (("Ders", 200), ("Ödev", 300), ("Teslim", 170), ("Puan", 60), ("Durum", 150))),
                ("grades", "Notlar", (("Ders", 220), ("Öğe", 330), ("Not", 120), ("Üzerinden", 80))),
                ("news", "Son duyurular", (("Ders", 220), ("Başlık", 440), ("Tarih", 170)))):
            f = ttk.Frame(self.nb)
            self.nb.add(f, text=title)
            tv = ttk.Treeview(f, columns=[c for c, _ in cols], show="headings")
            for c, w in cols:
                tv.heading(c, text=c)
                tv.column(c, width=w, anchor="w")
            tv.pack(fill="both", expand=True)
            tv.bind("<Double-1>", lambda e, t=tv: self._open_ov(t))
            self.ov[key] = (tv, {})
        cols = ("prio", "course", "type", "what", "when")
        self.tree = ttk.Treeview(t1, columns=cols, show="headings", height=14)
        for c, t, w in (("prio", "Öncelik", 70), ("course", "Ders", 170), ("type", "Olay", 150),
                        ("what", "Ne değişti", 420), ("when", "Tarih", 150)):
            self.tree.heading(c, text=t)
            self.tree.column(c, width=w, anchor="w")
        self.tree.tag_configure("high", foreground="#b00020")
        self.tree.tag_configure("unread", font=("Segoe UI", 9, "bold"))
        self.tree.pack(fill="both", expand=True)
        self.tree.bind("<<TreeviewSelect>>", self.show_detail)
        self.tree.bind("<Double-1>", lambda e: self.open_link())
        self.detail = tk.Text(t1, height=7, wrap="word", state="disabled", bg="#f6f6f6")
        self.detail.pack(fill="x", pady=6)
        bar = ttk.Frame(t1)
        bar.pack(fill="x")
        ttk.Button(bar, text="Canvas'ta aç", command=self.open_link).pack(side="left")
        ttk.Button(bar, text="Analiz planını aç", command=self.open_plan).pack(side="left", padx=4)
        ttk.Button(bar, text="Test bildirimi gönder", command=self.test_toast).pack(side="right")
        ttk.Label(r, text=OFFLINE_NOTE, wraplength=940, foreground="#666").pack(padx=8, pady=(0, 8))

    def refresh(self):
        sel = self.tree.selection()
        self.tree.delete(*self.tree.get_children())
        self.rows.clear()
        for n in self.notifier.list(limit=200):
            iid = str(n["id"])
            self.rows[iid] = n
            tags = [n["priority"]] + (["unread"] if not n["read"] else [])
            self.tree.insert("", "end", iid=iid, tags=tags, values=(
                PRIO_TR[n["priority"]], n["course_name"], EVENT_TR.get(n["event_type"], n["event_type"]),
                n["what_changed"], n["when_text"] or ""))
        if sel and sel[0] in self.rows:
            self.tree.selection_set(sel[0])
        if not self.rows:
            self.detail.config(state="normal")
            self.detail.delete("1.0", "end")
            self.detail.insert("1.0", "Henüz bildirim yok. Bu NORMAL: ilk kontrol Canvas'taki mevcut durumu kaydetti; "
                               "bildirimler bundan SONRA olan değişikliklerde (yeni ödev, not, tarih değişikliği, duyuru…) gelir.\n\n"
                               "Şu anki gerçek durumunuzu 'Yaklaşan teslimler', 'Notlar' ve 'Son duyurular' sekmelerinde görebilirsiniz.")
            self.detail.config(state="disabled")
        self.refresh_overview()

    def refresh_overview(self):
        """Kaydedilmiş (gerçek) Canvas verisinden özet sekmeleri. Veri yoksa boş kalır, uydurulmaz."""
        from datetime import datetime, timezone
        from .events import fmt_dt
        names = self.store.get_meta("course_names", {})
        snaps: dict[str, list] = {}
        for r in self.store.conn.execute("SELECT kind, course_id, data FROM snapshots"):
            snaps.setdefault(r["kind"], []).append((r["course_id"], json.loads(r["data"])))
        cn = lambda cid: names.get(cid, f"Ders {cid}")
        now = datetime.now(timezone.utc).isoformat()
        for tv, links in self.ov.values():
            tv.delete(*tv.get_children())
            links.clear()
        def add(key, vals, url=""):
            tv, links = self.ov[key]
            iid = tv.insert("", "end", values=vals)
            links[iid] = url
        due = sorted((a["due_at"], cid, a) for cid, a in snaps.get("assignments", [])
                     if a.get("due_at") and a["due_at"] >= now)
        for d, cid, a in due:
            add("due", (cn(cid), a["name"], fmt_dt(d), a.get("points") if a.get("points") is not None else "",
                        "Not girildi" if a.get("score") is not None else "Notlandırılmadı / teslim bekliyor"), a.get("url") or "")
        for cid, g in snaps.get("course_grade", []):
            if g.get("score") is not None:
                add("grades", (cn(cid), "DERS GENEL NOTU", f"{g['score']}% {g.get('grade') or ''}".strip(), "100"))
        for cid, a in snaps.get("assignments", []):
            if a.get("score") is not None:
                add("grades", (cn(cid), a["name"], a["score"], a.get("points") if a.get("points") is not None else ""), a.get("url") or "")
        for cid, a in sorted(snaps.get("announcements", []), key=lambda x: x[1].get("posted_at") or "", reverse=True)[:40]:
            add("news", (cn(cid), a.get("title"), fmt_dt(a.get("posted_at"))), a.get("url") or "")

    def _open_ov(self, tv):
        for key, (t, links) in self.ov.items():
            if t is tv and tv.selection() and links.get(tv.selection()[0]):
                webbrowser.open(links[tv.selection()[0]])

    def test_toast(self):
        if self.tray:
            self.tray.notify("Bildirimler çalışıyor. Gerçek değişiklik olunca böyle görünecek.", "Canvas Asistanım — Test")
        else:
            messagebox.showinfo("Test", "Sistem tepsisi kullanılamıyor; bildirim gösterilemedi.")

    def selected(self):
        s = self.tree.selection()
        return self.rows.get(s[0]) if s else None

    def show_detail(self, _=None):
        n = self.selected()
        if not n:
            return
        self.notifier.mark_read(n["id"])
        text = (f"Ders: {n['course_name']}\nOlay: {EVENT_TR.get(n['event_type'], n['event_type'])}\n"
                f"Başlık: {n['title']}\nNe değişti: {n['what_changed']}\n"
                f"Tarih/saat: {n['when_text'] or '-'}\nCanvas bağlantısı: {n['url'] or '-'}\n"
                f"Neden önemli: {WHY_TR.get(n['event_type'], n['why'])}")
        self.detail.config(state="normal")
        self.detail.delete("1.0", "end")
        self.detail.insert("1.0", text)
        self.detail.config(state="disabled")

    def open_link(self):
        n = self.selected()
        if n and n["url"]:
            webbrowser.open(n["url"])

    def open_plan(self):
        n = self.selected()
        m = re.search(r"Review (.+plan\.md)", (n or {}).get("what_changed", ""))
        if m and os.path.exists(m.group(1)):
            os.startfile(m.group(1))
        else:
            messagebox.showinfo("Analiz planı", "Bu kayıt için bir analiz planı yok. 'Ödev analizi hazır' kaydını seçin.")

    def mark_all(self):
        self.notifier.mark_read()
        self.refresh()

    def check_now(self):
        self.worker.wake.set()

    # ---- bağlantı penceresi
    def connect_dialog(self):
        d = tk.Toplevel(self.root)
        d.title("Canvas'a bağlan")
        d.transient(self.root)
        d.grab_set()
        f = ttk.Frame(d, padding=16)
        f.pack()
        ttk.Label(f, text="Bu bilgiler yalnızca bir kez istenir ve bu bilgisayarda şifreli saklanır.",
                  wraplength=420).grid(columnspan=2, pady=(0, 8))
        ttk.Label(f, text="Canvas adresi:").grid(row=1, column=0, sticky="e")
        url = ttk.Entry(f, width=42)
        url.grid(row=1, column=1, pady=3)
        url.insert(0, self.settings.v["base_url"] or "https://canvas.agu.edu.tr")
        ttk.Label(f, text="Canvas token:").grid(row=2, column=0, sticky="e")
        tok = ttk.Entry(f, width=42, show="*")
        tok.grid(row=2, column=1, pady=3)
        msg = tk.StringVar()
        ttk.Label(f, textvariable=msg, foreground="#b00020", wraplength=420).grid(columnspan=2)
        btn = ttk.Button(f, text="Bağlan")
        btn.grid(columnspan=2, pady=8)

        def done(ok, text, base, token):
            btn.config(state="normal")
            if not ok:
                msg.set(text)
                return
            self.settings.v.update(base_url=base, token=token)
            self.settings.save()
            set_autostart(bool(self.settings.v["autostart"]))
            d.destroy()
            self.worker.reset_flag = True
            self.worker.wake.set()

        def go():
            base, token = normalize_url(url.get()), tok.get().strip()
            if not base or not token:
                msg.set("Adres ve token gerekli.")
                return
            btn.config(state="disabled")
            msg.set("Bağlanılıyor…")

            def work():
                try:
                    caps = CanvasClient(base, token).probe_permissions()
                    c = caps.get("profile", {})
                    if not c.get("available"):
                        r = ("Token geçersiz veya süresi dolmuş (yetki reddedildi)." if "401" in c.get("reason", "")
                             else "Bağlanılamadı: " + c.get("reason", "bilinmeyen hata"))
                        self.root.after(0, done, False, r, base, token)
                    else:
                        self.root.after(0, done, True, "", base, token)
                except Exception as e:
                    self.root.after(0, done, False, f"Bağlantı hatası: {e}", base, token)
            threading.Thread(target=work, daemon=True).start()

        btn.config(command=go)

    # ---- ayarlar
    def settings_dialog(self):
        s = self.settings.v
        d = tk.Toplevel(self.root)
        d.title("Ayarlar")
        d.transient(self.root)
        f = ttk.Frame(d, padding=14)
        f.pack()
        vars_ = {}
        fields = [("poll_minutes", "Kontrol aralığı (dakika)", False), ("telegram_token", "Telegram bot token (isteğe bağlı)", True),
                  ("telegram_chat_id", "Telegram chat ID", False), ("smtp_host", "E-posta SMTP sunucusu (isteğe bağlı)", False),
                  ("smtp_port", "SMTP port", False), ("smtp_user", "SMTP kullanıcı", False),
                  ("smtp_password", "SMTP şifre", True), ("email_to", "Bildirim e-posta adresi", False)]
        r = 0
        ttk.Label(f, text="Windows bildirimi gösterilecek en düşük öncelik:").grid(row=r, column=0, sticky="e")
        prio = ttk.Combobox(f, state="readonly", values=["Düşük", "Normal", "Yüksek"], width=12)
        prio.set({"low": "Düşük", "normal": "Normal", "high": "Yüksek"}[s["toast_min_priority"]])
        prio.grid(row=r, column=1, sticky="w")
        for key, label, secret in fields:
            r += 1
            ttk.Label(f, text=label + ":").grid(row=r, column=0, sticky="e", pady=2)
            v = tk.StringVar(value=str(s[key]))
            ttk.Entry(f, textvariable=v, width=36, show="*" if secret else "").grid(row=r, column=1, pady=2)
            vars_[key] = v
        auto, grades = tk.BooleanVar(value=s["autostart"]), tk.BooleanVar(value=s["include_grades_external"])
        ttk.Checkbutton(f, text="Windows açılınca otomatik başlat", variable=auto).grid(row=r + 1, columnspan=2, sticky="w")
        ttk.Checkbutton(f, text="Not değerlerini e-posta/Telegram mesajına da yaz (kapalıyken sadece 'not girildi' der)",
                        variable=grades).grid(row=r + 2, columnspan=2, sticky="w")

        def save():
            try:
                s["poll_minutes"] = max(5, int(vars_["poll_minutes"].get()))
                s["smtp_port"] = int(vars_["smtp_port"].get())
            except ValueError:
                messagebox.showerror("Ayarlar", "Dakika ve port sayı olmalı.")
                return
            for k in ("telegram_token", "telegram_chat_id", "smtp_host", "smtp_user", "smtp_password", "email_to"):
                s[k] = vars_[k].get().strip()
            s["toast_min_priority"] = {"Düşük": "low", "Normal": "normal", "Yüksek": "high"}[prio.get()]
            s["autostart"], s["include_grades_external"] = auto.get(), grades.get()
            self.settings.save()
            set_autostart(s["autostart"])
            self.worker.reset_flag = True
            self.worker.wake.set()
            d.destroy()

        def disconnect():
            if messagebox.askyesno("Bağlantıyı sil", "Kayıtlı Canvas adresi ve token silinsin mi?"):
                self.settings.clear_connection()
                d.destroy()
                self.connect_dialog()

        b = ttk.Frame(f)
        b.grid(row=r + 3, columnspan=2, pady=10)
        ttk.Button(b, text="Kaydet", command=save).pack(side="left", padx=4)
        ttk.Button(b, text="Token'ı değiştir / bağlantıyı sil", command=disconnect).pack(side="left", padx=4)

    def status_dialog(self):
        d = tk.Toplevel(self.root)
        d.title("Durum")
        t = tk.Text(d, width=90, height=24, wrap="word")
        t.pack(padx=8, pady=8)
        caps = self.store.get_meta("capabilities", {})
        lines = ["Erişim izinleri (Canvas token'ınızın okuyabildikleri):"]
        for k, v in caps.items():
            lines.append(f"  {k}: " + ("OK" if v["available"] else f"ERİŞİLEMİYOR — {v['reason']}"))
        lines += ["", "Veri kaynakları:"] + [f"  {s['source']}: {s['status']} {s['detail'] or ''}" for s in self.store.statuses()]
        hb = self.store.get_meta("heartbeat")
        lines += ["", f"Son senkronizasyon: {time.strftime('%Y-%m-%d %H:%M', time.localtime(self.store.get_meta('last_sync', 0)))}"
                  if self.store.get_meta("last_sync") else "Henüz senkronizasyon yapılmadı.", "", OFFLINE_NOTE]
        t.insert("1.0", "\n".join(lines))
        t.config(state="disabled")

    # ---- tepsi
    def _start_tray(self):
        try:
            import pystray
            menu = pystray.Menu(
                pystray.MenuItem("Aç", lambda: self.cmd_q.put("show"), default=True),
                pystray.MenuItem("Şimdi kontrol et", lambda: self.cmd_q.put("check")),
                pystray.MenuItem("Çıkış", lambda: self.cmd_q.put("quit")))
            self.tray = pystray.Icon("CanvasAgent", make_icon_image(), "Canvas Asistanım", menu)
            threading.Thread(target=self.tray.run, daemon=True).start()
        except Exception:
            self.tray = None

    def hide(self):
        if self.tray:
            self.root.withdraw()
            if not getattr(self, "_told", False):
                self.tray.notify("Uygulama arka planda çalışmaya devam ediyor. Çıkmak için tepsi simgesine sağ tıklayın.",
                                 "Canvas Asistanım")
                self._told = True
        else:
            self.quit()

    def show(self):
        self.root.deiconify()
        self.root.lift()
        self.root.focus_force()

    def quit(self):
        self.worker.stop_flag = True
        self.worker.wake.set()
        if self.tray:
            self.tray.stop()
        self.root.destroy()

    def toast(self, rows):
        min_rank = PRIORITY_RANK[self.settings.v["toast_min_priority"]]
        rows = [n for n in rows if PRIORITY_RANK[n["priority"]] >= min_rank]
        if not rows or not self.tray:
            return
        for n in rows[:3]:
            self.tray.notify(f"{n['what_changed']}"[:250],
                             f"{n['course_name']} — {EVENT_TR.get(n['event_type'], n['event_type'])}"[:60])
            time.sleep(0.2)
        if len(rows) > 3:
            self.tray.notify(f"+{len(rows) - 3} bildirim daha. Uygulamayı açıp bakın.", "Canvas Asistanım")

    def poll(self):
        try:
            while True:
                kind, data = self.ui_q.get_nowait()
                if kind == "status":
                    self.status.set(data)
                elif kind == "synced":
                    self.refresh()
                    self.toast(data)

        except queue.Empty:
            pass
        try:
            while True:
                c = self.cmd_q.get_nowait()
                {"show": self.show, "check": self.check_now, "quit": self.quit}[c]()
        except queue.Empty:
            pass
        except tk.TclError:
            return
        self.root.after(700, self.poll)


def main():
    if not single_instance():
        ctypes.windll.user32.MessageBoxW(0, "Canvas Asistanım zaten çalışıyor (sistem tepsisine bakın).", "Canvas Asistanım", 0x40)
        return
    win = MainWindow(start_hidden="--tray" in sys.argv)
    win.root.mainloop()
