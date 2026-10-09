from dataclasses import dataclass
from pathlib import Path
import os

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[2]
load_dotenv(ROOT / ".env")


@dataclass(frozen=True)
class Settings:
    host: str = os.getenv("BACKEND_HOST", "127.0.0.1")
    port: int = int(os.getenv("BACKEND_PORT", "8010"))
    frontend_origin: str = os.getenv("FRONTEND_ORIGIN", "http://127.0.0.1:5174")
    target_url: str = os.getenv("TARGET_URL", "")
    auth_poll_seconds: float = float(os.getenv("AUTH_POLL_SECONDS", "2"))
    currency_selector: str = os.getenv("AUTH_CURRENCY_SELECTOR", "")
    currency_text: str = os.getenv("AUTH_CURRENCY_TEXT", "RUB")
    currency_selector_fallbacks: tuple[str, ...] = tuple(
        item.strip()
        for item in os.getenv("AUTH_CURRENCY_SELECTOR_FALLBACKS", "").split("||")
        if item.strip()
    )
    profile_dir: Path = ROOT / "browser_profile"
    observation_db: Path = ROOT / "observations.sqlite3"
    game_list_url: str = os.getenv("GAME_LIST_URL", os.getenv("TARGET_URL", ""))
    observation_poll_seconds: float = float(os.getenv("OBSERVATION_POLL_SECONDS", "1"))
    game_active_selector: str = os.getenv("GAME_ACTIVE_SELECTOR", "")
    round_finished_selector: str = os.getenv("ROUND_FINISHED_SELECTOR", "")


settings = Settings()
