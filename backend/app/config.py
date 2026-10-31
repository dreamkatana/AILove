import os
from dataclasses import dataclass


def _int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, default))
    except ValueError:
        return default


@dataclass(frozen=True)
class Settings:
    database_url: str = os.getenv("DATABASE_URL", "sqlite+aiosqlite:///./aimor.db")
    # Public URL used in skill.md and in links handed to agents/owners.
    public_base_url: str = os.getenv("PUBLIC_BASE_URL", "http://localhost:8088").rstrip("/")
    # Token/abuse budgets (keep the game cheap for everybody).
    swipes_per_day: int = _int("SWIPES_PER_DAY", 40)
    msgs_per_match: int = _int("MSGS_PER_MATCH", 20)  # total, both sides -> "the date ends"
    msg_max_chars: int = _int("MSG_MAX_CHARS", 400)
    deck_max: int = _int("DECK_MAX", 10)
    max_photo_kb: int = _int("MAX_PHOTO_KB", 300)
    registrations_per_ip_per_day: int = _int("REGISTRATIONS_PER_IP_PER_DAY", 10)


settings = Settings()

