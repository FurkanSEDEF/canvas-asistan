"""Configuration. Secrets come from environment variables only and are never logged."""
from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path

DEFAULT_PREFS = {
    # channel -> minimum priority that is delivered ("low" < "normal" < "high")
    "channels": {
        "in_app": {"enabled": True, "min_priority": "low"},
        "email": {"enabled": False, "min_priority": "high"},
        "telegram": {"enabled": False, "min_priority": "high"},
    },
    "muted_event_types": [],
    # Grades are private: external channels (email/Telegram) only say "a grade was posted"
    # unless you flip this to true.
    "include_grade_values_in_external": False,
}


@dataclass
class Config:
    base_url: str = ""
    token: str = field(default="", repr=False)  # repr=False: never print the token
    data_dir: Path = Path("data")
    poll_minutes: int = 15
    announcement_lookback_days: int = 30
    max_download_mb: int = 50
    # Optional integrations (all off unless configured)
    smtp_host: str = ""
    smtp_port: int = 587
    smtp_user: str = ""
    smtp_password: str = field(default="", repr=False)
    email_to: str = ""
    telegram_bot_token: str = field(default="", repr=False)
    telegram_chat_id: str = ""
    anthropic_api_key: str = field(default="", repr=False)
    llm_model: str = "claude-sonnet-5-5"

    @classmethod
    def from_env(cls) -> "Config":
        e = os.environ.get
        return cls(
            base_url=e("CANVAS_BASE_URL", "").rstrip("/"),
            token=e("CANVAS_TOKEN", ""),
            data_dir=Path(e("CANVAS_AGENT_DATA", "data")),
            poll_minutes=int(e("CANVAS_POLL_MINUTES", "15")),
            smtp_host=e("SMTP_HOST", ""),
            smtp_port=int(e("SMTP_PORT", "587")),
            smtp_user=e("SMTP_USER", ""),
            smtp_password=e("SMTP_PASSWORD", ""),
            email_to=e("NOTIFY_EMAIL_TO", ""),
            telegram_bot_token=e("TELEGRAM_BOT_TOKEN", ""),
            telegram_chat_id=e("TELEGRAM_CHAT_ID", ""),
            anthropic_api_key=e("ANTHROPIC_API_KEY", ""),
        )

    @property
    def db_path(self) -> Path:
        return self.data_dir / "canvas_agent.sqlite3"

    @property
    def prefs_path(self) -> Path:
        return self.data_dir / "preferences.json"

    def load_prefs(self) -> dict:
        prefs = json.loads(json.dumps(DEFAULT_PREFS))
        if self.prefs_path.exists():
            user = json.loads(self.prefs_path.read_text(encoding="utf-8"))
            for ch, v in user.get("channels", {}).items():
                prefs["channels"].setdefault(ch, {}).update(v)
            for k in ("muted_event_types", "include_grade_values_in_external"):
                if k in user:
                    prefs[k] = user[k]
        return prefs

    def save_prefs(self, prefs: dict) -> None:
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.prefs_path.write_text(json.dumps(prefs, indent=2), encoding="utf-8")
