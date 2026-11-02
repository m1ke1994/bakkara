"""Dashboard access for the dedicated production branch."""
import base64
import binascii
import hashlib
import hmac
import json
import os
import secrets
import time
from threading import Lock

USERS = {"aslan1994@gmail.com": "BAKKARA_ASLAN_PASSWORD_HASH",
         "tedeev1994@gmail.com": "BAKKARA_TEDEEV_PASSWORD_HASH"}
COOKIE = "__Host-bakkara_session"
MAX_AGE = 12 * 3600
ITERATIONS = 310_000
failures = {}
lock = Lock()


def enabled():
    return os.getenv("BAKKARA_AUTH_ENABLED", "0") == "1"


def encode(value):
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")


def decode(value):
    return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))


def password_hash(password):
    salt = secrets.token_bytes(24)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, ITERATIONS)
    return "pbkdf2_sha256$%s$%s$%s" % (ITERATIONS, encode(salt), encode(digest))


def check_password(password, hashed):
    try:
        algorithm, iterations, salt, digest = hashed.split("$")
        count = int(iterations)
        if algorithm != "pbkdf2_sha256" or not 100000 <= count <= 1000000:
            return False
        actual = hashlib.pbkdf2_hmac("sha256", password.encode(), decode(salt), count)
        return hmac.compare_digest(actual, decode(digest))
    except (ValueError, TypeError, OverflowError):
        return False


def validate():
    if not enabled():
        return
    if len(os.getenv("BAKKARA_SESSION_SECRET", "")) < 32:
        raise RuntimeError("BAKKARA_SESSION_SECRET must have at least 32 characters")
    for user, env_key in USERS.items():
        hashed = os.getenv(env_key, "")
        try:
            alg, count, salt, digest = hashed.split("$")
            if alg != "pbkdf2_sha256" or not 100000 <= int(count) <= 1000000:
                raise ValueError("invalid algorithm")
            if len(decode(salt)) < 16 or len(decode(digest)) != 32:
                raise ValueError("invalid salt/digest")
        except (ValueError, TypeError):
            raise RuntimeError("Missing or malformed password hash: " + user) from None


def authenticate(email, password):
    normalized = (email or "").strip().lower()
    hashed = os.getenv(USERS.get(normalized, ""), "")
    result = check_password(password, hashed) if hashed else False
    if not hashed:
        hashlib.pbkdf2_hmac("sha256", password.encode(), b"unknown_account", ITERATIONS)
    return normalized if result else None


def issue(email, now=None):
    at = int(time.time()) if now is None else now
    raw = encode(json.dumps({"email": email, "exp": at + MAX_AGE,
                             "nonce": secrets.token_hex(16)}, separators=(",", ":")).encode())
    signature = hmac.new(os.environ["BAKKARA_SESSION_SECRET"].encode(), raw.encode(), hashlib.sha256).digest()
    return raw + "." + encode(signature)


def verify(token, now=None):
    if not enabled() or not token:
        return None
    try:
        payload, digest = token.split(".", 1)
        expected = hmac.new(os.environ["BAKKARA_SESSION_SECRET"].encode(),
                            payload.encode(), hashlib.sha256).digest()
        if not hmac.compare_digest(expected, decode(digest)):
            return None
        data = json.loads(decode(payload))
        if data["email"] in USERS and isinstance(data["exp"], int) and data["exp"] > (int(time.time()) if now is None else now):
            return data["email"]
    except (ValueError, KeyError, TypeError, UnicodeError, binascii.Error):
        pass
    return None


def login_allowed(ip):
    now = time.monotonic()
    with lock:
        failures[ip] = [x for x in failures.get(ip, []) if now - x < 900]
        return len(failures[ip]) < 8


def login_failed(ip):
    with lock:
        failures.setdefault(ip, []).append(time.monotonic())


def login_succeeded(ip):
    with lock:
        failures.pop(ip, None)


if __name__ == "__main__":
    import getpass
    import sys
    if sys.argv[1:] != ["hash"]:
        raise SystemExit("Usage: python -m app.dashboard_auth hash")
    password = getpass.getpass("Password: ")
    if len(password) < 8:
        raise SystemExit("At least 8 characters")
    print(password_hash(password))
