# FIX SIMPLE V15 (ANTI ERROR YAHOO)

# LANGKAH:
# 1. Buka main.py
# 2. Cari fungsi: get_market_snapshot
# 3. GANTI bagian awalnya dengan ini (COPY PASTE SAJA)

def get_market_snapshot(symbol):
    try:
        ticker = yf.Ticker(yahoo_symbol(symbol))
        hist = ticker.history(period="1y", interval="1d")
    except:
        return None

    if hist is None or hist.empty:
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
        else:
            trend -= 6
        if ma20 > ma50:
            trend += 5
        else:
            trend -= 4
        if close > ma100:
            trend += 4
        if close > ma200:
            trend += 4
        if rsi > 55:
            trend += 4
        elif rsi < 45:
            trend -= 6
        if macd > signal:
            trend += 4
        else:
            trend -= 2

        if setup == "SIDEWAY ACCUMULATION PREPARE":
            structure += 22
        elif setup == "SUPPORT BOUNCE PREPARE":
            structure += 20
        elif setup == "VALID BREAKOUT EXECUTE":
            structure += 10

        if is_sideway:
            structure += 6
        if abs(close - ma20) / close < 0.02:
            structure += 6
        if abs(close - ma50) / close < 0.03:
            structure += 4
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

        score = int(round(
            0.25 * (trend * 4) +
            0.25 * structure +
            0.25 * execution +
            0.15 * confirmation -
            0.30 * penalty + 50
        ))

        v_status, _ = validation_status(close, bid_low, bid_high, trigger, invalidation, fake_breakout, setup, volume_score)
        if v_status == "INVALID":
            return None

        return {
            "symbol": symbol.upper(),
            "score": score,
            "setup": setup,
            "close": round(close, 2),
            "change_pct": round(change_pct, 2),
            "status": v_status,
            "bid_low": round(bid_low, 2),
            "bid_high": round(bid_high, 2),
            "trigger": trigger,
            "invalidation": invalidation
        }

    except:
        return None


# TAMBAHAN:
# Hapus dari watchlist:
# BBMI
# SPOT

# Lalu di Telegram:
# /reloadwatchlist
