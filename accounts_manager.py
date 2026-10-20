"""
accounts_manager.py
Local-only encrypted storage for saved bot logins.

Nothing in this file talks to the network. It only reads/writes an
encrypted JSON blob on-device. The Fernet key is generated on first
run and stored alongside the data file (both inside the app's private
storage directory on Android, or ./botcord_data locally for desktop
testing).
"""

import os
import json
from cryptography.fernet import Fernet

try:
    from android.storage import app_storage_path  # type: ignore
    DATA_DIR = app_storage_path()
except Exception:
    DATA_DIR = os.path.join(os.path.expanduser("~"), ".botcord_data")

os.makedirs(DATA_DIR, exist_ok=True)

KEY_PATH = os.path.join(DATA_DIR, "botcord.key")
DB_PATH = os.path.join(DATA_DIR, "accounts.enc")
AVATAR_DIR = os.path.join(DATA_DIR, "avatars")
os.makedirs(AVATAR_DIR, exist_ok=True)


def _get_key() -> bytes:
    if os.path.exists(KEY_PATH):
        with open(KEY_PATH, "rb") as f:
            return f.read()
    key = Fernet.generate_key()
    with open(KEY_PATH, "wb") as f:
        f.write(key)
    try:
        os.chmod(KEY_PATH, 0o600)
    except Exception:
        pass
    return key


_fernet = Fernet(_get_key())


def _load_raw() -> dict:
    if not os.path.exists(DB_PATH):
        return {"accounts": []}
    with open(DB_PATH, "rb") as f:
        blob = f.read()
    if not blob:
        return {"accounts": []}
    try:
        decrypted = _fernet.decrypt(blob)
        return json.loads(decrypted.decode("utf-8"))
    except Exception:
        # Corrupt or foreign file -> start fresh rather than crash.
        return {"accounts": []}


def _save_raw(data: dict):
    encrypted = _fernet.encrypt(json.dumps(data).encode("utf-8"))
    with open(DB_PATH, "wb") as f:
        f.write(encrypted)
    try:
        os.chmod(DB_PATH, 0o600)
    except Exception:
        pass


def list_accounts() -> list:
    """Returns saved accounts WITHOUT tokens (safe for list UI)."""
    data = _load_raw()
    return [
        {
            "id": a["id"],
            "username": a.get("username"),
            "display_name": a.get("display_name"),
            "avatar_path": a.get("avatar_path"),
        }
        for a in data["accounts"]
    ]


def get_token(account_id: str) -> str:
    data = _load_raw()
    for a in data["accounts"]:
        if a["id"] == account_id:
            return a["token"]
    raise KeyError("account not found")


def upsert_account(account_id: str, token: str, username: str,
                    display_name: str, avatar_path: str | None):
    data = _load_raw()
    accounts = data["accounts"]
    for a in accounts:
        if a["id"] == account_id:
            a.update(
                token=token,
                username=username,
                display_name=display_name,
                avatar_path=avatar_path,
            )
            break
    else:
        accounts.append(
            {
                "id": account_id,
                "token": token,
                "username": username,
                "display_name": display_name,
                "avatar_path": avatar_path,
            }
        )
    _save_raw(data)


def remove_account(account_id: str):
    data = _load_raw()
    target = next((a for a in data["accounts"] if a["id"] == account_id), None)
    data["accounts"] = [a for a in data["accounts"] if a["id"] != account_id]
    _save_raw(data)
    if target and target.get("avatar_path") and os.path.exists(target["avatar_path"]):
        try:
            os.remove(target["avatar_path"])
        except OSError:
            pass
