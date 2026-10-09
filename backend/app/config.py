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
    card_poll_seconds: float = float(os.getenv("CARD_POLL_SECONDS", "0.15"))
    game_active_selector: str = os.getenv("GAME_ACTIVE_SELECTOR", "")
    round_finished_selector: str = os.getenv(
        "ROUND_FINISHED_SELECTOR", ".ui-caption--size-xl.ui-caption--weight-700"
    ).strip() or ".ui-caption--size-xl.ui-caption--weight-700"
    player_name_text: str = os.getenv("PLAYER_NAME_TEXT", "Игрок")
    banker_name_text: str = os.getenv("BANKER_NAME_TEXT", "Банкир")
    game_page_wait_seconds: float = float(os.getenv("GAME_PAGE_WAIT_SECONDS", "5"))
    game_finish_timeout_seconds: float = float(os.getenv("GAME_FINISH_TIMEOUT_SECONDS", "180"))
    player_hand_timeout_seconds: float = float(os.getenv("PLAYER_HAND_TIMEOUT_SECONDS", "90"))
    incomplete_retry_seconds: float = float(os.getenv("INCOMPLETE_RETRY_SECONDS", "30"))


settings = Settings()
