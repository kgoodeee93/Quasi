"""
Telegram push notifications. Fire-and-forget: if Telegram is down or the keys
are missing, notifications silently no-op so trading is never blocked by a
failed ping.

Setup (2 minutes, one time):
  1. In Telegram, message @BotFather, send /newbot, follow the prompts, and
     copy the bot token it gives you into TELEGRAM_BOT_TOKEN in .env.
  2. Open a chat with your new bot and send it any message (say "hi"), or add the bot to a
     private channel as an admin and post there.
  3. Run:  python notify.py
     It prints the chat id. Put that in TELEGRAM_CHAT_ID in .env.

Use a private chat for trade pings, not the channel your alert subscribers read.
"""

import html
import os

import requests
from dotenv import load_dotenv


def esc(value) -> str:
    """Escape text for Telegram HTML. Token names and error messages are not ours: a '<' in
    either makes Telegram reject the whole message, and a failed-sell alert would vanish."""
    return html.escape(str(value), quote=False)


def send(text):
    """Send a Telegram message. Never raises; returns True/False.

    Reads the settings at send time, so it works whether or not .env was loaded before this
    module was imported."""
    token = os.environ.get("TELEGRAM_BOT_TOKEN", "")
    chat_id = os.environ.get("TELEGRAM_CHAT_ID", "")
    if not token or not chat_id:
        return False
    try:
        r = requests.post(
            f"https://api.telegram.org/bot{token}/sendMessage",
            json={"chat_id": chat_id, "text": text,
                  "parse_mode": "HTML", "disable_web_page_preview": True},
            timeout=10)
        if not r.ok:
            print(f"[tg] Telegram refused the message (HTTP {r.status_code})")
        return r.ok
    except Exception as e:
        # requests puts the URL (with the bot token) in some errors: print only the type.
        print(f"[tg] notify failed: {type(e).__name__}")
        return False


def find_chat_id():
    """Print the chat id of whoever last messaged the bot (or the channel it last saw a post in)."""
    load_dotenv()
    token = os.environ.get("TELEGRAM_BOT_TOKEN", "")
    if not token:
        print("Set TELEGRAM_BOT_TOKEN in .env first.")
        return
    r = requests.get(f"https://api.telegram.org/bot{token}/getUpdates", timeout=10)
    data = r.json()
    if not data.get("result"):
        print("No messages yet. Open your bot in Telegram, send it 'hi' (or post in the channel "
              "it admins), then run this again.")
        return
    for upd in reversed(data["result"]):
        chat = (upd.get("message") or upd.get("channel_post") or {}).get("chat")
        if chat:
            print(f"chat id: {chat['id']}   (put this in TELEGRAM_CHAT_ID)")
            return
    print("Found updates but no chat — send your bot a direct message and retry.")


if __name__ == "__main__":
    find_chat_id()
