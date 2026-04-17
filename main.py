import requests
import time
import os
import json
from datetime import datetime
import pandas as pd
import yfinance as yf

TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
if not TOKEN:
    print("TOKEN tidak ditemukan!")
    exit()

URL = f"https://api.telegram.org/bot{TOKEN}"
last_update_id = 0

CHAT_FILE = "entry_chat.json"
STATE_FILE = "entry_state.json"
WATCHLIST_FILE = "watchlist_syariah.txt"

chat_id_global = None
state = {"autoscan": False, "last_sent_text": "", "last_scan_minute_key": ""}

def load_chat():
    global chat_id_global
    if os.path.exists(CHAT_FILE):
        try:
            with open(CHAT_FILE, "r", encoding="utf-8") as f:
                chat_id_global = json.load(f).get("chat_id")
        except Exception:
            chat_id_global = None

def save_chat():
    with open(CHAT_FILE, "w", encoding="utf-8") as f:
        json.dump({"chat_id": chat_id_global}, f)

def load_state():
    global state
    if os.path.exists(STATE_FILE):
        try:
            with open(STATE_FILE, "r", encoding="utf-8") as f:
                state = json.load(f)
        except Exception:
            pass

def save_state():
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(state, f)

def load_watchlist():
    if not os.path.exists(WATCHLIST_FILE):
        return ["BRIS","ANTM","PTBA","TLKM","INDF","ICBP","KLBF","EXCL","PGAS","CPIN"]
    items = []
    with open(WATCHLIST_FILE, "r", encoding="utf-8") as f:
        for raw in f:
            code = raw.strip().upper()
            if not code or code.startswith("#"):
                continue
            items.append(code)
    seen = set()
    out = []
    for x in items:
        if x not in seen:
            seen.add(x)
            out.append(x)
    return out

WATCHLIST = load_watchlist()

def send_message(chat_id, text):
    requests.post(f"{URL}/sendMessage", json={"chat_id": chat_id, "text": text}, timeout=30)

def yahoo_symbol(symbol):
    symbol = symbol.upper().strip()
    return symbol if symbol.endswith(".JK") else f"{symbol}.JK"

def calc_indicators(df):
    out = df.copy()
    out["MA20"] = out["Close"].rolling(20).mean()
    out["MA50"] = out["Close"].rolling(50).mean()
    out["MA100"] = out["Close"].rolling(100).mean()
    out["MA200"] = out["Close"].rolling(200).mean()

    delta = out["Close"].diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)
    avg_gain = gain.ewm(alpha=1/14, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1/14, adjust=False).mean()
    rs = avg_gain / avg_loss.replace(0, pd.NA)
    out["RSI"] = 100 - (100 / (1 + rs))
    out["RSI"] = out["RSI"].fillna(50)

    ema12 = out["Close"].ewm(span=12, adjust=False).mean()
    ema26 = out["Close"].ewm(span=26, adjust=False).mean()
    out["MACD"] = ema12 - ema26
    out["SIGNAL"] = out["MACD"].ewm(span=9, adjust=False).mean()
    out["VOLAVG5"] = out["Volume"].rolling(5).mean()
    return out

def timing_label(close, low, high):
    day_range = high - low if high > low else 0.01
    close_range_pct = (close - low) / day_range
    if close_range_pct < 0.50:
        return "EARLY", "masih awal gerakan"
    elif close_range_pct < 0.75:
        return "MID", "sudah bergerak, masih bisa"
    return "LATE", "sudah tinggi di range"

def validation_status(close, bid_low, trigger, invalidation, fake_breakout):
    if fake_breakout:
        return "INVALID", "indikasi fake breakout"
    if close > trigger * 1.005:
        return "INVALID", "harga sudah terlalu tinggi"
    if close <= invalidation * 1.002:
        return "INVALID", "dekat invalidation"
    if bid_low <= close <= trigger:
        return "VALID ENTRY", "dekat area eksekusi"
    return "WAIT", "tunggu area ideal"

def get_market_snapshot(symbol):
    try:
        ticker = yf.Ticker(yahoo_symbol(symbol))
        hist = ticker.history(period="1y", interval="1d")
        if hist is None or hist.empty or len(hist) < 210:
            return None
        hist = calc_indicators(hist)
        last = hist.iloc[-1]
        prev = hist.iloc[-2]
        prev2 = hist.iloc[-3]

        close = float(last["Close"])
        prev_close = float(prev["Close"])
        prev2_close = float(prev2["Close"])
        high = float(last["High"])
        low = float(last["Low"])
        open_price = float(last["Open"])

        ma20 = float(last["MA20"]) if pd.notna(last["MA20"]) else close
        ma50 = float(last["MA50"]) if pd.notna(last["MA50"]) else close
        ma100 = float(last["MA100"]) if pd.notna(last["MA100"]) else close
        ma200 = float(last["MA200"]) if pd.notna(last["MA200"]) else close
        rsi = float(last["RSI"]) if pd.notna(last["RSI"]) else 50.0
        macd = float(last["MACD"]) if pd.notna(last["MACD"]) else 0.0
        signal = float(last["SIGNAL"]) if pd.notna(last["SIGNAL"]) else 0.0

        volume_today = float(last["Volume"])
        volume_avg = float(last["VOLAVG5"]) if pd.notna(last["VOLAVG5"]) else volume_today

        change_pct = ((close - prev_close) / prev_close) * 100 if prev_close else 0
        prev_change_pct = ((prev_close - prev2_close) / prev2_close) * 100 if prev2_close else 0

        day_range = high - low if high > low else 0.01
        close_range_pct = (close - low) / day_range
        upper_wick_pct = (high - max(open_price, close)) / day_range

        recent_high = float(hist["High"].iloc[-6:-1].max())
        breakout_attempt = high >= recent_high * 0.995

        if volume_today > volume_avg * 1.2:
            volume_label = "Kuat"
            volume_score = 10
        elif volume_today < volume_avg * 0.8:
            volume_label = "Lemah"
            volume_score = -10
        else:
            volume_label = "Normal"
            volume_score = 0

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

        score = 50 + volume_score
        reasons = []
        tech_notes = []

        if close > prev_close:
            score += 12
            reasons.append("close di atas hari sebelumnya")
        if change_pct > 1:
            score += 10
            reasons.append("momentum > 1%")
        if change_pct > 2:
            score += 8
            reasons.append("momentum > 2%")
        if close_range_pct >= 0.7:
            score += 8
            reasons.append("close dekat high")
        if prev_change_pct > 0 and change_pct > 0:
            score += 7
            reasons.append("2 hari bullish")
        if close >= high * 0.98:
            score += 5
            reasons.append("tekan area high")
        if change_pct < 0:
            score -= 10
        if close_range_pct < 0.4:
            score -= 5
        if fake_breakout:
            score -= 18

        if close > ma20:
            score += 8
            tech_notes.append("MA20 ok")
        else:
            score -= 8
            tech_notes.append("MA20 lemah")
        if close > ma50:
            score += 8
            tech_notes.append("MA50 ok")
        else:
            tech_notes.append("MA50 lemah")
        if close > ma100:
            score += 5
            tech_notes.append("MA100 ok")
        if close > ma200:
            score += 5
            tech_notes.append("MA200 ok")
        if rsi > 55:
            score += 6
            tech_notes.append(f"RSI {rsi:.1f} kuat")
        elif rsi < 45:
            score -= 6
            tech_notes.append(f"RSI {rsi:.1f} lemah")
        else:
            tech_notes.append(f"RSI {rsi:.1f} netral")
        if macd > signal:
            score += 6
            tech_notes.append("MACD bullish")
        else:
            tech_notes.append("MACD bearish")

        if score >= 85:
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

        v_status, v_reason = validation_status(close, bid_low, trigger, invalidation, fake_breakout)
        t_label, t_reason = timing_label(close, low, high)

        return {
            "symbol": symbol.upper(),
            "score": int(score),
            "setup": setup,
            "close": round(close, 2),
            "change_pct": round(change_pct, 2),
            "volume": volume_label,
            "status": v_status,
            "validation": v_reason,
            "timing": t_label,
            "timing_reason": t_reason,
            "bid_low": bid_low,
            "bid_high": bid_high,
            "trigger": trigger,
            "invalidation": invalidation,
            "tp1": tp1,
            "tp2": tp2,
            "fake_breakout": "Ya" if fake_breakout else "Tidak",
            "fake_reason": fake_reason,
            "reason": ", ".join(reasons[:2]) if reasons else "belum ada alasan kuat",
            "tech_summary": ", ".join(tech_notes[:4]),
            "ma20": round(ma20, 2),
            "ma50": round(ma50, 2),
            "ma100": round(ma100, 2),
            "ma200": round(ma200, 2),
            "rsi": round(rsi, 1),
            "macd_state": "bullish" if macd > signal else "bearish"
        }
    except Exception as e:
        print("Yahoo error:", symbol, e)
        return None

def build_watchlist_text():
    text = f"Watchlist syariah aktif: {len(WATCHLIST)} saham\n\n"
    preview = WATCHLIST[:100]
    text += "\n".join(f"- {s}" for s in preview)
    if len(WATCHLIST) > 100:
        text += f"\n\n... dan {len(WATCHLIST)-100} saham lain"
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

    lines = ["AUTOSCAN FINAL", "", "TOP 5 FINAL SETUP HARI INI", ""]
    for i, item in enumerate(top, start=1):
        lines.append(f"{i}. {item['symbol']}")
        lines.append(f"Score: {item['score']}")
        lines.append(f"Setup: {item['setup']}")
        lines.append(f"Harga: {item['close']:.2f} ({item['change_pct']:+.2f}%)")
        lines.append(f"Volume: {item['volume']}")
        lines.append(f"Status: {item['status']}")
        lines.append(f"Validasi: {item['validation']}")
        lines.append(f"Timing: {item['timing']}")
        lines.append(f"Catatan Timing: {item['timing_reason']}")
        lines.append(f"Fake Breakout: {item['fake_breakout']}")
        lines.append(f"Catatan Fake: {item['fake_reason']}")
        lines.append(f"Teknikal: {item['tech_summary']}")
        lines.append(f"MA20/50/100/200: {item['ma20']:.2f} / {item['ma50']:.2f} / {item['ma100']:.2f} / {item['ma200']:.2f}")
        lines.append(f"RSI: {item['rsi']:.1f}")
        lines.append(f"MACD: {item['macd_state']}")
        lines.append(f"Bid Zone: {item['bid_low']:.2f} - {item['bid_high']:.2f}")
        lines.append(f"Trigger: {item['trigger']:.2f}")
        lines.append(f"Invalidation: {item['invalidation']:.2f}")
        lines.append(f"Alasan: {item['reason']}")
        lines.append("")
        lines.append("Handoff ke Exit Bot:")
        lines.append(f"/startpos {item['symbol']} {item['close']:.2f}")
        lines.append(f"/setsl {item['symbol']} {item['invalidation']:.2f}")
        lines.append(f"/settp {item['symbol']} {item['tp1']:.2f} {item['tp2']:.2f}")
        lines.append("")
    lines.append("Fokus utama: VALID ENTRY + EARLY/MID + volume kuat + bukan fake breakout + teknikal mendukung.")
    return "\n".join(lines)

def try_autoscan():
    global state, chat_id_global
    if not chat_id_global or not state.get("autoscan"):
        return
    now = datetime.now()
    if now.minute not in [0, 15, 30, 45]:
        return
    minute_key = now.strftime("%Y-%m-%d %H:%M")
    if state.get("last_scan_minute_key") == minute_key:
        return
    scan_text = build_scan_text()
    if scan_text != state.get("last_sent_text", ""):
        send_message(chat_id_global, scan_text)
        state["last_sent_text"] = scan_text
    state["last_scan_minute_key"] = minute_key
    save_state()

def handle_command(chat_id, text):
    global chat_id_global, state, WATCHLIST
    cmd = text.strip().lower()
    if cmd == "/start":
        send_message(chat_id, "Entry Bot FINAL GABUNGAN aktif.\n\nCommand:\n/watchlist\n/scan\n/autoscanon\n/autoscanoff\n/statusauto\n/reloadwatchlist")
        return
    if cmd == "/watchlist":
        send_message(chat_id, build_watchlist_text())
        return
    if cmd == "/scan":
        send_message(chat_id, "Sedang scan FINAL Top 5 gabungan...")
        send_message(chat_id, build_scan_text())
        return
    if cmd == "/autoscanon":
        state["autoscan"] = True
        save_state()
        send_message(chat_id, "Autoscan FINAL diaktifkan.")
        return
    if cmd == "/autoscanoff":
        state["autoscan"] = False
        save_state()
        send_message(chat_id, "Autoscan FINAL dimatikan.")
        return
    if cmd == "/statusauto":
        send_message(chat_id, f"Status autoscan: {'ON' if state.get('autoscan') else 'OFF'}")
        return
    if cmd == "/reloadwatchlist":
        WATCHLIST = load_watchlist()
        send_message(chat_id, f"Watchlist dimuat ulang. Total: {len(WATCHLIST)} saham.")
        return
    send_message(chat_id, "Perintah tidak dikenal. Gunakan /start")

load_chat()
load_state()

while True:
    try:
        res = requests.get(f"{URL}/getUpdates", params={"offset": last_update_id + 1}, timeout=30).json()
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
