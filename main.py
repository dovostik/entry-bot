import requests
import time
import os
import json
import io
from contextlib import redirect_stdout, redirect_stderr
from datetime import datetime, timedelta
import pandas as pd
import yfinance as yf

TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
if not TOKEN:
    print("TOKEN tidak ditemukan!")
    raise SystemExit(1)

URL = f"https://api.telegram.org/bot{TOKEN}"
last_update_id = 0

CHAT_FILE = "entry_chat.json"
STATE_FILE = "entry_state.json"
WATCHLIST_FILE = "watchlist_syariah.txt"
UNSUPPORTED_SYMBOLS_FILE = "unsupported_symbols.json"
SIGNAL_JOURNAL_FILE = "signal_journal.json"
SIGNAL_EVAL_FILE = "signal_evaluations.json"
QUICK_POOL_FILE = "quick_pool.json"

chat_id_global = None
state = {
    "autoscan": False,
    "last_scan_minute_key": "",
    "active_candidates": {},
    "last_dual_scan_hash": ""
}
unsupported_symbols = set()
relative_strength_map = {}

MIN_VALUE_TRADED = 10000000000
MIN_DAILY_RANGE_PCT = 2.0
MAX_DISTANCE_TO_BID_PCT = 4.0
SCAN_INTERVAL_MINUTES = 5
EVAL_INTERVALS = [15, 30, 60]
TOP_PER_PATH = 5
TOP_COMBINED = 10
QUICK_POOL_MAX = 60

def load_json_file(path, default):
    if os.path.exists(path):
        try:
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return default
    return default

def save_json_file(path, data):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False)

def load_chat():
    global chat_id_global
    chat_id_global = load_json_file(CHAT_FILE, {}).get("chat_id")

def save_chat():
    save_json_file(CHAT_FILE, {"chat_id": chat_id_global})

def load_state():
    global state
    loaded = load_json_file(STATE_FILE, {})
    if isinstance(loaded, dict):
        state.update(loaded)
    state.setdefault("active_candidates", {})
    state.setdefault("last_dual_scan_hash", "")

def save_state():
    save_json_file(STATE_FILE, state)

def load_watchlist():
    default_items = ["BRIS", "ANTM", "PTBA", "TLKM", "INDF", "ICBP", "KLBF", "EXCL", "PGAS", "CPIN"]
    if not os.path.exists(WATCHLIST_FILE):
        return default_items
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
    return out if out else default_items

def load_unsupported_symbols():
    global unsupported_symbols
    data = load_json_file(UNSUPPORTED_SYMBOLS_FILE, [])
    unsupported_symbols = set(data if isinstance(data, list) else [])

def save_unsupported_symbols():
    save_json_file(UNSUPPORTED_SYMBOLS_FILE, sorted(list(unsupported_symbols)))

def mark_symbol_unsupported(symbol):
    symbol = symbol.upper().strip()
    if symbol not in unsupported_symbols:
        unsupported_symbols.add(symbol)
        save_unsupported_symbols()

WATCHLIST = load_watchlist()

def load_quick_pool():
    data = load_json_file(QUICK_POOL_FILE, [])
    if isinstance(data, list):
        return [str(x).upper().strip() for x in data if str(x).strip()]
    return []

def save_quick_pool(items):
    cleaned = []
    seen = set()
    for x in items:
        s = str(x).upper().strip()
        if not s or s in seen:
            continue
        seen.add(s)
        cleaned.append(s)
    save_json_file(QUICK_POOL_FILE, cleaned[:QUICK_POOL_MAX])

def refresh_quick_pool(result):
    symbols = []
    for d in result.get("combined", []):
        symbols.append(d["symbol"])
    for item in result.get("breakout", []):
        symbols.append(item["data"]["symbol"])
    for item in result.get("pullback", []):
        symbols.append(item["data"]["symbol"])
    for s in state.get("active_candidates", {}).keys():
        symbols.append(s)
    old_pool = load_quick_pool()
    symbols.extend(old_pool[:QUICK_POOL_MAX])
    save_quick_pool(symbols)

def get_quick_scan_universe():
    symbols = []
    for s in state.get("active_candidates", {}).keys():
        symbols.append(s)
    symbols.extend(load_quick_pool())
    if len(symbols) < 10:
        symbols.extend(WATCHLIST[:min(40, len(WATCHLIST))])
    seen = set()
    out = []
    for s in symbols:
        s = str(s).upper().strip()
        if not s or s in seen:
            continue
        seen.add(s)
        out.append(s)
    return out[:QUICK_POOL_MAX]

def send_message(chat_id, text):
    try:
        requests.post(f"{URL}/sendMessage", json={"chat_id": chat_id, "text": text}, timeout=30)
    except Exception:
        pass

def yahoo_symbol(symbol):
    symbol = symbol.upper().strip()
    return symbol if symbol.endswith(".JK") else f"{symbol}.JK"

def safe_yahoo_history(symbol, period="1y", interval="1d"):
    try:
        fake_out = io.StringIO()
        fake_err = io.StringIO()
        with redirect_stdout(fake_out), redirect_stderr(fake_err):
            ticker = yf.Ticker(yahoo_symbol(symbol))
            return ticker.history(period=period, interval=interval)
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

    out["VALUE_TRADED"] = out["Close"] * out["Volume"]
    out["VALAVG5"] = out["VALUE_TRADED"].rolling(5).mean()
    return out

def timing_label(close, low, high):
    rng = high - low if high > low else 0.01
    pos = (close - low) / rng
    if pos < 0.45:
        return "EARLY", "masih awal gerakan"
    if pos < 0.72:
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

def detect_micro_breakout(df):
    recent = df.iloc[-7:].copy()
    if recent.empty or len(recent) < 5:
        return False, 0.0, 0.0
    last = recent.iloc[-1]
    prev = recent.iloc[-2]
    ref_high = float(recent["High"].iloc[:-1].max())
    breakout = float(last["Close"]) > ref_high * 1.001
    body = abs(float(last["Close"]) - float(last["Open"]))
    rng = max(float(last["High"]) - float(last["Low"]), 0.01)
    body_ratio = body / rng
    vol_ratio = float(last["Volume"]) / max(float(recent["Volume"].iloc[:-1].mean()), 1.0)
    bullish = float(last["Close"]) >= float(prev["Close"])
    return bool(breakout and bullish), round(body_ratio, 4), round(vol_ratio, 4)

def detect_fake_breakout(close, high, low, open_price, recent_high, change_pct):
    rng = high - low if high > low else 0.01
    close_range_pct = (close - low) / rng
    upper_wick_pct = (high - max(open_price, close)) / rng
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
    width_pct = ((base_high - base_low) / base_high) * 100 if base_high else 0
    return round(base_low, 2), round(base_high, 2), width_pct <= 6.0

def build_entry_zone(setup, close, ma20, ma50, base_low, base_high):
    if base_low is None or base_high is None:
        return round(close * 0.994, 2), round(close * 0.998, 2), "fallback"
    if setup == "SIDEWAY ACCUMULATION PREPARE":
        width = max(base_high - base_low, 0.01)
        return round(base_low, 2), round(base_low + width * 0.25, 2), "base support"
    if setup == "SUPPORT BOUNCE PREPARE":
        support = min(base_low, ma20, ma50)
        return round(support * 0.998, 2), round(support * 1.004, 2), "support bounce"
    if setup == "PULLBACK_IDEAL":
        ref = ma20 if ma20 else close
        return round(ref * 0.992, 2), round(ref * 1.006, 2), "pullback ma20"
    if setup == "PULLBACK_DEEP":
        ref = ma50 if ma50 else close
        return round(ref * 0.99, 2), round(ref * 1.01, 2), "pullback ma50"
    if setup == "VALID_BREAKOUT_EXECUTE":
        return round(base_high * 0.998, 2), round(base_high * 1.006, 2), "breakout retest"
    if setup == "BREAKOUT_RETEST_READY":
        return round(base_high * 0.995, 2), round(base_high * 1.002, 2), "retest breakout"
    if setup == "BREAKOUT_FOLLOW_THROUGH":
        return round(close * 0.985, 2), round(close * 0.993, 2), "micro pullback"
    return round(base_low, 2), round(base_low + (base_high - base_low) * 0.2, 2), "watch only"

def validation_status(close, bid_low, bid_high, trigger, invalidation, fake_breakout, setup, volume_score, range_position, trend_bias, rsi, micro_breakout=False, micro_body_ratio=0.0, micro_vol_ratio=0.0):
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
    if setup == "PULLBACK_IDEAL":
        if micro_breakout and micro_body_ratio >= 0.45 and micro_vol_ratio >= 1.05:
            return "VALID ENTRY", "auto upgrade: micro breakout dari base dekat MA20"
        if bid_low <= close <= bid_high:
            return "VALID ENTRY", "pullback sehat dekat MA20"
        return "WAIT", "pullback sehat tapi belum ada trigger naik"
    if setup == "PULLBACK_DEEP":
        if micro_breakout and micro_body_ratio >= 0.50 and micro_vol_ratio >= 1.10:
            return "VALID ENTRY", "auto upgrade: deep pullback mulai mantul"
        if bid_low <= close <= bid_high:
            return "VALID ENTRY", "pullback dalam dekat MA50"
        if close <= bid_high * 1.02 and micro_breakout:
            return "VALID ENTRY", "auto upgrade: mulai keluar dari area pullback"
        return "WAIT", "deep pullback masih base, tunggu trigger naik"
    if bid_low <= close <= bid_high and range_position <= 0.65:
        return "VALID ENTRY", "harga di area eksekusi"
    if close <= bid_high * 1.01 and range_position <= 0.65 and setup in ["SIDEWAY ACCUMULATION PREPARE", "SUPPORT BOUNCE PREPARE"]:
        return "VALID ENTRY", "masih dekat area, boleh cicil kecil"
    if setup == "VALID_BREAKOUT_EXECUTE" and volume_score > 0 and close <= trigger * 1.02:
        return "VALID ENTRY", "breakout valid, masih dekat trigger"
    if setup == "BREAKOUT_RETEST_READY" and volume_score > 0 and bid_low <= close <= trigger:
        return "VALID ENTRY", "retest sehat"
    if setup == "BREAKOUT_FOLLOW_THROUGH":
        ma20_gap_pct = ((close - bid_low) / close) * 100 if close else 0
        if rsi > 78 or ma20_gap_pct > 4.0:
            return "WAIT", "momentum terlalu panas, tunggu micro pullback"
        if close <= trigger * 1.03 and rsi <= 78 and volume_score >= 0:
            return "VALID ENTRY", "momentum continuation, boleh cicil kecil"
        return "WAIT", "momentum sudah tinggi, tunggu micro pullback"
    return "WAIT", "tunggu area ideal"

def get_regime_snapshot(symbol):
    symbol = symbol.upper().strip()
    if symbol in unsupported_symbols:
        return None
    hist = safe_yahoo_history(symbol)
    if hist is None or hist.empty or len(hist) < 60:
        return None
    try:
        hist = calc_indicators(hist)
        last = hist.iloc[-1]
        prev = hist.iloc[-2]
        close = float(last["Close"])
        prev_close = float(prev["Close"])
        ma20 = float(last["MA20"]) if pd.notna(last["MA20"]) else close
        ma50 = float(last["MA50"]) if pd.notna(last["MA50"]) else close
        rsi = float(last["RSI"]) if pd.notna(last["RSI"]) else 50.0
        macd_hist = float(last["MACD_HIST"]) if pd.notna(last["MACD_HIST"]) else 0.0
        volume_today = float(last["Volume"])
        value_traded = float(last["VALUE_TRADED"]) if pd.notna(last["VALUE_TRADED"]) else close * volume_today
        valavg5 = float(last["VALAVG5"]) if pd.notna(last["VALAVG5"]) else value_traded
        change_pct = ((close - prev_close) / prev_close) * 100 if prev_close else 0.0
        volume_label, _ = classify_volume(value_traded, valavg5)
        return {
            "symbol": symbol,
            "close": close,
            "ma20": ma20,
            "ma50": ma50,
            "rsi": rsi,
            "macd_hist": macd_hist,
            "change_pct": change_pct,
            "volume": volume_label,
        }
    except Exception:
        return None

def detect_market_regime_from_watchlist():
    regime_data = []
    for symbol in WATCHLIST:
        snap = get_regime_snapshot(symbol)
        if snap:
            regime_data.append(snap)

    total = len(regime_data)
    if total == 0:
        return {"label": "NO_DATA", "sample_size": 0, "bullish_ma20_pct": 0.0, "bullish_ma50_pct": 0.0, "momentum_pct": 0.0, "expansion_pct": 0.0, "breakout_count": 0, "pullback_count": 0}

    bullish_ma20 = sum(1 for d in regime_data if d["close"] > d["ma20"])
    bullish_ma50 = sum(1 for d in regime_data if d["close"] > d["ma50"])
    momentum = sum(1 for d in regime_data if d["rsi"] > 50 and d["macd_hist"] > 0)
    expansion = sum(1 for d in regime_data if d["change_pct"] > 1 and d["volume"] == "Kuat")

    bullish_ma20_pct = bullish_ma20 / total * 100
    bullish_ma50_pct = bullish_ma50 / total * 100
    momentum_pct = momentum / total * 100
    expansion_pct = expansion / total * 100

    if bullish_ma20_pct >= 55 and momentum_pct >= 50 and expansion_pct >= 12:
        label = "BREAKOUT_FRIENDLY"
    elif bullish_ma20_pct >= 50 and momentum_pct >= 40 and expansion_pct < 12:
        label = "PULLBACK_FRIENDLY"
    elif bullish_ma20_pct < 40 and momentum_pct < 35:
        label = "WEAK_MARKET"
    else:
        label = "MIXED"

    return {
        "label": label,
        "sample_size": total,
        "bullish_ma20_pct": round(bullish_ma20_pct, 1),
        "bullish_ma50_pct": round(bullish_ma50_pct, 1),
        "momentum_pct": round(momentum_pct, 1),
        "expansion_pct": round(expansion_pct, 1),
        "breakout_count": 0,
        "pullback_count": 0
    }

def get_market_snapshot(symbol, regime_label=None):
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
        micro_breakout, micro_body_ratio, micro_vol_ratio = detect_micro_breakout(hist)

        fake_breakout, _, breakout_attempt, close_range_pct, upper_wick_pct = detect_fake_breakout(close, high, low, open_price, recent_high, change_pct)
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

        ma20_distance_pct = ((close - ma20) / ma20) * 100 if ma20 else 0
        pullback_friendly = regime_label == "PULLBACK_FRIENDLY"
        pullback_ideal_rsi_min = 45 if pullback_friendly else 48
        pullback_ideal_rsi_max = 65 if pullback_friendly else 62
        pullback_deep_rsi_min = 38 if pullback_friendly else 40
        pullback_deep_rsi_max = 60 if pullback_friendly else 58
        pullback_ma20_dist = 0.04 if pullback_friendly else 0.03
        pullback_ma50_dist = 0.065 if pullback_friendly else 0.05
        pullback_ma50_floor = 0.98 if pullback_friendly else 0.985

        pullback_ideal = (
            trend_bias == "bullish"
            and pd.notna(ma20)
            and abs(close - ma20) / ma20 <= pullback_ma20_dist
            and pullback_ideal_rsi_min <= rsi <= pullback_ideal_rsi_max
            and close >= ma20 * 0.99
            and macd_hist >= -2.5
            and value_traded >= MIN_VALUE_TRADED
        )
        pullback_deep = (
            trend_bias != "bearish"
            and pd.notna(ma50)
            and abs(close - ma50) / ma50 <= pullback_ma50_dist
            and pullback_deep_rsi_min <= rsi <= pullback_deep_rsi_max
            and close >= ma50 * pullback_ma50_floor
            and macd >= signal * 0.82
            and value_traded >= MIN_VALUE_TRADED
        )

        breakout_follow_through = (
            breakout_attempt and not fake_breakout and volume_score >= 0 and trend_bias == "bullish"
            and close > (base_high if base_high else recent_high) * 1.01
            and close_range_pct >= 0.72 and 62 <= rsi <= 78 and macd_hist > 0
            and change_pct <= 6.5 and ma20_distance_pct <= 8.0
        )

        setup = None
        if breakout_attempt and not fake_breakout and volume_score > 0:
            if move_from_base_pct <= 2.5:
                setup = "VALID_BREAKOUT_EXECUTE"
            elif breakout_follow_through:
                setup = "BREAKOUT_FOLLOW_THROUGH"
            elif move_from_base_pct <= 5.0:
                setup = "BREAKOUT_RETEST_READY"
            else:
                setup = "OVEREXTENDED"
        elif strong_accumulation:
            setup = "SIDEWAY ACCUMULATION PREPARE"
        elif support_bounce:
            setup = "SUPPORT BOUNCE PREPARE"
        elif pullback_ideal:
            setup = "PULLBACK_IDEAL"
        elif pullback_deep:
            setup = "PULLBACK_DEEP"
        elif is_sideway:
            setup = "WEAK SIDEWAY"
        else:
            return None

        if setup in ["WEAK SIDEWAY", "OVEREXTENDED"]:
            return None
        if trend_bias == "bearish" and setup in ["SIDEWAY ACCUMULATION PREPARE", "SUPPORT BOUNCE PREPARE", "PULLBACK_IDEAL", "PULLBACK_DEEP"]:
            return None
        if dead_market and setup in ["SIDEWAY ACCUMULATION PREPARE", "SUPPORT BOUNCE PREPARE"]:
            return None
        if bad_sideway and setup in ["SIDEWAY ACCUMULATION PREPARE", "SUPPORT BOUNCE PREPARE"]:
            return None
        if recent_drop_pct > 8 and is_sideway and setup in ["SIDEWAY ACCUMULATION PREPARE", "SUPPORT BOUNCE PREPARE"]:
            return None
        if volume_score < -10 and setup != "SUPPORT BOUNCE PREPARE":
            return None
        if rsi > 78 and setup not in ["VALID_BREAKOUT_EXECUTE", "BREAKOUT_RETEST_READY", "BREAKOUT_FOLLOW_THROUGH"]:
            return None
        if setup == "BREAKOUT_FOLLOW_THROUGH" and rsi > 78:
            return None
        if setup == "BREAKOUT_FOLLOW_THROUGH" and change_pct > 6.5:
            return None
        if timing == "LATE" and setup in ["SIDEWAY ACCUMULATION PREPARE", "SUPPORT BOUNCE PREPARE"]:
            return None
        if timing == "LATE" and setup in ["PULLBACK_IDEAL", "PULLBACK_DEEP"] and not pullback_friendly:
            return None

        bid_low, bid_high, _ = build_entry_zone(setup, close, ma20, ma50, base_low, base_high)
        short_term_high = float(hist["High"].iloc[-4:-1].max()) if len(hist) >= 4 else (base_high if base_high else close)
        trigger = round((base_high if base_high else close) * 1.003, 2)
        if setup in ["SIDEWAY ACCUMULATION PREPARE", "SUPPORT BOUNCE PREPARE", "PULLBACK_IDEAL", "PULLBACK_DEEP"]:
            trigger = round(short_term_high * 1.001, 2)
        if setup == "BREAKOUT_FOLLOW_THROUGH":
            trigger = round(close * 1.003, 2)
        invalidation = round((bid_low if bid_low else close) * 0.992, 2)

        if ((close - bid_high) / close * 100 if close > bid_high else 0) > MAX_DISTANCE_TO_BID_PCT and setup in ["SIDEWAY ACCUMULATION PREPARE", "SUPPORT BOUNCE PREPARE"]:
            return None

        trend = structure = execution = confirmation = penalty = 0
        reasons, tech_notes = [], []

        if close > ma20:
            trend += 3; tech_notes.append("di atas MA20")
        else:
            trend -= 4; tech_notes.append("di bawah MA20")
        if ma20 > ma50:
            trend += 4; tech_notes.append("MA20 > MA50")
        else:
            trend -= 3; tech_notes.append("MA20 < MA50")
        if close > ma100:
            trend += 2; tech_notes.append("di atas MA100")
        else:
            tech_notes.append("di bawah MA100")
        if rsi >= 55:
            trend += 3; tech_notes.append(f"RSI {rsi:.1f} kuat")
        elif rsi < 45:
            trend -= 4; tech_notes.append(f"RSI {rsi:.1f} lemah")
        else:
            tech_notes.append(f"RSI {rsi:.1f} netral")
        if macd_hist > 0:
            trend += 3; tech_notes.append("MACD menguat")
        else:
            trend -= 2; tech_notes.append("MACD lemah")

        if setup == "SIDEWAY ACCUMULATION PREPARE":
            structure += 18; reasons.append("base sehat dekat bawah range")
        elif setup == "SUPPORT BOUNCE PREPARE":
            structure += 16; reasons.append("pantulan support dekat area bawah")
        elif setup == "VALID_BREAKOUT_EXECUTE":
            structure += 16; reasons.append("breakout valid belum terlalu jauh")
        elif setup == "BREAKOUT_RETEST_READY":
            structure += 15; reasons.append("breakout sudah jalan, tunggu retest")
        elif setup == "BREAKOUT_FOLLOW_THROUGH":
            structure += 18; reasons.append("breakout lanjut tanpa retest, momentum masih jalan")
        elif setup == "PULLBACK_IDEAL":
            structure += 17; reasons.append("pullback sehat dekat MA20")
        elif setup == "PULLBACK_DEEP":
            structure += 14; reasons.append("pullback dalam dekat MA50")

        if is_sideway:
            structure += 4; reasons.append("range rapi")
        if near_lower_range:
            structure += 6; reasons.append("posisi dekat support")
        if volume_score > 0:
            confirmation += 8; reasons.append("volume mendukung")
        if micro_breakout and setup in ["PULLBACK_IDEAL", "PULLBACK_DEEP"]:
            confirmation += 6; reasons.append("micro breakout terdeteksi")
        if setup == "BREAKOUT_RETEST_READY" and trend_bias != "bearish":
            confirmation += 3
        if setup == "BREAKOUT_FOLLOW_THROUGH":
            confirmation += 5
        if prev_change_pct > 0 and change_pct > 0:
            confirmation += 2

        if fake_breakout:
            penalty += 20
        if upper_wick_pct > 0.35:
            penalty += 6
        if trend_bias == "neutral":
            penalty += 4
        if dead_market and setup in ["PULLBACK_IDEAL", "PULLBACK_DEEP"]:
            penalty += 4
        if setup == "BREAKOUT_RETEST_READY":
            penalty += 4
        if move_from_base_pct > 3 and setup != "BREAKOUT_FOLLOW_THROUGH":
            penalty += 8
        if setup == "VALID_BREAKOUT_EXECUTE" and close < ma50:
            penalty += 4
        if setup == "VALID_BREAKOUT_EXECUTE" and close < ma100:
            penalty += 3
        if setup == "BREAKOUT_FOLLOW_THROUGH" and rsi > 76:
            penalty += 4

        if bid_low <= close <= bid_high and near_lower_range:
            execution += 16
        elif close < bid_low:
            execution += 8
        elif setup == "VALID_BREAKOUT_EXECUTE" and close <= trigger * 1.01:
            execution += 12
        elif setup == "BREAKOUT_RETEST_READY" and close <= trigger:
            execution += 10
        elif setup == "BREAKOUT_FOLLOW_THROUGH" and close <= trigger * 0.998:
            execution += 9
        elif setup == "PULLBACK_IDEAL" and close <= bid_high * 1.005:
            execution += 14
        elif setup == "PULLBACK_DEEP" and close <= bid_high * 1.005:
            execution += 12
        else:
            penalty += 6

        if timing == "EARLY":
            execution += 10
        elif timing == "MID":
            execution += 4
        elif setup == "BREAKOUT_FOLLOW_THROUGH" and timing == "LATE":
            execution += 2

        risk_pct = ((close - invalidation) / close) * 100 if close else 0
        reward_pct = ((trigger - close) / close) * 100 if close else 0
        if setup != "BREAKOUT_FOLLOW_THROUGH" and (reward_pct <= 0 or reward_pct < risk_pct):
            penalty += 5

        score = int(round(0.25 * (trend * 4) + 0.25 * structure + 0.25 * execution + 0.15 * confirmation + 0.10 * max(volume_score, 0) - 0.30 * penalty + 50))
        tp1 = round(close * 1.01, 2)
        tp2 = round(close * 1.02, 2)

        v_status, v_reason = validation_status(close, bid_low, bid_high, trigger, invalidation, fake_breakout, setup, volume_score, range_position, trend_bias, rsi, micro_breakout, micro_body_ratio, micro_vol_ratio)
        if v_status == "INVALID":
            return None

        confidence = "HIGH" if score >= 85 else "MEDIUM" if score >= 70 else "LOW"
        if setup in ["BREAKOUT_RETEST_READY", "BREAKOUT_FOLLOW_THROUGH"] and confidence == "LOW":
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
            "confidence": confidence,
            "trend_bias": trend_bias,
            "micro_breakout": micro_breakout,
            "micro_body_ratio": micro_body_ratio,
            "micro_vol_ratio": micro_vol_ratio,
            "ma20": round(ma20, 2),
            "ma50": round(ma50, 2),
            "rsi": round(rsi, 2),
            "macd_hist": round(macd_hist, 4),
            "rs_label": "N/A",
            "rs_bonus": 0,
            "rs_5": 0.0,
            "rs_20": 0.0
        }
    except Exception:
        mark_symbol_unsupported(symbol)
        return None

def candidate_key(data):
    return data["symbol"]

def decision_status(data):
    if data["setup"] == "BREAKOUT_FOLLOW_THROUGH":
        if data["status"] != "VALID ENTRY":
            return "WATCH MOMENTUM"
        if data.get("change_pct", 0) > 6.0:
            return "OVEREXTENDED MOMENTUM"
        return "MOMENTUM CONTINUATION"
    if data["setup"] == "PULLBACK_IDEAL":
        return "ACTIVE BID PULLBACK" if data["status"] == "VALID ENTRY" else "WATCH PULLBACK"
    if data["setup"] == "PULLBACK_DEEP":
        return "ACTIVE BID PULLBACK" if data["status"] == "VALID ENTRY" else "WATCH DEEP PULLBACK"
    if data["status"] == "VALID ENTRY":
        if data["setup"] == "VALID_BREAKOUT_EXECUTE" and data["close"] > data["bid_high"]:
            return "ACTIVE BID EARLY"
        if data["close"] <= data["bid_high"]:
            return "ACTIVE BID"
        return "ACTIVE BID EARLY"
    if data["setup"] == "BREAKOUT_RETEST_READY":
        return "WAIT RETEST"
    return "WATCH WAIT"

def action_priority(data):
    status = decision_status(data)
    priority_map = {
        "ACTIVE BID PULLBACK": 9,
        "ACTIVE BID": 8,
        "ACTIVE BID EARLY": 7,
        "MOMENTUM CONTINUATION": 6,
        "WAIT RETEST": 5,
        "WATCH PULLBACK": 4,
        "WATCH DEEP PULLBACK": 3,
        "WATCH WAIT": 2,
        "WATCH MOMENTUM": 1,
        "OVEREXTENDED MOMENTUM": 0,
    }
    return priority_map.get(status, 0)

def update_relative_strength_map(scanned_data):
    global relative_strength_map
    rows = []
    for d in scanned_data:
        try:
            hist = safe_yahoo_history(d["symbol"])
            if hist is None or hist.empty or len(hist) < 25:
                continue
            close_now = float(hist["Close"].iloc[-1])
            close_5 = float(hist["Close"].iloc[-6]) if len(hist) >= 6 else close_now
            close_20 = float(hist["Close"].iloc[-21]) if len(hist) >= 21 else close_now
            ret_5 = ((close_now - close_5) / close_5) * 100 if close_5 else 0.0
            ret_20 = ((close_now - close_20) / close_20) * 100 if close_20 else 0.0
            rows.append({"symbol": d["symbol"], "ret_5": ret_5, "ret_20": ret_20})
        except Exception:
            continue

    if not rows:
        relative_strength_map = {}
        return

    rows_5 = sorted(rows, key=lambda x: x["ret_5"], reverse=True)
    rows_20 = sorted(rows, key=lambda x: x["ret_20"], reverse=True)
    n = len(rows)
    rank5 = {}
    rank20 = {}
    for idx, row in enumerate(rows_5):
        rank5[row["symbol"]] = ((n - idx) / n) * 100 if n else 0
    for idx, row in enumerate(rows_20):
        rank20[row["symbol"]] = ((n - idx) / n) * 100 if n else 0

    rs_map = {}
    for row in rows:
        symbol = row["symbol"]
        pct5 = rank5.get(symbol, 50)
        pct20 = rank20.get(symbol, 50)
        if pct5 >= 75 and pct20 >= 75:
            label = "LEADER STRONG"; bonus = 5
        elif pct5 >= 75 and pct20 >= 40:
            label = "EMERGING LEADER"; bonus = 3
        elif pct20 >= 75 and pct5 < 45:
            label = "PULLBACK LEADER"; bonus = 2
        elif pct5 <= 30 and pct20 <= 30:
            label = "LAGGARD"; bonus = -4
        else:
            label = "AVERAGE"; bonus = 0
        rs_map[symbol] = {
            "ret_5": round(row["ret_5"], 2),
            "ret_20": round(row["ret_20"], 2),
            "pct_rank_5": round(pct5, 1),
            "pct_rank_20": round(pct20, 1),
            "label": label,
            "bonus": bonus
        }
    relative_strength_map = rs_map

def detect_market_regime(scanned_data=None):
    regime_data = []
    for symbol in WATCHLIST:
        snap = get_regime_snapshot(symbol)
        if snap:
            regime_data.append(snap)

    total = len(regime_data)
    if total == 0:
        return {"label": "NO_DATA", "sample_size": 0, "bullish_ma20_pct": 0.0, "bullish_ma50_pct": 0.0, "momentum_pct": 0.0, "expansion_pct": 0.0, "breakout_count": 0, "pullback_count": 0}

    bullish_ma20 = sum(1 for d in regime_data if d["close"] > d["ma20"])
    bullish_ma50 = sum(1 for d in regime_data if d["close"] > d["ma50"])
    momentum = sum(1 for d in regime_data if d["rsi"] > 50 and d["macd_hist"] > 0)
    expansion = sum(1 for d in regime_data if d["change_pct"] > 1 and d["volume"] == "Kuat")

    candidate_data = scanned_data or []
    breakout_count = sum(1 for d in candidate_data if d["setup"] in ["VALID_BREAKOUT_EXECUTE", "BREAKOUT_RETEST_READY", "BREAKOUT_FOLLOW_THROUGH"])
    pullback_count = sum(1 for d in candidate_data if d["setup"] in ["SIDEWAY ACCUMULATION PREPARE", "SUPPORT BOUNCE PREPARE", "PULLBACK_IDEAL", "PULLBACK_DEEP"])

    bullish_ma20_pct = bullish_ma20 / total * 100
    bullish_ma50_pct = bullish_ma50 / total * 100
    momentum_pct = momentum / total * 100
    expansion_pct = expansion / total * 100

    if bullish_ma20_pct >= 55 and momentum_pct >= 50 and expansion_pct >= 12 and breakout_count >= pullback_count:
        label = "BREAKOUT_FRIENDLY"
    elif bullish_ma20_pct >= 50 and momentum_pct >= 40 and expansion_pct < 12 and pullback_count >= breakout_count:
        label = "PULLBACK_FRIENDLY"
    elif bullish_ma20_pct < 40 and momentum_pct < 35:
        label = "WEAK_MARKET"
    else:
        label = "MIXED"

    return {
        "label": label,
        "sample_size": total,
        "bullish_ma20_pct": round(bullish_ma20_pct, 1),
        "bullish_ma50_pct": round(bullish_ma50_pct, 1),
        "momentum_pct": round(momentum_pct, 1),
        "expansion_pct": round(expansion_pct, 1),
        "breakout_count": breakout_count,
        "pullback_count": pullback_count
    }

def score_breakout_path(data):
    score = int(data.get("score", 0))
    if data["setup"] == "VALID_BREAKOUT_EXECUTE":
        score += 8
    if data["setup"] == "BREAKOUT_RETEST_READY":
        score += 6
    if data["setup"] == "BREAKOUT_FOLLOW_THROUGH":
        score += 10
    if data["volume"] == "Kuat":
        score += 6
    if decision_status(data) in ["ACTIVE BID EARLY", "MOMENTUM CONTINUATION"]:
        score += 4
    if data["confidence"] == "MEDIUM":
        score += 4
    if data["trend_bias"] == "bearish":
        score -= 8
    score += int(data.get("rs_bonus", 0))
    return score

def score_pullback_path(data):
    score = int(data.get("score", 0))
    if data["setup"] == "SIDEWAY ACCUMULATION PREPARE":
        score += 8
    if data["setup"] == "SUPPORT BOUNCE PREPARE":
        score += 6
    if data["setup"] == "PULLBACK_IDEAL":
        score += 12
    if data["setup"] == "PULLBACK_DEEP":
        score += 8
    if decision_status(data) in ["ACTIVE BID", "ACTIVE BID PULLBACK"]:
        score += 5
    if data["timing"] == "EARLY":
        score += 3
    if data["trend_bias"] == "bearish":
        score -= 8
    score += int(data.get("rs_bonus", 0))
    return score

def apply_market_regime_bonus(data, base_score, path_type, regime_label):
    score = int(base_score)
    if regime_label == "BREAKOUT_FRIENDLY":
        if path_type == "breakout":
            score += 5
        elif path_type == "pullback":
            score -= 1
    elif regime_label == "PULLBACK_FRIENDLY":
        if path_type == "pullback":
            score += 5
        elif path_type == "breakout":
            score -= 1
    elif regime_label == "WEAK_MARKET":
        if decision_status(data) in ["ACTIVE BID EARLY", "MOMENTUM CONTINUATION"]:
            score -= 5
        elif path_type == "breakout":
            score -= 3
        elif path_type == "pullback":
            score -= 1
    return score

def get_action_hint(data):
    status = decision_status(data)
    if status == "ACTIVE BID":
        return "Aksi: boleh bid bertahap di area bid zone."
    if status == "ACTIVE BID EARLY":
        return "Aksi: boleh cicil kecil, jangan full size."
    if status == "ACTIVE BID PULLBACK":
        return "Aksi: boleh mulai cicil pullback, fokus dekat support."
    if status == "WAIT RETEST":
        return "Aksi: jangan kejar, tunggu masuk zona retest."
    if status == "WATCH PULLBACK":
        return "Aksi: pantau trigger mikro sebelum entry."
    if status == "WATCH DEEP PULLBACK":
        return "Aksi: tunggu bounce / micro breakout dari area bawah, boleh pantau lebih dekat saat regime pullback."
    if status == "MOMENTUM CONTINUATION":
        return "Aksi: boleh cicil kecil cepat, disiplin ambil profit."
    if status == "OVEREXTENDED MOMENTUM":
        return "Aksi: hindari entry baru, tunggu pullback sehat."
    if status == "WATCH MOMENTUM":
        return "Aksi: tunggu micro pullback, jangan kejar candle tinggi."
    return "Aksi: wait and see, belum ada eksekusi valid."

def format_candidate_block(data, score_name, rank_score, base_rank_score=None):
    score_line = f"{score_name}: {rank_score}"
    if base_rank_score is not None and base_rank_score != rank_score:
        score_line = f"{score_name}: {rank_score} (base {base_rank_score})"
    return "\n".join([
        data["symbol"],
        f"Status: {decision_status(data)}",
        f"Setup: {data['setup']}",
        f"Confidence: {data['confidence']}",
        f"RS: {data.get('rs_label','N/A')} ({data.get('rs_5',0):+.2f}% /5d | {data.get('rs_20',0):+.2f}% /20d)",
        f"Harga: {data['close']:.2f} ({data['change_pct']:+.2f}%)",
        score_line,
        f"Bid Zone: {data['bid_low']:.2f} - {data['bid_high']:.2f}",
        f"Trigger: {data['trigger']:.2f}",
        f"Invalidation: {data['invalidation']:.2f}",
        f"Alasan: {data['reason']}",
        get_action_hint(data)
    ])

def build_market_regime_header(regime):
    return "\n".join([
        f"MARKET REGIME: {regime['label']}",
        f"Sample Watchlist: {regime.get('sample_size', 0)} saham",
        f"Breadth MA20: {regime['bullish_ma20_pct']:.1f}%",
        f"Breadth MA50: {regime['bullish_ma50_pct']:.1f}%",
        f"Momentum: {regime['momentum_pct']:.1f}%",
        f"Expansion: {regime['expansion_pct']:.1f}%",
        f"Breakout vs Pullback: {regime['breakout_count']} vs {regime['pullback_count']}",
        ""
    ])

def build_dual_path_text(result):
    lines = []
    regime = result.get("market_regime")
    if regime:
        lines.append(build_market_regime_header(regime).strip())
        lines.append("")
    lines += ["AUTOSCAN DUA JALUR", "", "TOP BREAKOUT KERAS"]
    if result["breakout"]:
        for i, item in enumerate(result["breakout"], start=1):
            lines.append(f"{i}. {format_candidate_block(item['data'], 'Score Breakout', item['rank_score'], item.get('base_rank_score'))}")
            lines.append("")
    else:
        lines += ["Tidak ada kandidat breakout.", ""]
    lines.append("TOP PULLBACK SUPPORT")
    if result["pullback"]:
        for i, item in enumerate(result["pullback"], start=1):
            lines.append(f"{i}. {format_candidate_block(item['data'], 'Score Pullback', item['rank_score'], item.get('base_rank_score'))}")
            lines.append("")
    else:
        lines += ["Tidak ada kandidat pullback.", ""]
    return "\n".join(lines).strip()

def load_signal_journal():
    data = load_json_file(SIGNAL_JOURNAL_FILE, [])
    return data if isinstance(data, list) else []

def save_signal_journal(data):
    save_json_file(SIGNAL_JOURNAL_FILE, data)

def load_signal_evals():
    data = load_json_file(SIGNAL_EVAL_FILE, [])
    return data if isinstance(data, list) else []

def save_signal_evals(data):
    save_json_file(SIGNAL_EVAL_FILE, data)

def signal_id(data):
    return f"{datetime.now().strftime('%Y-%m-%d_%H:%M')}_{data['symbol']}"

def add_signal_to_journal(data):
    journal = load_signal_journal()
    sid = signal_id(data)
    if any(x.get("id") == sid for x in journal):
        return
    journal.append({
        "id": sid,
        "time": datetime.now().strftime("%Y-%m-%d %H:%M"),
        "symbol": data["symbol"],
        "status": decision_status(data),
        "setup": data["setup"],
        "close": data["close"],
        "bid_low": data["bid_low"],
        "bid_high": data["bid_high"],
        "trigger": data["trigger"],
        "invalidation": data["invalidation"],
        "score": data["score"],
        "eval_15m": None,
        "eval_30m": None,
        "eval_60m": None
    })
    save_signal_journal(journal)

def get_latest_price(symbol):
    hist = safe_yahoo_history(symbol, period="5d", interval="5m")
    if hist is None or hist.empty:
        hist = safe_yahoo_history(symbol, period="1mo", interval="1d")
    if hist is None or hist.empty:
        return None
    try:
        return float(hist["Close"].iloc[-1])
    except Exception:
        return None

def eval_active_signal(sig, current_price):
    base = sig["close"]
    invalid = sig["invalidation"]
    pct = ((current_price - base) / base) * 100 if base else 0
    if current_price <= invalid:
        return {"label": "GAGAL", "pct": round(pct, 2), "note": "tembus invalidation"}
    if pct >= 1.0:
        return {"label": "BENAR", "pct": round(pct, 2), "note": "naik minimal 1%"}
    return {"label": "NETRAL", "pct": round(pct, 2), "note": "belum follow-through"}

def eval_wait_retest(sig, current_price):
    bid_low, bid_high, invalid = sig["bid_low"], sig["bid_high"], sig["invalidation"]
    pct = round(((current_price - sig["close"]) / sig["close"]) * 100, 2)
    if current_price <= invalid:
        return {"label": "GAGAL", "pct": pct, "note": "retest gagal / tembus invalidation"}
    if bid_low <= current_price <= bid_high:
        return {"label": "BENAR", "pct": pct, "note": "masuk area retest"}
    if current_price > sig["trigger"] * 1.03:
        return {"label": "GAGAL", "pct": pct, "note": "lari tanpa retest"}
    return {"label": "NETRAL", "pct": pct, "note": "belum retest"}

def eval_watch_wait(sig, current_price):
    pct = ((current_price - sig["close"]) / sig["close"]) * 100 if sig["close"] else 0
    if pct >= 2.0:
        return {"label": "SALAH", "pct": round(pct, 2), "note": "ternyata breakout sehat"}
    return {"label": "BENAR", "pct": round(pct, 2), "note": "memang belum layak entry"}

def evaluate_pending_signals():
    journal = load_signal_journal()
    if not journal:
        return
    now = datetime.now()
    changed = False
    eval_rows = load_signal_evals()
    for sig in journal:
        try:
            sig_time = datetime.strptime(sig["time"], "%Y-%m-%d %H:%M")
        except Exception:
            continue
        current_price = None
        for mins in EVAL_INTERVALS:
            key = f"eval_{mins}m"
            if sig.get(key) is not None or now < sig_time + timedelta(minutes=mins):
                continue
            if current_price is None:
                current_price = get_latest_price(sig["symbol"])
            if current_price is None:
                continue
            if sig["status"] in ["ACTIVE BID", "ACTIVE BID EARLY", "MOMENTUM CONTINUATION"]:
                result = eval_active_signal(sig, current_price)
            elif sig["status"] == "WAIT RETEST":
                result = eval_wait_retest(sig, current_price)
            else:
                result = eval_watch_wait(sig, current_price)
            sig[key] = result
            eval_rows.append({"id": sig["id"], "symbol": sig["symbol"], "status": sig["status"], "minutes": mins, "result": result["label"], "pct": result["pct"], "note": result["note"], "time_eval": now.strftime("%Y-%m-%d %H:%M")})
            changed = True
    if changed:
        save_signal_journal(journal)
        save_signal_evals(eval_rows)

def build_journal_today_text():
    journal = load_signal_journal()
    today = datetime.now().strftime("%Y-%m-%d")
    rows = [x for x in journal if str(x.get("time", "")).startswith(today)]
    if not rows:
        return "Belum ada jurnal hari ini."
    lines = ["JURNAL HARI INI", ""]
    for sig in rows[:15]:
        lines += [sig["symbol"], f"Sinyal: {sig['status']}", f"Harga awal: {sig['close']:.2f}"]
        for mins in EVAL_INTERVALS:
            res = sig.get(f"eval_{mins}m")
            if res is None:
                lines.append(f"{mins}m: BELUM DIEVALUASI")
            else:
                lines.append(f"{mins}m: {res.get('label')} ({res.get('pct', 0):+.2f}%) - {res.get('note')}")
        lines.append("")
    return "\n".join(lines).strip()

def build_journal_summary_text():
    evals = load_signal_evals()
    if not evals:
        return "Belum ada ringkasan jurnal."
    total = len(evals)
    benar = sum(1 for x in evals if x.get("result") == "BENAR")
    gagal = sum(1 for x in evals if x.get("result") == "GAGAL")
    netral = sum(1 for x in evals if x.get("result") == "NETRAL")
    salah = sum(1 for x in evals if x.get("result") == "SALAH")
    lines = ["RINGKASAN JURNAL", "", f"Total evaluasi: {total}", f"BENAR: {benar}", f"GAGAL: {gagal}", f"NETRAL: {netral}", f"SALAH: {salah}", "", "Win rate per status:"]
    by_status = {}
    for x in evals:
        status = x.get("status", "-")
        by_status.setdefault(status, {"n": 0, "benar": 0})
        by_status[status]["n"] += 1
        if x.get("result") == "BENAR":
            by_status[status]["benar"] += 1
    for status, val in by_status.items():
        rate = (val["benar"] / val["n"] * 100) if val["n"] else 0
        lines.append(f"- {status}: {rate:.1f}% ({val['benar']}/{val['n']})")
    return "\n".join(lines)

def build_journal_stock_text(symbol):
    rows = [x for x in load_signal_journal() if x.get("symbol") == symbol.upper()]
    if not rows:
        return f"Belum ada jurnal untuk {symbol.upper()}."
    lines = [f"JURNAL {symbol.upper()}", ""]
    for sig in rows[-10:]:
        lines.append(f"{sig['time']} | {sig['status']} | harga {sig['close']:.2f}")
        for mins in EVAL_INTERVALS:
            res = sig.get(f"eval_{mins}m")
            lines.append(f"  {mins}m: BELUM" if res is None else f"  {mins}m: {res.get('label')} ({res.get('pct', 0):+.2f}%)")
        lines.append("")
    return "\n".join(lines).strip()

def scan_dual_path(universe=None, quick_mode=False):
    breakout_candidates, pullback_candidates, combined = [], [], []
    raw_breakout = []
    raw_pullback = []

    pre_regime = detect_market_regime_from_watchlist()
    pre_regime_label = pre_regime.get("label", "MIXED")

    symbols = universe if universe else WATCHLIST

    for symbol in symbols:
        data = get_market_snapshot(symbol, regime_label=pre_regime_label)
        if not data:
            continue
        combined.append(data)

    update_relative_strength_map(combined)

    for data in combined:
        rs = relative_strength_map.get(data["symbol"], {})
        data["rs_label"] = rs.get("label", "N/A")
        data["rs_bonus"] = rs.get("bonus", 0)
        data["rs_5"] = rs.get("ret_5", 0.0)
        data["rs_20"] = rs.get("ret_20", 0.0)

        if data["setup"] in ["VALID_BREAKOUT_EXECUTE", "BREAKOUT_RETEST_READY", "BREAKOUT_FOLLOW_THROUGH"]:
            raw_breakout.append(data)
        elif data["setup"] in ["SIDEWAY ACCUMULATION PREPARE", "SUPPORT BOUNCE PREPARE", "PULLBACK_IDEAL", "PULLBACK_DEEP"]:
            raw_pullback.append(data)

    regime = detect_market_regime(combined)
    regime_label = regime.get("label", "MIXED")

    for data in raw_breakout:
        base_rank = score_breakout_path(data)
        final_rank = apply_market_regime_bonus(data, base_rank, "breakout", regime_label)
        breakout_candidates.append({"data": data, "rank_score": final_rank, "base_rank_score": base_rank})

    for data in raw_pullback:
        base_rank = score_pullback_path(data)
        final_rank = apply_market_regime_bonus(data, base_rank, "pullback", regime_label)
        pullback_candidates.append({"data": data, "rank_score": final_rank, "base_rank_score": base_rank})

    breakout_candidates.sort(key=lambda x: x["rank_score"], reverse=True)
    pullback_candidates.sort(key=lambda x: x["rank_score"], reverse=True)
    combined.sort(key=lambda x: (action_priority(x), x["score"]), reverse=True)

    return {
        "breakout": breakout_candidates[:TOP_PER_PATH],
        "pullback": pullback_candidates[:TOP_PER_PATH],
        "combined": combined[:TOP_COMBINED],
        "market_regime": regime,
        "quick_mode": quick_mode,
        "universe_size": len(symbols),
    }

def sync_active_candidates_from_combined(combined):
    prev_map = state.get("active_candidates", {})
    new_map = {}
    for rank, data in enumerate(combined, start=1):
        key = candidate_key(data)
        prev = prev_map.get(key)
        data["rank"] = rank
        data["decision_status"] = decision_status(data)
        data["miss_count"] = 0
        new_map[key] = data
        if prev is None:
            add_signal_to_journal(data)
    state["active_candidates"] = new_map
    save_state()

def dual_scan_hash(result):
    parts = []
    regime = result.get("market_regime", {})
    parts.append(f"REGIME:{regime.get('label','-')}:{regime.get('sample_size',0)}:{regime.get('bullish_ma20_pct',0)}:{regime.get('bullish_ma50_pct',0)}:{regime.get('momentum_pct',0)}:{regime.get('expansion_pct',0)}")
    for item in result.get("breakout", []):
        d = item["data"]
        parts.append(f"B:{d['symbol']}:{decision_status(d)}:{item.get('rank_score',0)}:{d.get('close',0)}:{d.get('setup','-')}")
    for item in result.get("pullback", []):
        d = item["data"]
        parts.append(f"P:{d['symbol']}:{decision_status(d)}:{item.get('rank_score',0)}:{d.get('close',0)}:{d.get('setup','-')}")
    for d in result.get("combined", []):
        parts.append(f"C:{d['symbol']}:{decision_status(d)}:{d.get('score',0)}:{d.get('close',0)}:{d.get('setup','-')}")
    return "|".join(parts)


def should_notify_quick_update(prev_active, new_combined, old_digest, new_digest, quick_mode):
    if new_digest != old_digest:
        return True

    if not quick_mode:
        return False

    new_map = {d["symbol"]: d for d in new_combined}
    for symbol, prev in prev_active.items():
        new = new_map.get(symbol)
        if not new:
            continue

        prev_close = float(prev.get("close", 0) or 0)
        new_close = float(new.get("close", 0) or 0)

        if prev_close > 0:
            move_pct = abs((new_close - prev_close) / prev_close) * 100
            if move_pct >= 0.8:
                return True

        if prev.get("decision_status") != decision_status(new):
            return True

        if abs(int(new.get("score", 0)) - int(prev.get("score", 0))) >= 4:
            return True

    return False

def process_dual_path_scan(notify=False, quick_mode=False):
    universe = get_quick_scan_universe() if quick_mode else WATCHLIST
    prev_active = dict(state.get("active_candidates", {}))

    result = scan_dual_path(universe=universe, quick_mode=quick_mode)
    sync_active_candidates_from_combined(result["combined"])
    refresh_quick_pool(result)

    digest = dual_scan_hash(result)
    old_digest = state.get("last_dual_scan_hash", "")

    notify_needed = should_notify_quick_update(
        prev_active=prev_active,
        new_combined=result["combined"],
        old_digest=old_digest,
        new_digest=digest,
        quick_mode=quick_mode
    )

    if notify and chat_id_global and notify_needed:
        prefix = "AUTOSCAN CEPAT\n\n" if quick_mode else ""
        msg = prefix + build_dual_path_text(result) + "\n\n" + build_status_text()
        send_message(chat_id_global, msg)
        state["last_dual_scan_hash"] = digest
        save_state()

    return result

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
        lines.append(f"{item['symbol']} | {item.get('decision_status','-')} | rank {item.get('rank','-')} | score {item.get('score','-')}")
    return "\n".join(lines)

def build_unsupported_text():
    if not unsupported_symbols:
        return "Belum ada symbol unsupported / delisting yang terdeteksi."
    items = sorted(list(unsupported_symbols))
    lines = ["SYMBOL UNSUPPORTED / DELISTING", ""]
    for s in items[:100]:
        lines.append(f"- {s}")
    if len(items) > 100:
        lines += ["", f"... dan {len(items)-100} symbol lain"]
    return "\n".join(lines)

def is_market_open():
    now = datetime.now()
    if now.weekday() >= 5:
        return False
    current = now.strftime("%H:%M")
    return ("09:00" <= current <= "12:00") or ("13:30" <= current <= "15:15")

def should_run_scan():
    now = datetime.now()
    if now.minute % SCAN_INTERVAL_MINUTES != 0:
        return None
    return now.strftime("%Y-%m-%d %H:%M")

def try_autoscan():
    global state
    if not state.get("autoscan"):
        return
    if not is_market_open():
        return
    if not chat_id_global:
        return

    minute_key = should_run_scan()
    if minute_key is None:
        return
    if state.get("last_scan_minute_key") == minute_key:
        return

    pool = get_quick_scan_universe()
    if not pool:
        seed = list(state.get("active_candidates", {}).keys())
        if not seed:
            seed = WATCHLIST[:min(QUICK_POOL_MAX, 40)]
        save_quick_pool(seed)

    process_dual_path_scan(notify=True, quick_mode=True)
    state["last_scan_minute_key"] = minute_key
    save_state()

def handle_command(chat_id, text):
    global chat_id_global, WATCHLIST
    raw = text.strip()
    cmd = raw.lower()

    if cmd == "/start":
        send_message(chat_id, "Entry Bot QUICK AUTOSCAN + FULL MANUAL FIX 3 aktif.\n\nCommand:\n/scan\n/scanjalur\n/statuskandidat\n/watchlist\n/autoscanon\n/autoscanoff\n/statusauto\n/listskips\n/reloadwatchlist\n/journaltoday\n/journalsummary\n/journalstock KODE")
        return
    if cmd == "/watchlist":
        send_message(chat_id, build_watchlist_text())
        return
    if cmd in ["/scan", "/scanjalur"]:
        if not WATCHLIST:
            send_message(chat_id, "Watchlist kosong. Isi watchlist_syariah.txt lalu /reloadwatchlist.")
            return
        result = process_dual_path_scan(notify=False, quick_mode=False)
        send_message(chat_id, build_dual_path_text(result) + "\n\n" + build_status_text())
        return
    if cmd == "/autoscanon":
        state["autoscan"] = True
        save_state()

        pool = get_quick_scan_universe()
        if len(pool) < 10:
            seed = list(state.get("active_candidates", {}).keys())
            if len(seed) < 10:
                seed.extend(WATCHLIST[:min(40, len(WATCHLIST))])
            save_quick_pool(seed)
            pool = get_quick_scan_universe()

        send_message(chat_id, f"Autoscan cepat diaktifkan. Scan tiap 5 menit memakai quick pool prioritas.\nQuick pool awal: {len(pool)} saham\nWatchlist aktif: {len(WATCHLIST)} saham")
        return
    if cmd == "/autoscanoff":
        state["autoscan"] = False
        save_state()
        send_message(chat_id, "Autoscan dimatikan.")
        return
    if cmd == "/statusauto":
        pool = get_quick_scan_universe()
        send_message(chat_id, f"Status autoscan: {'ON' if state.get('autoscan') else 'OFF'}\nQuick pool: {len(pool)} saham\nWatchlist aktif: {len(WATCHLIST)} saham")
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
    if cmd == "/journaltoday":
        evaluate_pending_signals()
        send_message(chat_id, build_journal_today_text())
        return
    if cmd == "/journalsummary":
        evaluate_pending_signals()
        send_message(chat_id, build_journal_summary_text())
        return
    if cmd.startswith("/journalstock"):
        parts = raw.split()
        send_message(chat_id, build_journal_stock_text(parts[1]) if len(parts) >= 2 else "Gunakan format: /journalstock KODE")
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
        evaluate_pending_signals()
        time.sleep(5)
    except Exception as e:
        print("Error:", e)
        time.sleep(5)