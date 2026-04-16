import requests
import time
import os
import yfinance as yf

TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")

if not TOKEN:
    print("TOKEN tidak ditemukan!")
    exit()

URL = f"https://api.telegram.org/bot{TOKEN}"
last_update_id = 0

print("Entry Bot jalan dengan scan Top 5 harga real...")

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

        return {
            "symbol": symbol.upper(),
            "close": round(close, 2),
            "prev_close": round(prev_close, 2),
            "change_pct": round(change_pct, 2),
            "score": int(score),
            "setup": setup,
            "bid_low": bid_low,
            "bid_high": bid_high,
            "trigger": trigger,
            "invalidation": invalidation,
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
            f"Alasan: {item['reason']}\n"
        )

    lines.append("Catatan: siapkan antrean bid, jangan langsung kejar harga.")
    return "\n".join(lines)

def build_entry_test_text():
    return (
        "ENTRY TEST\n\n"
        "1. BRIS\n"
        "Setup: BREAKOUT PREPARE\n"
        "Bid Zone: 2440-2444\n"
        "Trigger: 2446\n"
        "Invalidation: 2432\n\n"
        "2. ANTM\n"
        "Setup: PULLBACK PREPARE\n"
        "Bid Zone: 1830-1834\n"
        "Trigger: 1838\n"
        "Invalidation: 1818"
    )

def handle_command(chat_id, text):
    cmd = text.strip().lower()

    if cmd == "/start":
        send_message(
            chat_id,
            "Entry Bot aktif (Top 5 scan harga real).\n\n"
            "Command:\n"
            "/watchlist\n"
            "/scan\n"
            "/entrytest\n"
            "/entrystart"
        )
        return

    if cmd == "/watchlist":
        send_message(chat_id, build_watchlist_text())
        return

    if cmd == "/scan":
        send_message(chat_id, "Sedang scan watchlist Top 5...")
        send_message(chat_id, build_scan_text())
        return

    if cmd == "/entrytest":
        send_message(chat_id, build_entry_test_text())
        return

    if cmd == "/entrystart":
        send_message(
            chat_id,
            "Mode entry dimulai.\n"
            "Gunakan /scan untuk melihat Top 5 setup terbaik hari ini."
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
