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

MIN_VALUE_TRADED = 5000000000
MIN_DAILY_RANGE_PCT = 2.0

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
    result = []
    for x in items:
        if x not in seen:
            seen.add(x)
            result.append(x)
    return result

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
    out["RSI"] = (100 - (100 / (1 + rs))).fillna(50)

    ema12 = out["Close"].ewm(span=12, adjust=False).mean()
    ema26 = out["Close"].ewm(span=26, adjust=False).mean()
    out["MACD"] = ema12 - ema26
    out["SIGNAL"] = out["MACD"].ewm(span=9, adjust=False).mean()
    out["VOLAVG5"] = out["Volume"].rolling(5).mean()
    out["VALUE_TRADED"] = out["Close"] * out["Volume"]
    out["VALAVG5"] = out["VALUE_TRADED"].rolling(5).mean()
    return out

def timing_label(close, low, high):
    day_range = high - low if high > low else 0.01
    close_range_pct = (close - low) / day_range
    if close_range_pct < 0.45:
        return "EARLY", "masih awal gerakan", close_range_pct
    elif close_range_pct < 0.72:
        return "MID", "sudah bergerak, masih bisa", close_range_pct
    return "LATE", "sudah tinggi di range", close_range_pct

def classify_volume(value_traded, valavg5):
    if pd.isna(valavg5) or valavg5 <= 0:
        return "Normal", 0
    if value_traded > valavg5 * 1.25:
        return "Kuat", 12
    if value_traded < valavg5 * 0.8:
        return "Lemah", -12
    return "Normal", 0

def detect_fake_breakout(close, high, low, open_price, recent_high, change_pct):
    day_range = high - low if high > low else 0.01
    close_range_pct = (close - low) / day_range
    upper_wick_pct = (high - max(open_price, close)) / day_range
    breakout_attempt = high >= recent_high * 0.995

    fake = False
    reason = "-"
    if breakout_attempt and close < recent_high and upper_wick_pct > 0.35:
        fake = True
        reason = "sempat tekan high tapi close lemah"
    elif breakout_attempt and close_range_pct < 0.45:
        fake = True
        reason = "close di bawah pertengahan range"
    elif breakout_attempt and change_pct < 0.3:
        fake = True
        reason = "breakout tidak punya follow-through"
    return fake, reason, breakout_attempt, close_range_pct, upper_wick_pct

def get_base_zone(df):
    recent = df.iloc[-9:-1].copy()
    if recent.empty:
        return None, None, None
    base_low = float(recent["Low"].min())
    base_high = float(recent["High"].max())
    base_width_pct = ((base_high - base_low) / base_high) * 100 if base_high else 0
    is_sideway = base_width_pct <= 6.0
    return round(base_low, 2), round(base_high, 2), is_sideway

def detect_setup(close, ma20, ma50, base_low, base_high, value_traded, valavg5, breakout_attempt, fake_breakout):
    if base_low is None or base_high is None:
        return "WATCH ONLY"
    near_base_low = close <= base_low * 1.03
    in_base = base_low <= close <= base_high * 1.01
    near_ma_support = abs(close - ma20) / close < 0.02 or abs(close - ma50) / close < 0.03
    strong_value = (not pd.isna(valavg5)) and valavg5 > 0 and value_traded > valavg5 * 1.2

    if in_base and near_ma_support:
        return "SIDEWAY ACCUMULATION PREPARE"
    if near_base_low and near_ma_support:
        return "SUPPORT BOUNCE PREPARE"
    if breakout_attempt and not fake_breakout and strong_value:
        return "VALID BREAKOUT EXECUTE"
    if close > base_high and not strong_value:
        return "LATE BREAKOUT"
    return "WATCH ONLY"

def build_entry_zone(setup, close, ma20, ma50, base_low, base_high):
    if base_low is None or base_high is None:
        return round(close * 0.994, 2), round(close * 0.998, 2), "fallback"
    if setup == "SIDEWAY ACCUMULATION PREPARE":
        width = base_high - base_low
        return round(base_low, 2), round(base_low + (width * 0.35), 2), "base accumulation"
    if setup == "SUPPORT BOUNCE PREPARE":
        support = min(base_low, ma20, ma50)
        return round(support * 0.998, 2), round(support * 1.006, 2), "support bounce"
    if setup == "VALID BREAKOUT EXECUTE":
        return round(base_high * 0.998, 2), round(base_high * 1.004, 2), "breakout retest"
    return round(base_low, 2), round(base_low + ((base_high - base_low) * 0.25), 2), "watch only"

def validation_status(close, bid_low, bid_high, trigger, invalidation, fake_breakout, setup, volume_score):
    if fake_breakout:
        return "INVALID", "indikasi fake breakout"
    if setup == "LATE BREAKOUT":
        return "INVALID", "breakout sudah telat"
    if close <= invalidation * 1.002:
        return "INVALID", "harga terlalu dekat invalidation"
    if close > trigger * 1.01 and volume_score <= 0:
        return "INVALID", "harga lari tanpa volume"
    if bid_low <= close <= bid_high:
        return "VALID ENTRY", "harga di area akumulasi / eksekusi"
    if bid_high < close <= trigger and volume_score > 0 and setup == "VALID BREAKOUT EXECUTE":
        return "VALID ENTRY", "breakout valid dengan volume besar"
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
        value_traded = float(last["VALUE_TRADED"]) if pd.notna(last["VALUE_TRADED"]) else close * volume_today
        valavg5 = float(last["VALAVG5"]) if pd.notna(last["VALAVG5"]) else value_traded

        change_pct = ((close - prev_close) / prev_close) * 100 if prev_close else 0
        prev_change_pct = ((prev_close - prev2_close) / prev2_close) * 100 if prev2_close else 0
        daily_range_pct = ((high - low) / close) * 100 if close else 0

        recent_high = float(hist["High"].iloc[-6:-1].max())
        fake_breakout, fake_reason, breakout_attempt, close_range_pct, upper_wick_pct = detect_fake_breakout(close, high, low, open_price, recent_high, change_pct)

        timing, timing_reason, range_pct = timing_label(close, low, high)
        volume_label, volume_score = classify_volume(value_traded, valavg5)
        base_low, base_high, is_sideway = get_base_zone(hist)

        liquid_ok = value_traded >= MIN_VALUE_TRADED and daily_range_pct >= MIN_DAILY_RANGE_PCT
        liquidity_penalty = 0 if liquid_ok else 35
        liquidity_note = "likuid" if liquid_ok else "kurang likuid"

        setup = detect_setup(close, ma20, ma50, base_low, base_high, value_traded, valavg5, breakout_attempt, fake_breakout)
        bid_low, bid_high, zone_type = build_entry_zone(setup, close, ma20, ma50, base_low, base_high)

        trigger = round((base_high if base_high else close) * 1.003, 2)
        invalidation = round((bid_low if bid_low else close) * 0.992, 2)

        trend = 0
        structure = 0
        execution = 0
        confirmation = 0
        penalty = 0

        reasons = []
        tech_notes = []

        if close > ma20:
            trend += 4
            tech_notes.append("di atas MA20")
        else:
            trend -= 6
            tech_notes.append("di bawah MA20")
        if ma20 > ma50:
            trend += 5
            tech_notes.append("MA20 > MA50")
        else:
            trend -= 4
            tech_notes.append("MA20 < MA50")
        if close > ma100:
            trend += 4
            tech_notes.append("di atas MA100")
        if close > ma200:
            trend += 4
            tech_notes.append("di atas MA200")
        if rsi > 55:
            trend += 4
            tech_notes.append(f"RSI {rsi:.1f} kuat")
        elif rsi < 45:
            trend -= 6
            tech_notes.append(f"RSI {rsi:.1f} lemah")
        else:
            tech_notes.append(f"RSI {rsi:.1f} netral")
        if macd > signal:
            trend += 4
            tech_notes.append("MACD bullish")
        else:
            trend -= 2
            tech_notes.append("MACD bearish")

        if setup == "SIDEWAY ACCUMULATION PREPARE":
            structure += 22
            reasons.append("base sideway rapat")
        elif setup == "SUPPORT BOUNCE PREPARE":
            structure += 20
            reasons.append("pantulan support / MA")
        elif setup == "PULLBACK PREPARE":
            structure += 14
            reasons.append("pullback sehat")
        elif setup == "VALID BREAKOUT EXECUTE":
            structure += 10
            reasons.append("breakout valid")
        elif setup == "LATE BREAKOUT":
            penalty += 18

        if is_sideway:
            structure += 6
            reasons.append("konsolidasi rapi")
        if abs(close - ma20) / close < 0.02:
            structure += 6
            reasons.append("dekat MA20")
        if abs(close - ma50) / close < 0.03:
            structure += 4
            reasons.append("dekat MA50")
        if prev_change_pct > 0 and change_pct > 0:
            structure += 2

        if bid_low <= close <= bid_high:
            execution += 18
        elif close < bid_low:
            execution += 8
        elif bid_high < close <= trigger:
            execution += 4

        if timing == "EARLY":
            execution += 12
        elif timing == "MID":
            execution += 4
        else:
            penalty += 16

        rr_pct = ((trigger - invalidation) / close) * 100 if close else 0
        if rr_pct < 1.0:
            penalty += 8

        if setup == "VALID BREAKOUT EXECUTE" and volume_score > 0:
            confirmation += 16
        elif setup == "VALID BREAKOUT EXECUTE" and volume_score <= 0:
            penalty += 12

        if change_pct > 1:
            confirmation += 3
        if close_range_pct >= 0.7 and not fake_breakout:
            confirmation += 3

        if fake_breakout:
            penalty += 24
        if rsi > 70:
            penalty += 10
        if volume_score < 0:
            penalty += 12
        if liquidity_penalty:
            penalty += liquidity_penalty
        if close > trigger * 1.01 and setup != "VALID BREAKOUT_EXECUTE":
            penalty += 20
        if upper_wick_pct > 0.35:
            penalty += 8

        score = int(round(
            0.25 * (trend * 4) +
            0.25 * structure +
            0.25 * execution +
            0.15 * confirmation +
            0.10 * max(volume_score, 0) -
            0.30 * penalty + 50
        ))

        tp1 = round(close * 1.01, 2)
        tp2 = round(close * 1.02, 2)
        v_status, v_reason = validation_status(close, bid_low, bid_high, trigger, invalidation, fake_breakout, setup, volume_score)

        return {
            "symbol": symbol.upper(),
            "score": score,
            "setup": setup,
            "close": round(close, 2),
            "change_pct": round(change_pct, 2),
            "volume": volume_label,
            "status": v_status,
            "validation": v_reason,
            "timing": timing,
            "timing_reason": timing_reason,
            "bid_low": round(bid_low, 2),
            "bid_high": round(bid_high, 2),
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
            "macd_state": "bullish" if macd > signal else "bearish",
            "trend_block": trend,
            "structure_block": structure,
            "execution_block": execution,
            "confirmation_block": confirmation,
            "penalty_block": penalty,
            "value_traded": round(value_traded, 2),
            "liquidity_note": liquidity_note,
            "zone_type": zone_type
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
        if data and data["score"] > 35:
            results.append(data)
    if not results:
        return "Data scan belum tersedia atau banyak saham gugur oleh filter likuiditas."
    results.sort(key=lambda x: x["score"], reverse=True)
    top = results[:5]

    lines = ["AUTOSCAN FINAL SMART V12", "", "TOP 5 FINAL SETUP HARI INI", ""]
    for i, item in enumerate(top, start=1):
        lines.append(f"{i}. {item['symbol']}")
        lines.append(f"Score: {item['score']}")
        lines.append(f"Setup: {item['setup']}")
        lines.append(f"Harga: {item['close']:.2f} ({item['change_pct']:+.2f}%)")
        lines.append(f"Likuiditas: {item['liquidity_note']}")
        lines.append(f"Volume: {item['volume']}")
        lines.append(f"Status: {item['status']}")
        lines.append(f"Validasi: {item['validation']}")
        lines.append(f"Timing: {item['timing']}")
        lines.append(f"Catatan Timing: {item['timing_reason']}")
        lines.append(f"Fake Breakout: {item['fake_breakout']}")
        lines.append(f"Catatan Fake: {item['fake_reason']}")
        lines.append(f"Zona Entry: {item['zone_type']}")
        lines.append(f"Teknikal: {item['tech_summary']}")
        lines.append(f"MA20/50/100/200: {item['ma20']:.2f} / {item['ma50']:.2f} / {item['ma100']:.2f} / {item['ma200']:.2f}")
        lines.append(f"RSI: {item['rsi']:.1f}")
        lines.append(f"MACD: {item['macd_state']}")
        lines.append(f"Bobot T/S/E/C/P: {item['trend_block']} / {item['structure_block']} / {item['execution_block']} / {item['confirmation_block']} / {item['penalty_block']}")
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
    lines.append("Fokus utama: hanya saham likuid, trend besar sehat, zona akumulasi jelas, jangan kejar harga tinggi kecuali breakout valid + volume besar.")
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
        send_message(chat_id, "Entry Bot FINAL SMART V12 aktif.\n\nCommand:\n/watchlist\n/scan\n/autoscanon\n/autoscanoff\n/statusauto\n/reloadwatchlist")
        return
    if cmd == "/watchlist":
        send_message(chat_id, build_watchlist_text())
        return
    if cmd == "/scan":
        send_message(chat_id, "Sedang scan FINAL SMART V12...")
        send_message(chat_id, build_scan_text())
        return
    if cmd == "/autoscanon":
        state["autoscan"] = True
        save_state()
        send_message(chat_id, "Autoscan FINAL SMART V12 diaktifkan.")
        return
    if cmd == "/autoscanoff":
        state["autoscan"] = False
        save_state()
        send_message(chat_id, "Autoscan FINAL SMART V12 dimatikan.")
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
