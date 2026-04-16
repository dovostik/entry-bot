import requests
import time
import os

TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")

if not TOKEN:
    print("TOKEN tidak ditemukan!")
    exit()

URL = f"https://api.telegram.org/bot{TOKEN}"
last_update_id = 0

print("Entry Bot jalan...")

WATCHLIST = [
    "BRIS",
    "ANTM",
    "INCO",
    "PTBA",
    "TLKM"
]

def send_message(chat_id, text):
    requests.post(
        f"{URL}/sendMessage",
        json={"chat_id": chat_id, "text": text},
        timeout=30
    )

def build_watchlist_text():
    text = "Watchlist saham syariah:\n\n"
    for s in WATCHLIST:
        text += f"- {s}\n"
    return text

def build_entry_test_text():
    return (
        "ENTRY TEST\n\n"
        "1. BRIS\n"
        "Setup: Breakout Prepare\n"
        "Bid Zone: 2440-2444\n"
        "Trigger: 2446\n"
        "Invalidation: 2432\n\n"
        "2. ANTM\n"
        "Setup: Pullback Prepare\n"
        "Bid Zone: 1830-1834\n"
        "Trigger: 1838\n"
        "Invalidation: 1818"
    )

def handle_command(chat_id, text):
    cmd = text.strip().lower()

    if cmd == "/start":
        send_message(
            chat_id,
            "Entry Bot aktif.\n\n"
            "Command:\n"
            "/watchlist\n"
            "/entrytest\n"
            "/entrystart"
        )
        return

    if cmd == "/watchlist":
        send_message(chat_id, build_watchlist_text())
        return

    if cmd == "/entrytest":
        send_message(chat_id, build_entry_test_text())
        return

    if cmd == "/entrystart":
        send_message(
            chat_id,
            "Mode entry dimulai.\n"
            "Versi awal ini masih manual sederhana.\n"
            "Gunakan /watchlist dan /entrytest dulu."
        )
        return

    send_message(chat_id, "Perintah tidak dikenal. Gunakan /start")

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
                handle_command(chat_id, text)

        time.sleep(2)

    except Exception as e:
        print("Error:", e)
        time.sleep(5)
