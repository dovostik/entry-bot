# PATCH V15 - suppress Yahoo ticker errors and skip unsupported symbols
# Copy-paste these changes into main.py

# 1) Tambahkan import ini di bagian atas
import io
from contextlib import redirect_stdout, redirect_stderr

# 2) Tambahkan file cache ini dekat konstanta lain
BAD_SYMBOLS_FILE = "bad_symbols.json"

# 3) Tambahkan variable global ini
bad_symbols = set()

# 4) Tambahkan helper ini
def load_bad_symbols():
    global bad_symbols
    if os.path.exists(BAD_SYMBOLS_FILE):
        try:
            with open(BAD_SYMBOLS_FILE, "r", encoding="utf-8") as f:
                items = json.load(f)
                bad_symbols = set(items)
        except Exception:
            bad_symbols = set()

def save_bad_symbols():
    with open(BAD_SYMBOLS_FILE, "w", encoding="utf-8") as f:
        json.dump(sorted(list(bad_symbols)), f)

def safe_history(ticker_obj, period="1y", interval="1d"):
    try:
        buf_out = io.StringIO()
        buf_err = io.StringIO()
        with redirect_stdout(buf_out), redirect_stderr(buf_err):
            df = ticker_obj.history(period=period, interval=interval)
        return df
    except Exception:
        return None

# 5) Di fungsi get_market_snapshot(symbol), ganti bagian awalnya jadi ini
def get_market_snapshot(symbol):
    try:
        symbol = symbol.upper().strip()

        # skip kalau sudah pernah terbukti tidak didukung Yahoo
        if symbol in bad_symbols:
            return None

        ticker = yf.Ticker(yahoo_symbol(symbol))
        hist = safe_history(ticker, period="1y", interval="1d")

        # kalau kosong / tidak ada data, blacklist supaya scan berikutnya tidak ribut lagi
        if hist is None or hist.empty or len(hist) < 210:
            bad_symbols.add(symbol)
            save_bad_symbols()
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
        fake_breakout, fake_reason, breakout_attempt = detect_fake_breakout(close, high, low, open_price, recent_high, change_pct)
        timing, timing_reason = timing_label(close, low, high)
        volume_label, volume_score = classify_volume(value_traded, valavg5)
        base_low, base_high, is_sideway = get_base_zone(hist)

        liquid_ok = value_traded >= MIN_VALUE_TRADED and daily_range_pct >= MIN_DAILY_RANGE_PCT
        if not liquid_ok:
            return None

        setup = detect_setup(close, ma20, ma50, base_low, base_high, value_traded, valavg5, breakout_attempt, fake_breakout)
        if setup == "WATCH ONLY":
            return None
        if volume_score < 0:
            return None
        if rsi > 78 and setup != "VALID BREAKOUT EXECUTE":
            return None
        if timing == "LATE":
            return None

        bid_low, bid_high, zone_type = build_entry_zone(setup, close, ma20, ma50, base_low, base_high)
        trigger = round((base_high if base_high else close) * 1.003, 2)
        invalidation = round((bid_low if bid_low else close) * 0.992, 2)

        distance_to_bid_pct = ((close - bid_high) / close) * 100 if close > bid_high else 0
        if distance_to_bid_pct > MAX_DISTANCE_TO_BID_PCT and setup != "VALID BREAKOUT EXECUTE":
            return None

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
        elif setup == "VALID BREAKOUT EXECUTE":
            structure += 10
            reasons.append("breakout valid")

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

        risk_pct = ((close - invalidation) / close) * 100 if close else 0
        reward_pct = ((trigger - close) / close) * 100 if close else 0
        if reward_pct <= 0 or reward_pct < risk_pct:
            penalty += 12

        if setup == "VALID BREAKOUT EXECUTE" and volume_score > 0:
            confirmation += 16
        if change_pct > 1:
            confirmation += 3

        if fake_breakout:
            penalty += 24
        if rsi > 72:
            penalty += 12
        if close > trigger * 1.01 and setup != "VALID BREAKOUT EXECUTE":
            penalty += 20

        score = int(round(0.25 * (trend * 4) + 0.25 * structure + 0.25 * execution + 0.15 * confirmation + 0.10 * max(volume_score, 0) - 0.30 * penalty + 50))
        tp1 = round(close * 1.01, 2)
        tp2 = round(close * 1.02, 2)
        v_status, v_reason = validation_status(close, bid_low, bid_high, trigger, invalidation, fake_breakout, setup, volume_score)
        if v_status == "INVALID":
            return None

        confidence = "HIGH" if score >= 85 else "MEDIUM" if score >= 70 else "LOW"

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
            "reason": ", ".join(reasons[:2]) if reasons else "belum ada alasan kuat",
            "tech_summary": ", ".join(tech_notes[:4]),
            "confidence": confidence
        }
    except Exception:
        bad_symbols.add(symbol.upper().strip())
        save_bad_symbols()
        return None

# 6) Setelah load_state(), tambahkan ini
load_bad_symbols()

# 7) OPTIONAL: hapus BBMI dan SPOT dari watchlist_syariah.txt kalau masih ada
#    karena dari log, Yahoo tidak punya data untuk dua kode itu.
