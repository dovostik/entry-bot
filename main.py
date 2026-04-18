# FIXED VERSION V16.3b (breakout tolerance improved)

# perubahan utama:
# close <= trigger * 1.02 (lebih fleksibel dari 1.01)

# gunakan file ini untuk replace main.py sepenuhnya

import requests
import time
import os
import json
import io
from contextlib import redirect_stdout, redirect_stderr
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
UNSUPPORTED_SYMBOLS_FILE = "unsupported_symbols.json"

chat_id_global = None
state = {"autoscan": False, "last_scan_minute_key": "", "active_candidates": {}}
unsupported_symbols = set()

MIN_VALUE_TRADED = 10000000000
MIN_DAILY_RANGE_PCT = 2.0
MAX_DISTANCE_TO_BID_PCT = 4.0
SCAN_INTERVAL_MINUTES = 5
MEMORY_MISS_LIMIT = 2

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
                loaded = json.load(f)
                state.update(loaded)
        except Exception:
            pass
    if "active_candidates" not in state:
        state["active_candidates"] = {}

def save_state():
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(state, f)

def load_watchlist():
    if not os.path.exists(WATCHLIST_FILE):
        return ["BRIS", "ANTM", "PTBA", "TLKM", "INDF", "ICBP", "KLBF", "EXCL", "PGAS", "CPIN"]
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

def load_unsupported_symbols():
    global unsupported_symbols
    if os.path.exists(UNSUPPORTED_SYMBOLS_FILE):
        try:
            with open(UNSUPPORTED_SYMBOLS_FILE, "r", encoding="utf-8") as f:
                unsupported_symbols = set(json.load(f))
        except Exception:
            unsupported_symbols = set()
    else:
        unsupported_symbols = set()

def save_unsupported_symbols():
    try:
        with open(UNSUPPORTED_SYMBOLS_FILE, "w", encoding="utf-8") as f:
            json.dump(sorted(list(unsupported_symbols)), f)
    except Exception:
        pass

def mark_symbol_unsupported(symbol):
    global unsupported_symbols
    symbol = symbol.upper().strip()
    if symbol not in unsupported_symbols:
        unsupported_symbols.add(symbol)
        save_unsupported_symbols()

WATCHLIST = load_watchlist()

def send_message(chat_id, text):
    requests.post(f"{URL}/sendMessage", json={"chat_id": chat_id, "text": text}, timeout=30)

def yahoo_symbol(symbol):
    symbol = symbol.upper().strip()
    return symbol if symbol.endswith(".JK") else f"{symbol}.JK"

def safe_yahoo_history(symbol):
    try:
        fake_out = io.StringIO()
        fake_err = io.StringIO()
        with redirect_stdout(fake_out), redirect_stderr(fake_err):
            ticker = yf.Ticker(yahoo_symbol(symbol))
            hist = ticker.history(period="1y", interval="1d")
        return hist
    except Exception:
        return None

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
    out["MACD_HIST"] = out["MACD"] - out["SIGNAL"]

    out["VOLAVG5"] = out["Volume"].rolling(5).mean()
    out["VALUE_TRADED"] = out["Close"] * out["Volume"]
    out["VALAVG5"] = out["VALUE_TRADED"].rolling(5).mean()
    return out

def timing_label(close, low, high):
    day_range = high - low if high > low else 0.01
    close_range_pct = (close - low) / day_range
    if close_range_pct < 0.45:
        return "EARLY", "masih awal gerakan"
    elif close_range_pct < 0.72:
        return "MID", "sudah bergerak, masih bisa"
    return "LATE", "sudah tinggi di range"

def classify_volume(value_traded, valavg5):
    if pd.isna(valavg5) or valavg5 <= 0:
        return "Normal", 0
    if value_traded > valavg5 * 1.25:
        return "Kuat", 12
    if value_traded < valavg5 * 0.9:
        return "Lemah", -15
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

def build_entry_zone(setup, close, ma20, ma50, base_low, base_high):
    if base_low is None or base_high is None:
        return round(close * 0.994, 2), round(close * 0.998, 2), "fallback"
    if setup == "SIDEWAY ACCUMULATION PREPARE":
        width = max(base_high - base_low, 0.01)
        return round(base_low, 2), round(base_low + (width * 0.25), 2), "base support"
    if setup == "SUPPORT BOUNCE PREPARE":
        support = min(base_low, ma20, ma50)
        return round(support * 0.998, 2), round(support * 1.004, 2), "support bounce"
    if setup == "VALID_BREAKOUT_EXECUTE":
        return round(base_high * 0.998, 2), round(base_high * 1.006, 2), "breakout retest"
    if setup == "BREAKOUT RETEST READY":
        return round(base_high * 0.995, 2), round(base_high * 1.002, 2), "retest breakout"
    return round(base_low, 2), round(base_low + ((base_high - base_low) * 0.20), 2), "watch only"

def validation_status(close, bid_low, bid_high, trigger, invalidation, fake_breakout, setup, volume_score, range_position, trend_bias):
    if fake_breakout:
        return "INVALID", "indikasi fake breakout"
    if setup in ["WEAK SIDEWAY", "BREAKOUT EXTENDED", "OVEREXTENDED"]:
        return "WAIT", "belum aman, tunggu pullback / abaikan"
    if close <= invalidation * 1.002:
        return "INVALID", "harga terlalu dekat invalidation"
    if close > trigger * 1.03 and volume_score <= 0:
        return "INVALID", "harga lari tanpa volume"
    if trend_bias == "bearish" and setup in ["SIDEWAY ACCUMULATION PREPARE", "SUPPORT BOUNCE PREPARE"]:
        return "WAIT", "counter trend / downtrend, jangan agresif"
    if range_position > 0.65 and setup in ["SIDEWAY ACCUMULATION PREPARE", "SUPPORT BOUNCE PREPARE"]:
        return "WAIT", "harga masih terlalu tinggi di range"
    if bid_low <= close <= bid_high and range_position <= 0.65:
        return "VALID ENTRY", "harga di area eksekusi"
    if close <= bid_high * 1.01 and range_position <= 0.65 and setup in ["SIDEWAY ACCUMULATION PREPARE", "SUPPORT BOUNCE PREPARE"]:
        return "VALID ENTRY", "masih dekat area, boleh cicil kecil"

    # BREAKOUT AWAL: lebih fleksibel sedikit dari V16.2
    if setup == "VALID_BREAKOUT_EXECUTE" and volume_score > 0 and close <= trigger * 1.02:
        return "VALID ENTRY", "breakout valid, masih dekat trigger"

    if setup == "BREAKOUT RETEST READY" and volume_score > 0 and bid_low <= close <= trigger:
        return "VALID ENTRY", "retest sehat"

    return "WAIT", "tunggu area ideal"

def get_market_snapshot(symbol):
    symbol = symbol.upper().strip()
    if symbol in unsupported_symbols:
        return None

    hist = safe_yahoo_history(symbol)
    if hist is None or hist.empty or len(hist) < 210:
        mark_symbol_unsupported(symbol)
        return None

    try:
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
        macd_hist = float(last["MACD_HIST"]) if pd.notna(last["MACD_HIST"]) else 0.0

        volume_today = float(last["Volume"])
        value_traded = float(last["VALUE_TRADED"]) if pd.notna(last["VALUE_TRADED"]) else close * volume_today
        valavg5 = float(last["VALAVG5"]) if pd.notna(last["VALAVG5"]) else value_traded

        change_pct = ((close - prev_close) / prev_close) * 100 if prev_close else 0
        prev_change_pct = ((prev_close - prev2_close) / prev2_close) * 100 if prev2_close else 0
        daily_range_pct = ((high - low) / close) * 100 if close else 0

        recent_high = float(hist["High"].iloc[-6:-1].max())
        recent_low = float(hist["Low"].iloc[-6:-1].min())

        fake_breakout, fake_reason, breakout_attempt, close_range_pct, upper_wick_pct = detect_fake_breakout(
            close, high, low, open_price, recent_high, change_pct
        )

        timing, timing_reason = timing_label(close, low, high)
        volume_label, volume_score = classify_volume(value_traded, valavg5)
        base_low, base_high, is_sideway = get_base_zone(hist)

        if value_traded < MIN_VALUE_TRADED or daily_range_pct < MIN_DAILY_RANGE_PCT:
            return None

        trend_bias = "bullish"
        if ma20 < ma50 and close < ma50:
            trend_bias = "bearish"
        elif close < ma100 and ma50 < ma100:
            trend_bias = "bearish"
        elif close < ma20 or rsi < 45 or macd_hist < 0:
            trend_bias = "neutral"

        move_from_base_pct = ((close - base_high) / base_high) * 100 if base_high else 0
        recent_drop_pct = ((recent_high - close) / recent_high) * 100 if recent_high else 0

        support = base_low if base_low is not None else recent_low
        resistance = base_high if base_high is not None else recent_high
        range_width = max(resistance - support, 0.01)
        range_position = (close - support) / range_width
        near_lower_range = range_position <= 0.40

        dead_market = abs(change_pct) < 1 and value_traded < max(valavg5, 1) * 1.05 and volume_score <= 0
        post_drop_sideway = recent_drop_pct > 8 and is_sideway
        bad_sideway = is_sideway and ((close < ma20 and ma20 < ma50) or (close < ma50 and ma50 < ma100))

        strong_accumulation = (
            is_sideway and near_lower_range and close >= ma20 * 0.995 and rsi >= 45
            and macd_hist >= -1.0 and not dead_market and not post_drop_sideway
            and trend_bias != "bearish"
        )
        support_bounce = (
            near_lower_range and close >= support and close >= ma20 * 0.99 and rsi >= 45
            and macd >= signal * 0.9 and trend_bias != "bearish"
        )

        setup = None
        if breakout_attempt and not fake_breakout and volume_score > 0:
            if move_from_base_pct <= 2.5:
                setup = "VALID_BREAKOUT_EXECUTE"
            elif move_from_base_pct <= 5.0:
                setup = "BREAKOUT RETEST_READY"
            else:
                setup = "OVEREXTENDED"
        elif strong_accumulation:
            setup = "SIDEWAY ACCUMULATION PREPARE"
        elif support_bounce:
            setup = "SUPPORT BOUNCE PREPARE"
        elif is_sideway:
            setup = "WEAK SIDEWAY"
        else:
            return None

        # normalisasi nama setup agar konsisten
        if setup == "BREAKOUT RETEST_READY":
            setup = "BREAKOUT RETEST READY"

        if setup in ["WEAK SIDEWAY", "OVEREXTENDED"]:
            return None
        if trend_bias == "bearish" and setup in ["SIDEWAY ACCUMULATION PREPARE", "SUPPORT BOUNCE PREPARE"]:
            return None
        if dead_market and setup in ["SIDEWAY ACCUMULATION PREPARE", "SUPPORT BOUNCE PREPARE"]:
            return None
        if bad_sideway and setup in ["SIDEWAY ACCUMULATION PREPARE", "SUPPORT BOUNCE PREPARE"]:
            return None
        if recent_drop_pct > 8 and is_sideway and setup in ["SIDEWAY ACCUMULATION PREPARE", "SUPPORT BOUNCE PREPARE"]:
            return None
        if volume_score < -10 and setup != "SUPPORT BOUNCE PREPARE":
            return None
        if rsi > 78 and setup not in ["VALID_BREAKOUT_EXECUTE", "BREAKOUT RETEST READY"]:
            return None
        if timing == "LATE" and setup in ["SIDEWAY ACCUMULATION PREPARE", "SUPPORT BOUNCE PREPARE"]:
            return None

        bid_low, bid_high, _zone_type = build_entry_zone(setup, close, ma20, ma50, base_low, base_high)
        trigger = round((base_high if base_high else close) * 1.003, 2)
        invalidation = round((bid_low if bid_low else close) * 0.992, 2)

        distance_to_bid_pct = ((close - bid_high) / close) * 100 if close > bid_high else 0
        if distance_to_bid_pct > MAX_DISTANCE_TO_BID_PCT and setup in ["SIDEWAY ACCUMULATION PREPARE", "SUPPORT BOUNCE PREPARE"]:
            return None

        trend = 0
        structure = 0
        execution = 0
        confirmation = 0
        penalty = 0
        reasons = []
        tech_notes = []

        if close > ma20:
            trend += 3
            tech_notes.append("di atas MA20")
        else:
            trend -= 4
            tech_notes.append("di bawah MA20")

        if ma20 > ma50:
            trend += 4
            tech_notes.append("MA20 > MA50")
        else:
            trend -= 3
            tech_notes.append("MA20 < MA50")

        if close > ma100:
            trend += 2
            tech_notes.append("di atas MA100")
        else:
            tech_notes.append("di bawah MA100")

        if rsi >= 55:
            trend += 3
            tech_notes.append(f"RSI {rsi:.1f} kuat")
        elif rsi < 45:
            trend -= 4
            tech_notes.append(f"RSI {rsi:.1f} lemah")
        else:
            tech_notes.append(f"RSI {rsi:.1f} netral")

        if macd_hist > 0:
            trend += 3
            tech_notes.append("MACD menguat")
        else:
            trend -= 2
            tech_notes.append("MACD lemah")

        if setup == "SIDEWAY ACCUMULATION PREPARE":
            structure += 18
            reasons.append("base sehat dekat bawah range")
        elif setup == "SUPPORT BOUNCE PREPARE":
            structure += 16
            reasons.append("pantulan support dekat area bawah")
        elif setup == "VALID_BREAKOUT_EXECUTE":
            structure += 16
            reasons.append("breakout valid belum terlalu jauh")
        elif setup == "BREAKOUT RETEST READY":
            structure += 15
            reasons.append("breakout sudah jalan, tunggu retest")

        if is_sideway:
            structure += 4
            reasons.append("range rapi")

        if near_lower_range:
            structure += 6
            reasons.append("posisi dekat support")

        if volume_score > 0:
            confirmation += 8
            reasons.append("volume mendukung")

        if setup == "BREAKOUT RETEST READY" and trend_bias != "bearish":
            confirmation += 3

        if prev_change_pct > 0 and change_pct > 0:
            confirmation += 2

        if fake_breakout:
            penalty += 20
        if upper_wick_pct > 0.35:
            penalty += 6
        if trend_bias == "neutral":
            penalty += 4
        if setup == "BREAKOUT RETEST READY":
            penalty += 4
        if move_from_base_pct > 3:
            penalty += 8

        # ranking tweak: breakout awal yang masih di bawah MA50 / MA100 jangan terlalu mudah unggul
        if setup == "VALID_BREAKOUT_EXECUTE" and close < ma50:
            penalty += 4
        if setup == "VALID_BREAKOUT_EXECUTE" and close < ma100:
            penalty += 3

        # execution lebih ramah ke breakout awal
        if bid_low <= close <= bid_high and near_lower_range:
            execution += 16
        elif close < bid_low:
            execution += 8
        elif setup == "VALID_BREAKOUT_EXECUTE" and close <= trigger * 1.02:
            execution += 12
        elif setup == "BREAKOUT RETEST READY" and close <= trigger:
            execution += 10
        else:
            penalty += 6

        if timing == "EARLY":
            execution += 10
        elif timing == "MID":
            execution += 4

        risk_pct = ((close - invalidation) / close) * 100 if close else 0
        reward_pct = ((trigger - close) / close) * 100 if close else 0
        if reward_pct <= 0 or reward_pct < risk_pct:
            penalty += 5

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

        v_status, v_reason = validation_status(
            close, bid_low, bid_high, trigger, invalidation, fake_breakout,
            setup, volume_score, range_position, trend_bias
        )
        if v_status == "INVALID":
            return None

        confidence = "HIGH" if score >= 85 else "MEDIUM" if score >= 70 else "LOW"
        if setup == "BREAKOUT RETEST READY" and confidence == "LOW":
            confidence = "MEDIUM"

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
            "reason": ", ".join(reasons[:3]) if reasons else "belum ada alasan kuat",
            "tech_summary": ", ".join(tech_notes[:4]),
            "confidence": confidence
        }
    except Exception:
        mark_symbol_unsupported(symbol)
        return None

def candidate_key(data):
    return data["symbol"]

def decision_status(data):
    if data["status"] == "VALID ENTRY":
        if data["setup"] == "VALID_BREAKOUT_EXECUTE" and data["close"] > data["bid_high"]:
            return "ACTIVE BID EARLY"
        if data["close"] <= data["bid_high"]:
            return "ACTIVE BID"
        return "ACTIVE BID EARLY"
    if data["setup"] == "BREAKOUT RETEST READY":
        return "WAIT RETEST"
    return "WATCH WAIT"

def format_new_candidate(data):
    lines = [
        "PRIORITAS BARU",
        "",
        data["symbol"],
        f"Status: {decision_status(data)}",
        f"Setup: {data['setup']}",
        f"Confidence: {data['confidence']}",
        f"Harga: {data['close']:.2f} ({data['change_pct']:+.2f}%)",
        f"Bid Zone: {data['bid_low']:.2f} - {data['bid_high']:.2f}",
        f"Trigger: {data['trigger']:.2f}",
        f"Invalidation: {data['invalidation']:.2f}",
        f"Alasan: {data['reason']}"
    ]
    return "\n".join(lines)

def format_cancel_candidate(prev, reason):
    lines = [
        "CANCEL BID",
        "",
        prev["symbol"],
        f"Status sebelumnya: {prev.get('decision_status', '-')}",
        f"Alasan: {reason}"
    ]
    return "\n".join(lines)

def format_weakening_candidate(prev, new):
    lines = [
        "WARNING",
        "",
        new["symbol"],
        f"Status berubah: {prev.get('decision_status', '-')} -> {decision_status(new)}",
        f"Harga sekarang: {new['close']:.2f}",
        f"Alasan: {new.get('validation', 'kualitas menurun')}"
    ]
    return "\n".join(lines)

def build_unsupported_text():
    if not unsupported_symbols:
        return "Belum ada symbol unsupported."
    items = sorted(list(unsupported_symbols))
    lines = ["SYMBOL UNSUPPORTED / DELISTING", ""]
    for s in items[:100]:
        lines.append(f"- {s}")
    if len(items) > 100:
        lines.append("")
        lines.append(f"... dan {len(items)-100} symbol lain")
    return "\n".join(lines)

def scan_candidates():
    valid_pullback = []
    valid_breakout = []
    wait_pullback = []
    wait_breakout = []

    for symbol in WATCHLIST:
        data = get_market_snapshot(symbol)
        if not data:
            continue
        if data["setup"] in ["SIDEWAY ACCUMULATION PREPARE", "SUPPORT BOUNCE PREPARE"]:
            if data["status"] == "VALID ENTRY":
                valid_pullback.append(data)
            else:
                wait_pullback.append(data)
        elif data["setup"] in ["VALID_BREAKOUT_EXECUTE", "BREAKOUT RETEST READY"]:
            if data["status"] == "VALID ENTRY":
                valid_breakout.append(data)
            else:
                wait_breakout.append(data)

    valid_pullback.sort(key=lambda x: x["score"], reverse=True)
    valid_breakout.sort(key=lambda x: x["score"], reverse=True)
    wait_pullback.sort(key=lambda x: x["score"], reverse=True)
    wait_breakout.sort(key=lambda x: x["score"], reverse=True)

    selected = valid_pullback[:3] + valid_breakout[:3] + wait_pullback[:3] + wait_breakout[:3]
    selected.sort(key=lambda x: x["score"], reverse=True)
    return selected[:10]

def process_event_driven_scan():
    global state, chat_id_global
    current = scan_candidates()
    prev_map = state.get("active_candidates", {})
    new_map = {}

    for rank, data in enumerate(current, start=1):
        key = candidate_key(data)
        prev = prev_map.get(key)
        data["rank"] = rank
        data["decision_status"] = decision_status(data)
        data["miss_count"] = 0
        new_map[key] = data

        if prev is None:
            send_message(chat_id_global, format_new_candidate(data))
        else:
            prev_status = prev.get("decision_status")
            new_status = data["decision_status"]
            prev_score = prev.get("score", 0)
            new_score = data["score"]

            if prev_status in ["ACTIVE BID", "ACTIVE BID EARLY"] and new_status not in ["ACTIVE BID", "ACTIVE BID EARLY"]:
                send_message(chat_id_global, format_weakening_candidate(prev, data))
            elif prev_status not in ["ACTIVE BID", "ACTIVE BID EARLY"] and new_status in ["ACTIVE BID", "ACTIVE BID EARLY"]:
                send_message(chat_id_global, format_new_candidate(data))
            elif new_score < prev_score - 12:
                send_message(chat_id_global, format_weakening_candidate(prev, data))

    for key, prev in prev_map.items():
        if key not in new_map:
            miss = prev.get("miss_count", 0) + 1
            prev["miss_count"] = miss
            if miss >= MEMORY_MISS_LIMIT:
                send_message(chat_id_global, format_cancel_candidate(prev, "kandidat hilang dari prioritas beberapa scan berturut"))
            else:
                new_map[key] = prev

    state["active_candidates"] = new_map
    save_state()

def build_watchlist_text():
    text = f"Watchlist syariah aktif: {len(WATCHLIST)} saham\n\n"
    preview = WATCHLIST[:100]
    text += "\n".join(f"- {s}" for s in preview)
    if len(WATCHLIST) > 100:
        text += f"\n\n... dan {len(WATCHLIST)-100} saham lain"
    return text

def build_status_text():
    active = state.get("active_candidates", {})
    if not active:
        return "Belum ada kandidat aktif."

    items = sorted(active.values(), key=lambda x: x.get("rank", 999))
    lines = ["STATUS KANDIDAT AKTIF", ""]
    for item in items[:12]:
        lines.append(
            f"{item['symbol']} | {item.get('decision_status','-')} | rank {item.get('rank','-')} | score {item.get('score','-')}"
        )
    return "\n".join(lines)

def is_market_open():
    now = datetime.now()
    if now.weekday() >= 5:
        return False
    current = now.strftime("%H:%M")
    if "09:00" <= current <= "12:00":
        return True
    if "13:30" <= current <= "15:15":
        return True
    return False

def should_run_scan():
    now = datetime.now()
    if now.minute % SCAN_INTERVAL_MINUTES != 0:
        return None
    return now.strftime("%Y-%m-%d %H:%M")

def try_autoscan():
    global state, chat_id_global
    if not is_market_open():
        return
    if not chat_id_global or not state.get("autoscan"):
        return
    minute_key = should_run_scan()
    if minute_key is None:
        return
    if state.get("last_scan_minute_key") == minute_key:
        return
    process_event_driven_scan()
    state["last_scan_minute_key"] = minute_key
    save_state()

def handle_command(chat_id, text):
    global chat_id_global, state, WATCHLIST
    cmd = text.strip().lower()

    if cmd == "/start":
        send_message(
            chat_id,
            "Entry Bot V16.3c ranking tuned aktif.\n\n"
            "Command:\n"
            "/watchlist\n"
            "/scan\n"
            "/autoscanon\n"
            "/autoscanoff\n"
            "/statusauto\n"
            "/statuskandidat\n"
            "/listskips\n"
            "/reloadwatchlist"
        )
        return

    if cmd == "/watchlist":
        send_message(chat_id, build_watchlist_text())
        return
    if cmd == "/scan":
        process_event_driven_scan()
        send_message(chat_id, build_status_text())
        return
    if cmd == "/autoscanon":
        state["autoscan"] = True
        save_state()
        send_message(chat_id, "Autoscan V16.3 diaktifkan. Scan setiap 5 menit saat market buka.")
        return
    if cmd == "/autoscanoff":
        state["autoscan"] = False
        save_state()
        send_message(chat_id, "Autoscan V16.3 dimatikan.")
        return
    if cmd == "/statusauto":
        send_message(chat_id, f"Status autoscan: {'ON' if state.get('autoscan') else 'OFF'}")
        return
    if cmd == "/statuskandidat":
        send_message(chat_id, build_status_text())
        return
    if cmd == "/listskips":
        send_message(chat_id, build_unsupported_text())
        return
    if cmd == "/reloadwatchlist":
        WATCHLIST = load_watchlist()
        send_message(chat_id, f"Watchlist dimuat ulang. Total: {len(WATCHLIST)} saham.")
        return

    send_message(chat_id, "Perintah tidak dikenal. Gunakan /start")

load_chat()
load_state()
load_unsupported_symbols()

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