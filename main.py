import requests
import time
import os
import json
from datetime import datetime
import yfinance as yf

TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")

if not TOKEN:
    print("TOKEN tidak ditemukan!")
    exit()

URL = f"https://api.telegram.org/bot{TOKEN}"
last_update_id = 0

WATCHLIST = [
    "BRIS",
    "ANTM",
    "INCO",
    "PTBA",
    "TLKM",
    "MDKA",
    "ADMR",
    "EXCL",
    "ICBP",
    "INDF",
    "KLBF",
    "SIDO",
    "MAPI",
    "ACES",
    "ERAA"
]

CHAT_FILE = "entry_chat.json"
STATE_FILE = "entry_state.json"

chat_id_global = None
state = {
    "autoscan": False,
    "last_sent_text": "",
    "last_scan_minute_key": ""
}

print("Entry Bot jalan dengan Top 5 autoscan + handoff + validate...")

def load_chat():
    global chat_id_global
    if os.path.exists(CHAT_FILE):
        try:
            with open(CHAT_FILE, "r") as f:
                data = json.load(f)
                chat_id_global = data.get("chat_id")
        except:
            chat_id_global = None

def save_chat():
    with open(CHAT_FILE, "w") as f:
        json.dump({"chat_id": chat_id_global}, f)

def load_state():
    global state
    if os.path.exists(STATE_FILE):
        try:
            with open(STATE_FILE, "r") as f:
                state = json.load(f)
        except:
            pass

def save_state():
    with open(STATE_FILE, "w") as f:
        json.dump(state, f)

def send_message(chat_id, text):
    requests.post(
        f"{URL}/sendMessage",
        json={"chat_id": chat_id, "text": text},
        timeout=30
    )

def yahoo_symbol(symbol):
    symbol = symbol.upper().strip()
    if symbol.endswith(".JK"):
        return symbol
    return f"{symbol}.JK"

def validation_status(close, bid_low, bid_high, trigger, invalidation):
    if close > trigger * 1.005:
        return "❌ INVALID", "harga sudah terlalu tinggi, jangan kejar"
    if close <= invalidation * 1.002:
        return "❌ INVALID", "harga terlalu dekat area invalidation"
    if bid_low <= close <= trigger:
        return "✅ VALID ENTRY", "harga masih dekat area eksekusi dan belum lari"
    return "⚠️ WAIT", "setup ada, tapi tunggu harga masuk area ideal"

def get_market_snapshot(symbol):
    ys = yahoo_symbol(symbol)

    try:
        ticker = yf.Ticker(ys)
        hist = ticker.history(period="10d", interval="1d")

        if hist is None or hist.empty or len(hist) < 3:
            return None

        close = float(hist["Close"].iloc[-1])
        prev_close = float(hist["Close"].iloc[-2])
        prev2_close = float(hist["Close"].iloc[-3])
        high = float(hist["High"].iloc[-1])
        low = float(hist["Low"].iloc[-1])

        change_pct = ((close - prev_close) / prev_close) * 100 if prev_close else 0
        prev_change_pct = ((prev_close - prev2_close) / prev2_close) * 100 if prev2_close else 0

        day_range = high - low if high > low else 0.01
        close_range_pct = (close - low) / day_range

        score = 50
        reasons = []

        if close > prev_close:
            score += 12
            reasons.append("close di atas hari sebelumnya")

        if change_pct > 1:
            score += 10
            reasons.append("momentum harian > 1%")

        if change_pct > 2:
            score += 8
            reasons.append("momentum kuat > 2%")

        if close_range_pct >= 0.7:
            score += 8
            reasons.append("close dekat high")

        if prev_change_pct > 0 and change_pct > 0:
            score += 7
            reasons.append("2 hari berturut bullish")

        if close >= high * 0.98:
            score += 5
            reasons.append("tekan area high")

        if change_pct < 0:
            score -= 10

        if close_range_pct < 0.4:
            score -= 5

        if score >= 80:
            setup = "BREAKOUT PREPARE"
        elif score >= 68:
            setup = "PULLBACK PREPARE"
        else:
            setup = "WATCH ONLY"

        bid_low = round(close * 0.9975, 2)
        bid_high = round(close * 0.9995, 2)
        trigger = round(close * 1.0025, 2)
        invalidation = round(close * 0.9925, 2)

        tp1 = round(close * 1.01, 2)
        tp2 = round(close * 1.02, 2)

        v_status, v_reason = validation_status(close, bid_low, bid_high, trigger, invalidation)

        return {
            "symbol": symbol.upper(),
            "close": round(close, 2),
            "change_pct": round(change_pct, 2),
            "score": int(score),
            "setup": setup,
            "bid_low": bid_low,
            "bid_high": bid_high,
            "trigger": trigger,
            "invalidation": invalidation,
            "tp1": tp1,
            "tp2": tp2,
            "validation": v_status,
            "validation_reason": v_reason,
            "reason": ", ".join(reasons[:2]) if reasons else "belum ada alasan kuat"
        }

    except Exception as e:
        print("Yahoo error:", symbol, e)
        return None

def build_watchlist_text():
    text = "Watchlist saham syariah:\n\n"
    for s in WATCHLIST:
        text += f"- {s}\n"
    return text

def build_scan_text():
    results = []

    for symbol in WATCHLIST:
        data = get_market_snapshot(symbol)
        if data:
            results.append(data)

    if not results:
        return "Data scan belum tersedia."

    results.sort(key=lambda x: x["score"], reverse=True)
    top = results[:5]

    lines = ["TOP 5 SETUP HARI INI\n"]

    for i, item in enumerate(top, start=1):
        lines.append(
            f"{i}. {item['symbol']}\n"
            f"Score: {item['score']}\n"
            f"Setup: {item['setup']}\n"
            f"Harga: {item['close']:.2f} ({item['change_pct']:+.2f}%)\n"
            f"Bid Zone: {item['bid_low']:.2f} - {item['bid_high']:.2f}\n"
            f"Trigger: {item['trigger']:.2f}\n"
            f"Invalidation: {item['invalidation']:.2f}\n"
            f"Status: {item['validation']}\n"
            f"Validasi: {item['validation_reason']}\n"
            f"Alasan: {item['reason']}\n\n"
            f"Handoff ke Exit Bot:\n"
            f"/startpos {item['symbol']} {item['close']:.2f}\n"
            f"/setsl {item['symbol']} {item['invalidation']:.2f}\n"
            f"/settp {item['symbol']} {item['tp1']:.2f} {item['tp2']:.2f}\n"
        )

    lines.append("Catatan: fokus pada yang VALID ENTRY, hindari yang INVALID.")
    return "\n".join(lines)

def try_autoscan():
    global state, chat_id_global

    if not chat_id_global:
        return

    if not state.get("autoscan"):
        return

    now = datetime.now()
    if now.minute not in [0, 15, 30, 45]:
        return

    minute_key = now.strftime("%Y-%m-%d %H:%M")
    if state.get("last_scan_minute_key") == minute_key:
        return

    scan_text = build_scan_text()

    if scan_text != state.get("last_sent_text", ""):
        send_message(chat_id_global, "AUTOSCAN AKTIF\n\n" + scan_text)
        state["last_sent_text"] = scan_text

    state["last_scan_minute_key"] = minute_key
    save_state()

def handle_command(chat_id, text):
    global chat_id_global, state

    cmd = text.strip().lower()

    if cmd == "/start":
        send_message(
            chat_id,
            "Entry Bot aktif (Top 5 + autoscan + handoff + validate).\n\n"
            "Command:\n"
            "/watchlist\n"
            "/scan\n"
            "/autoscanon\n"
            "/autoscanoff\n"
            "/statusauto"
        )
        return

    if cmd == "/watchlist":
        send_message(chat_id, build_watchlist_text())
        return

    if cmd == "/scan":
        send_message(chat_id, "Sedang scan watchlist Top 5...")
        send_message(chat_id, build_scan_text())
        return

    if cmd == "/autoscanon":
        state["autoscan"] = True
        save_state()
        send_message(chat_id, "Autoscan diaktifkan. Bot akan cek tiap 15 menit dan kirim hanya jika hasil berubah.")
        return

    if cmd == "/autoscanoff":
        state["autoscan"] = False
        save_state()
        send_message(chat_id, "Autoscan dimatikan.")
        return

    if cmd == "/statusauto":
        status = "ON" if state.get("autoscan") else "OFF"
        send_message(chat_id, f"Status autoscan: {status}")
        return

    send_message(chat_id, "Perintah tidak dikenal. Gunakan /start")

load_chat()
load_state()

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
                chat_id_global = chat_id
                save_chat()
                handle_command(chat_id, text)

        try_autoscan()
        time.sleep(5)

    except Exception as e:
        print("Error:", e)
        time.sleep(5)
