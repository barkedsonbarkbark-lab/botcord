"""
discord_api.py
Thin synchronous wrapper around Discord's official Bot (REST) API.
Every call uses "Authorization: Bot <token>" - the sanctioned way to drive
a bot account. Includes basic 429 rate-limit handling.
"""

import time
import requests

BASE = "https://discord.com/api/v10"


class DiscordAPIError(Exception):
    pass


def _headers(token: str) -> dict:
    return {"Authorization": f"Bot {token}", "Content-Type": "application/json"}


def _req(method, token, path, **kw):
    for _ in range(3):
        r = requests.request(method, BASE + path, headers=_headers(token), timeout=15, **kw)
        if r.status_code == 429:
            try:
                wait = float(r.json().get("retry_after", 1))
            except Exception:
                wait = 1.0
            time.sleep(min(wait, 10))
            continue
        if r.status_code >= 400:
            try:
                msg = r.json().get("message", r.text[:200])
            except Exception:
                msg = r.text[:200]
            raise DiscordAPIError(f"{r.status_code}: {msg}")
        return r.json() if r.content else {}
    raise DiscordAPIError("rate limited")


def get_me(token):
    return _req("GET", token, "/users/@me")


def get_user(token, user_id):
    return _req("GET", token, f"/users/{user_id}")


def avatar_url(user, size=64):
    if not user or not user.get("avatar"):
        return None
    return f"https://cdn.discordapp.com/avatars/{user['id']}/{user['avatar']}.png?size={size}"


def get_avatar_url(user):
    return avatar_url(user, 128)


def icon_url(guild_id, icon, size=64):
    if not icon:
        return None
    return f"https://cdn.discordapp.com/icons/{guild_id}/{icon}.png?size={size}"


def get_guilds(token, limit=100, after=None):
    """One page of the bot's servers (Discord returns at most 200 per call)."""
    params = {"limit": limit}
    if after:
        params["after"] = after
    return _req("GET", token, "/users/@me/guilds", params=params)


def get_guild_channels(token, guild_id):
    """All channel types (text 0, voice 2, category 4, announcement 5, stage 13)."""
    return _req("GET", token, f"/guilds/{guild_id}/channels")


def get_messages(token, channel_id, limit=50, before=None):
    params = {"limit": limit}
    if before:
        params["before"] = before
    return list(reversed(_req("GET", token, f"/channels/{channel_id}/messages", params=params)))


def send_message(token, channel_id, content, reply_to=None, nonce=None):
    body = {"content": content}
    if nonce:
        # the gateway echoes the nonce back, so the UI can match its own message
        body["nonce"] = str(nonce)
        body["enforce_nonce"] = True
    if reply_to:
        body["message_reference"] = {"message_id": reply_to, "fail_if_not_exists": False}
    return _req("POST", token, f"/channels/{channel_id}/messages", json=body)


def edit_message(token, channel_id, message_id, content):
    return _req("PATCH", token, f"/channels/{channel_id}/messages/{message_id}", json={"content": content})


def delete_message(token, channel_id, message_id):
    return _req("DELETE", token, f"/channels/{channel_id}/messages/{message_id}")


def trigger_typing(token, channel_id):
    return _req("POST", token, f"/channels/{channel_id}/typing")


def open_dm(token, user_id):
    """Opens (or fetches) a DM channel. Discord enforces the shared-server rule."""
    return _req("POST", token, "/users/@me/channels", json={"recipient_id": user_id})
