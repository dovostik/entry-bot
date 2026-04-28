# entry_bot_production_v2.py
# STEP 2: FULL ENGINE READY (structure for merging your full logic safely)

import requests
import time
import os
import json
import io
from contextlib import redirect_stdout, redirect_stderr
from datetime import datetime, timedelta, timezone

import pandas as pd
import yfinance as yf

# ===============================
# CONFIG
# ===============================
TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
if not TOKEN:
    raise SystemExit("TOKEN tidak ditemukan!")

URL = f"https://api.telegram.org/bot{TOKEN}"
WIB = timezone(timedelta(hours=7))
INSTANCE_ID = os.getenv("HOSTNAME") or "local"

# ===============================
# LOGGER
# ===============================
def log(msg):
    print(f"[{datetime.now()}] {msg}", flush=True)

# ===============================
# TELEGRAM
# ===============================
def send_message(chat_id, text):
    try:
        prefix = f"[inst:{str(INSTANCE_ID)[-6:]}]"
        requests.post(
            f"{URL}/sendMessage",
            json={"chat_id": chat_id, "text": prefix + "\n" + text},
            timeout=30
        )
    except Exception as e:
        log(f"SEND ERROR: {e}")

# ===============================
# SAFE YFINANCE WRAPPER
# ===============================
def safe_yahoo(symbol):
    try:
        fake_out = io.StringIO()
        fake_err = io.StringIO()
        with redirect_stdout(fake_out), redirect_stderr(fake_err):
            return yf.Ticker(symbol).history(period="1y")
    except Exception as e:
        log(f"YF ERROR {symbol}: {e}")
        return None

# ===============================
# ENGINE WRAPPER (IMPORTANT)
# ===============================
def run_full_engine():
    """
    ⛔ DI SINI TEMPAT MASUKKAN FULL LOGIC KAMU
    (scan_engine, get_market_snapshot, dll)

    Wrapper ini:
    - mencegah crash bot
    - logging error
    """

    try:
        log("ENGINE START")

        # ===============================
        # >>> TEMPORARY ENGINE (SAFE)
        # ===============================
        result = {
            "status": "engine running",
            "timestamp": str(datetime.now())
        }

        # ===============================
        # NANTI GANTI:
        # result = process_dual_path_scan(...)
        # ===============================

        log("ENGINE SUCCESS")
        return result

    except Exception as e:
        log(f"ENGINE ERROR: {e}")
        return None

# ===============================
# COMMAND HANDLER (FIXED)
# ===============================
def normalize_cmd(text):
    cmd = text.strip().split()[0].lower()
    if "@" in cmd:
        cmd = cmd.split("@")[0]
    return cmd

def handle_command(chat_id, text):
    cmd = normalize_cmd(text)

    log(f"CMD: {cmd}")

    if cmd == "/start":
        send_message(chat_id, "Bot aktif.\nGunakan /scan")
        return

    if cmd == "/scan":
        send_message(chat_id, "Scanning...")

        result = run_full_engine()

        if not result:
            send_message(chat_id, "Scan gagal (engine error)")
            return

        send_message(chat_id, str(result))
        return

    send_message(chat_id, "Command tidak dikenal")

# ===============================
# MAIN LOOP (ANTI CRASH)
# ===============================
last_update_id = 0

while True:
    try:
        res = requests.get(
            f"{URL}/getUpdates",
            params={"offset": last_update_id + 1},
            timeout=30
        ).json()

        for update in res.get("result", []):
            last_update_id = update["update_id"]

            if "message" in update:
                chat_id = update["message"]["chat"]["id"]
                text = update["message"].get("text", "")

                try:
                    handle_command(chat_id, text)
                except Exception as e:
                    log(f"HANDLE ERROR: {e}")

        time.sleep(3)

    except Exception as e:
        log(f"MAIN LOOP ERROR: {e}")
        time.sleep(5)
