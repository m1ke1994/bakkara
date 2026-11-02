"""Create a private Docker production .env with individually salted password hashes.

Run on the server from the checked-out production branch:
    python3 ops/create_env.py
Never commit .env or paste its values into a chat.
"""
from __future__ import annotations

from getpass import getpass
import os
from pathlib import Path
from secrets import token_hex
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))
from app.dashboard_auth import password_hash


def main() -> None:
    destination = ROOT / ".env"
    if destination.exists():
        raise SystemExit("Private .env already exists: refusing to overwrite it")

    template = (ROOT / ".env.production.example").read_text(encoding="utf-8")
    aslan = getpass("Password for Aslan1994@gmail.com (not displayed): ")
    tedeev = getpass("Password for Tedeev1994@gmail.com (not displayed): ")
    if min(len(aslan), len(tedeev)) < 8:
        raise SystemExit("Each password needs at least eight characters")

    changes = {
        "BAKKARA_ASLAN_PASSWORD_HASH=": "BAKKARA_ASLAN_PASSWORD_HASH='" + password_hash(aslan) + "'",
        "BAKKARA_TEDEEV_PASSWORD_HASH=": "BAKKARA_TEDEEV_PASSWORD_HASH='" + password_hash(tedeev) + "'",
        "BAKKARA_SESSION_SECRET=": "BAKKARA_SESSION_SECRET=" + token_hex(32),
        "BROWSER_PROFILE_DIR=/var/lib/bakkara/chromium": "BROWSER_PROFILE_DIR=/data/chromium",
        "OBSERVATION_DB=/var/lib/bakkara/observations.sqlite3": "OBSERVATION_DB=/data/observations.sqlite3",
    }
    for before, after in changes.items():
        if template.count(before) != 1:
            raise SystemExit(f"Expected exactly one setting: {before}")
        template = template.replace(before, after)

    descriptor = os.open(destination, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
        stream.write(template)
    print("Private .env created (mode 0600); passwords and session key not printed.")


if __name__ == "__main__":
    main()
