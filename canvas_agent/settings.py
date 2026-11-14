"""Kalıcı ayarlar. Token ve şifreler Windows DPAPI ile şifrelenir (sadece bu Windows kullanıcısı çözebilir)."""
from __future__ import annotations

import base64
import ctypes
import json
import os
from ctypes import wintypes
from pathlib import Path

from .config import Config

APP_DIR = Path(os.environ.get("APPDATA", str(Path.home()))) / "CanvasAgent"


class _Blob(ctypes.Structure):
    _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_char))]


def _crypt(data: bytes, protect: bool) -> bytes:
    crypt32, kernel32 = ctypes.windll.crypt32, ctypes.windll.kernel32
    buf = ctypes.create_string_buffer(data, len(data))
    bin_ = _Blob(len(data), ctypes.cast(buf, ctypes.POINTER(ctypes.c_char)))
    out = _Blob()
    fn = crypt32.CryptProtectData if protect else crypt32.CryptUnprotectData
    if not fn(ctypes.byref(bin_), None, None, None, None, 0, ctypes.byref(out)):
        raise OSError("DPAPI failed")
    try:
        return ctypes.string_at(out.pbData, out.cbData)
    finally:
        kernel32.LocalFree(out.pbData)


def protect(text: str) -> str:
    return base64.b64encode(_crypt(text.encode("utf-8"), True)).decode() if text else ""


def unprotect(blob: str) -> str:
    try:
        return _crypt(base64.b64decode(blob), False).decode("utf-8") if blob else ""
    except Exception:
        return ""


SECRETS = {"token", "telegram_token", "smtp_password"}
DEFAULTS = dict(base_url="", token="", poll_minutes=15, toast_min_priority="normal", autostart=True,
                telegram_token="", telegram_chat_id="", smtp_host="", smtp_port=587, smtp_user="",
                smtp_password="", email_to="", include_grades_external=False)


class Settings:
    path = APP_DIR / "settings.json"

    def __init__(self):
        self.v = dict(DEFAULTS)
        self.load()

    def load(self):
        if self.path.exists():
            try:
                raw = json.loads(self.path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                raw = {}
            for k, val in raw.items():
                if k in DEFAULTS:
                    self.v[k] = unprotect(val) if k in SECRETS else val

    def save(self):
        APP_DIR.mkdir(parents=True, exist_ok=True)
        out = {k: (protect(val) if k in SECRETS else val) for k, val in self.v.items()}
        self.path.write_text(json.dumps(out, indent=2), encoding="utf-8")

    @property
    def connected(self) -> bool:
        return bool(self.v["base_url"] and self.v["token"])

    def clear_connection(self):
        self.v["base_url"] = self.v["token"] = ""
        self.save()

    def to_config(self) -> Config:
        v = self.v
        cfg = Config(base_url=v["base_url"], token=v["token"], data_dir=APP_DIR / "data",
                     poll_minutes=int(v["poll_minutes"]), smtp_host=v["smtp_host"], smtp_port=int(v["smtp_port"]),
                     smtp_user=v["smtp_user"], smtp_password=v["smtp_password"], email_to=v["email_to"],
                     telegram_bot_token=v["telegram_token"], telegram_chat_id=v["telegram_chat_id"])
        prefs = cfg.load_prefs()
        prefs["channels"]["telegram"]["enabled"] = bool(v["telegram_token"] and v["telegram_chat_id"])
        prefs["channels"]["email"]["enabled"] = bool(v["smtp_host"] and v["email_to"])
        prefs["include_grade_values_in_external"] = bool(v["include_grades_external"])
        cfg.save_prefs(prefs)
        return cfg
