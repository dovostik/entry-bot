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

WATCHLIST = [
    "BRIS","ANTM","INCO","PTBA","TLKM",
    "MDKA","ADMR","EXCL","ICBP","INDF",
    "KLBF","SIDO","MAPI","ACES","ERAA"
]

def send_message(chat_id, text):
    requests.post(f"{URL}/sendMessage", json={"chat_id": chat_id, "text": text}, timeout=30)

def yahoo_symbol(symbol):
    return symbol if symbol.endswith(".JK") else f"{symbol}.JK"

def get_market_snapshot(symbol):
    try:
        ticker = yf.Ticker(yahoo_symbol(symbol))
        hist = ticker.history(period="10d", interval="1d")

        if hist is None or hist.empty or len(hist) < 6:
            return None

        close = float(hist["Close"].iloc[-1])
        prev_close = float(hist["Close"].iloc[-2])
        high = float(hist["High"].iloc[-1])

        volume_today = float(hist["Volume"].iloc[-1])
        volume_avg = float(hist["Volume"].iloc[-6:-1].mean())

        change_pct = ((close - prev_close) / prev_close) * 100

        if volume_today > volume_avg * 1.2:
            volume_label = "Kuat ✅"
            volume_score = 10
        elif volume_today < volume_avg * 0.8:
            volume_label = "Lemah ❌"
            volume_score = -10
        else:
            volume_label = "Normal ⚠️"
            volume_score = 0

        score = 50 + volume_score

        if change_pct > 1:
            score += 10
        if close >= high * 0.98:
            score += 5

        if score >= 80:
            setup = "BREAKOUT PREPARE"
        elif score >= 65:
            setup = "PULLBACK PREPARE"
        else:
            setup = "WATCH ONLY"

        bid_low = round(close * 0.9975, 2)
        bid_high = round(close * 0.9995, 2)
        trigger = round(close * 1.0025, 2)
        invalidation = round(close * 0.9925, 2)

        if close > trigger * 1.005:
            status = "❌ INVALID"
        elif bid_low <= close <= trigger:
            status = "✅ VALID ENTRY"
        else:
            status = "⚠️ WAIT"

        return {
            "symbol": symbol,
            "score": int(score),
            "setup": setup,
            "close": round(close,2),
            "change_pct": round(change_pct,2),
            "volume": volume_label,
            "status": status,
            "bid_low": bid_low,
            "bid_high": bid_high,
            "trigger": trigger,
            "invalidation": invalidation
        }

    except:
        return None

def build_scan():
    results = []

    for s in WATCHLIST:
        data = get_market_snapshot(s)
        if data:
            results.append(data)

    results.sort(key=lambda x: x["score"], reverse=True)
    top = results[:5]

    text = "TOP 5 + VOLUME\n\n"

    for i, d in enumerate(top,1):
        text += f"{i}. {d['symbol']}\n"
        text += f"Score: {d['score']}\n"
        text += f"Setup: {d['setup']}\n"
        text += f"Harga: {d['close']} ({d['change_pct']}%)\n"
        text += f"Volume: {d['volume']}\n"
        text += f"Status: {d['status']}\n"
        text += f"Bid: {d['bid_low']} - {d['bid_high']}\n"
        text += f"Trigger: {d['trigger']}\n"
        text += f"Invalidation: {d['invalidation']}\n\n"

    return text

while True:
    try:
        res = requests.get(f"{URL}/getUpdates", params={"offset": last_update_id + 1}).json()

        for u in res.get("result", []):
            last_update_id = u["update_id"]

            if "message" in u:
                chat_id = u["message"]["chat"]["id"]
                text = u["message"].get("text","")

                if text == "/scan":
                    send_message(chat_id, build_scan())
                elif text == "/start":
                    send_message(chat_id, "Gunakan /scan untuk melihat Top 5 + Volume")

        time.sleep(2)

    except Exception as e:
        print(e)
        time.sleep(5)
