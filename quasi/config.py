"""Settings from environment / .env. Nothing secret is ever committed."""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
HOME = Path(os.getenv("QUASI_HOME", Path.home() / ".config" / "quasi"))


def _load_dotenv(path: Path) -> None:
    if not path.exists():
        return
    for line in path.read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, _, v = line.partition("=")
            os.environ.setdefault(k.strip(), v.strip().strip("'\""))


@dataclass(frozen=True)
class Settings:
    model: str
    effort: str
    max_turns: int
    google_client_secret: Path   # OAuth client JSON downloaded from Google Cloud console
    google_token: Path           # cached user token (0600)
    audit_log: Path
    timezone: str | None         # override; default = your Google Calendar's timezone

    @classmethod
    def load(cls) -> "Settings":
        _load_dotenv(ROOT / ".env")
        home = Path(os.getenv("QUASI_HOME", HOME))
        return cls(
            model=os.getenv("QUASI_MODEL", "claude-opus-5-5"),
            effort=os.getenv("QUASI_EFFORT", "medium"),
            max_turns=int(os.getenv("QUASI_MAX_TURNS", "12")),
            google_client_secret=Path(os.getenv("GOOGLE_CLIENT_SECRET_FILE", home / "google_client_secret.json")),
            google_token=Path(os.getenv("GOOGLE_TOKEN_FILE", home / "google_token.json")),
            audit_log=Path(os.getenv("QUASI_AUDIT_LOG", home / "audit.jsonl")),
            timezone=os.getenv("QUASI_TIMEZONE") or None,
        )
