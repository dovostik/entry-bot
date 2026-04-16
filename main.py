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
        hist = ticker.history(period="15d", interval="1d")

        if hist is None or hist.empty or len(hist) < 7:
            return None

        close = float(hist["Close"].iloc[-1])
        prev_close = float(hist["Close"].iloc[-2])
        high = float(hist["High"].iloc[-1])
        low = float(hist["Low"].iloc[-1])
        open_price = float(hist["Open"].iloc[-1])

        volume_today = float(hist["Volume"].iloc[-1])
        volume_avg = float(hist["Volume"].iloc[-6:-1].mean())

        change_pct = ((close - prev_close) / prev_close) * 100 if prev_close else 0
        day_range = high - low if high > low else 0.01
        close_range_pct = (close - low) / day_range
        upper_wick_pct = (high - max(open_price, close)) / day_range

        recent_high = float(hist["High"].iloc[-6:-1].max())
        breakout_attempt = high >= recent_high * 0.995

        # ===== volume label =====
        if volume_today > volume_avg * 1.2:
            volume_label = "Kuat ✅"
            volume_score = 10
        elif volume_today < volume_avg * 0.8:
            volume_label = "Lemah ❌"
            volume_score = -10
        else:
            volume_label = "Normal ⚠️"
            volume_score = 0

        # ===== fake breakout detection =====
        fake_breakout = False
        fake_reason = "-"
        if breakout_attempt and close < recent_high and upper_wick_pct > 0.35:
            fake_breakout = True
            fake_reason = "sempat tekan high tapi close lemah"
        elif breakout_attempt and close_range_pct < 0.45:
            fake_breakout = True
            fake_reason = "close di bawah pertengahan range"
        elif breakout_attempt and change_pct < 0.3:
            fake_breakout = True
            fake_reason = "breakout tidak punya follow-through"

        # ===== score =====
        score = 50 + volume_score

        if change_pct > 1:
            score += 10
        if close >= high * 0.98:
            score += 5
        if close_range_pct >= 0.7:
            score += 8

        if fake_breakout:
            score -= 18

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

        if fake_breakout:
            status = "❌ INVALID"
            validation = "indikasi fake breakout, jangan kejar"
        elif close > trigger * 1.005:
            status = "❌ INVALID"
            validation = "harga sudah terlalu tinggi, jangan kejar"
        elif bid_low <= close <= trigger:
            status = "✅ VALID ENTRY"
            validation = "harga masih dekat area eksekusi"
        else:
            status = "⚠️ WAIT"
            validation = "setup ada, tunggu area ideal"

        fake_label = "Ya ❌" if fake_breakout else "Tidak ✅"

        return {
            "symbol": symbol,
            "score": int(score),
            "setup": setup,
            "close": round(close, 2),
            "change_pct": round(change_pct, 2),
            "volume": volume_label,
            "status": status,
            "validation": validation,
            "bid_low": bid_low,
            "bid_high": bid_high,
            "trigger": trigger,
            "invalidation": invalidation,
            "fake_breakout": fake_label,
            "fake_reason": fake_reason
        }

    except Exception as e:
        print("error", symbol, e)
        return None

def build_scan():
    results = []
    for s in WATCHLIST:
        data = get_market_snapshot(s)
        if data:
            results.append(data)

    results.sort(key=lambda x: x["score"], reverse=True)
    top = results[:5]

    text = "TOP 5 + VOLUME + FAKE BREAKOUT\n\n"
    for i, d in enumerate(top, 1):
        text += f"{i}. {d['symbol']}\n"
        text += f"Score: {d['score']}\n"
        text += f"Setup: {d['setup']}\n"
        text += f"Harga: {d['close']} ({d['change_pct']}%)\n"
        text += f"Volume: {d['volume']}\n"
        text += f"Status: {d['status']}\n"
        text += f"Validasi: {d['validation']}\n"
        text += f"Fake Breakout: {d['fake_breakout']}\n"
        text += f"Catatan Fake: {d['fake_reason']}\n"
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
                text = u["message"].get("text","").strip().lower()

                if text == "/scan":
                    send_message(chat_id, build_scan())
                elif text == "/start":
                    send_message(chat_id, "Gunakan /scan untuk melihat Top 5 + Volume + Fake Breakout")

        time.sleep(2)

    except Exception as e:
        print(e)
        time.sleep(5)
