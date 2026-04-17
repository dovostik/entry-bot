# ENTRY BOT UPGRADE: MA, RSI, MACD, VOLUME
# requirements.txt:
# requests
# yfinance
# pandas

import requests
import time
import os
import pandas as pd
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

def send(chat_id, text):
    requests.post(f"{URL}/sendMessage", json={"chat_id": chat_id, "text": text})

def ys(s): return s if s.endswith(".JK") else f"{s}.JK"

def calc_indicators(df):
    df["MA20"] = df["Close"].rolling(20).mean()
    df["MA50"] = df["Close"].rolling(50).mean()
    df["MA100"] = df["Close"].rolling(100).mean()
    df["MA200"] = df["Close"].rolling(200).mean()

    delta = df["Close"].diff()
    gain = delta.clip(lower=0).rolling(14).mean()
    loss = (-delta.clip(upper=0)).rolling(14).mean()
    rs = gain / loss
    df["RSI"] = 100 - (100 / (1 + rs))

    ema12 = df["Close"].ewm(span=12).mean()
    ema26 = df["Close"].ewm(span=26).mean()
    df["MACD"] = ema12 - ema26
    df["SIGNAL"] = df["MACD"].ewm(span=9).mean()

    return df

def analyze(symbol):
    try:
        df = yf.Ticker(ys(symbol)).history(period="6mo")
        if df is None or df.empty:
            return None

        df = calc_indicators(df)

        last = df.iloc[-1]

        close = last["Close"]
        ma20 = last["MA20"]
        ma50 = last["MA50"]
        rsi = last["RSI"]
        macd = last["MACD"]
        signal = last["SIGNAL"]

        volume = last["Volume"]
        avg_vol = df["Volume"].rolling(5).mean().iloc[-1]

        score = 50
        notes = []

        if close > ma20:
            score += 10
            notes.append("di atas MA20")
        if close > ma50:
            score += 10
            notes.append("di atas MA50")
        if rsi > 55:
            score += 10
            notes.append("RSI kuat")
        if macd > signal:
            score += 10
            notes.append("MACD bullish")
        if volume > avg_vol * 1.2:
            score += 10
            notes.append("volume kuat")

        return {
            "symbol": symbol,
            "score": int(score),
            "close": round(close,2),
            "rsi": round(rsi,1),
            "notes": ", ".join(notes)
        }

    except:
        return None

def scan():
    res = []
    for s in WATCHLIST:
        d = analyze(s)
        if d:
            res.append(d)

    res.sort(key=lambda x: x["score"], reverse=True)
    top = res[:5]

    text = "TOP 5 + TEKNIKAL\n\n"
    for i,d in enumerate(top,1):
        text += f"{i}. {d['symbol']}\n"
        text += f"Score: {d['score']}\n"
        text += f"Harga: {d['close']}\n"
        text += f"RSI: {d['rsi']}\n"
        text += f"Alasan: {d['notes']}\n\n"

    return text

while True:
    try:
        r = requests.get(f"{URL}/getUpdates", params={"offset": last_update_id+1}).json()

        for u in r.get("result", []):
            last_update_id = u["update_id"]
            if "message" in u:
                chat_id = u["message"]["chat"]["id"]
                text = u["message"].get("text","")

                if text == "/scan":
                    send(chat_id, scan())
                elif text == "/start":
                    send(chat_id, "Gunakan /scan")

        time.sleep(2)

    except Exception as e:
        print(e)
        time.sleep(5)