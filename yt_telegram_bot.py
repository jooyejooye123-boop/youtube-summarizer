#!/usr/bin/env python3
"""
YouTube Script Telegram Bot
===========================

Send a YouTube link to your bot in Telegram -> it runs the extractor on this
PC and sends the script back as a file. The phone only sends a URL.

Runs on your PC (keep it on while you use it). Uses long-polling, so it works
behind a home router with no public IP or port forwarding.

SETUP (once)
------------
1. In Telegram, open @BotFather -> /newbot -> pick a name -> copy the TOKEN.
2. Paste the token into BOT_TOKEN below.
3. Put this file in the SAME folder as yt_script_extractor.py (and cookies.txt).
4. Install the one dependency if needed:   pip install requests
5. Run it:   python yt_telegram_bot.py
6. In Telegram, find your bot and send any message. The console prints your
   chat_id -> paste that number into ALLOWED_CHAT_ID and restart (locks the bot
   to only you). Now send a YouTube link.

AUTH NOTE
---------
The bot must use an account that actually has the membership. Easiest & lowest
maintenance: log into that account in FIREFOX and set BROWSER = "firefox"
(no cookie-lock issues, always fresh). Or keep a fresh cookies.txt via
COOKIES_FILE and re-export it when it expires.
"""

import glob
import os
import re
import subprocess
import time

import requests

# ------------------------------- CONFIG ------------------------------------ #
BOT_TOKEN = "PASTE_YOUR_BOT_TOKEN_HERE"
LANG = "ko"                       # caption language
COOKIES_FILE = "cookies.txt"      # path to cookies.txt, OR leave "" to use a browser
BROWSER = ""                      # e.g. "firefox" / "chrome"; used only if COOKIES_FILE is ""
ALLOWED_CHAT_ID = None            # set to your numeric chat id (int) to lock the bot to you
EXTRACTOR = "yt_extract_file.py"
OUT_DIR = "scripts"
# --------------------------------------------------------------------------- #

API = f"https://api.telegram.org/bot{BOT_TOKEN}"
YT_RE = re.compile(r"https?://(?:www\.)?(?:youtube\.com|youtu\.be|m\.youtube\.com)/\S+")


def send(chat_id, text):
    try:
        requests.post(f"{API}/sendMessage", data={"chat_id": chat_id, "text": text}, timeout=20)
    except Exception as e:
        print("send error:", e)


def send_document(chat_id, path):
    try:
        with open(path, "rb") as f:
            requests.post(f"{API}/sendDocument", data={"chat_id": chat_id},
                          files={"document": f}, timeout=120)
    except Exception as e:
        print("send_document error:", e)


def run_extractor(url):
    """Run the CLI extractor and return (newest_output_file, stderr)."""
    args = ["python", EXTRACTOR, url, "--lang", LANG, "--out", OUT_DIR]
    if COOKIES_FILE:
        args += ["--cookies-file", COOKIES_FILE]
    elif BROWSER:
        args += ["--browser", BROWSER]

    before = set(glob.glob(os.path.join(OUT_DIR, "*")))
    proc = subprocess.run(args, capture_output=True, text=True)
    after = set(glob.glob(os.path.join(OUT_DIR, "*")))
    new_files = sorted(after - before, key=os.path.getmtime)
    return (new_files[-1] if new_files else None), proc.stderr


def main():
    if BOT_TOKEN.startswith("PASTE_"):
        print("ERROR: set BOT_TOKEN first (get it from @BotFather).")
        return
    # Quick sanity check on the token.
    me = requests.get(f"{API}/getMe", timeout=20).json()
    if not me.get("ok"):
        print("ERROR: bad BOT_TOKEN. Telegram said:", me)
        return
    print(f"Bot @{me['result']['username']} is running. Send it a YouTube link.")
    print("(Ctrl+C to stop.)  Waiting for messages...")

    offset = None
    while True:
        try:
            resp = requests.get(f"{API}/getUpdates",
                                params={"timeout": 30, "offset": offset}, timeout=40).json()
        except Exception as e:
            print("poll error:", e)
            time.sleep(3)
            continue

        for upd in resp.get("result", []):
            offset = upd["update_id"] + 1
            msg = upd.get("message") or upd.get("channel_post") or {}
            chat_id = (msg.get("chat") or {}).get("id")
            text = msg.get("text", "") or ""
            if not chat_id:
                continue

            print(f"message from chat_id={chat_id}: {text[:80]}")

            if ALLOWED_CHAT_ID and chat_id != ALLOWED_CHAT_ID:
                send(chat_id, "이 봇은 개인용입니다.")
                continue

            match = YT_RE.search(text)
            if not match:
                send(chat_id, "유튜브 링크를 보내주세요 🙂")
                continue

            send(chat_id, "추출 중... 잠시만요 ⏳")
            path, err = run_extractor(match.group(0))
            if path:
                send_document(chat_id, path)
                send(chat_id, "완료 ✅")
            else:
                tail = (err or "알 수 없는 오류").strip()[-600:]
                send(chat_id, "실패 ❌\n" + tail)


if __name__ == "__main__":
    main()
