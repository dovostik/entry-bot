V16.1 FINAL LOGIC - CONTEXT AWARE ENTRY BOT

TUJUAN
- Bot hanya memberi: NO TRADE, WAIT, ACTIVE BID, WAIT RETEST, BREAKOUT EXECUTE
- Tidak lagi salah baca downtrend sebagai accumulation
- Tidak lagi mengejar breakout yang sudah terlalu jauh

A. KLASIFIKASI TREND
1. BULLISH TREND
   - close > MA20
   - MA20 > MA50
   - close >= MA50
   - RSI >= 50

2. NEUTRAL
   - close di sekitar MA20/MA50
   - trend belum jelas

3. BEARISH TREND
   - close < MA20 dan MA20 < MA50
   atau
   - close < MA50 dan MA50 < MA100

RULE:
- BEARISH TREND -> NO TRADE untuk accumulation / bounce
- NEUTRAL -> maksimal WAIT
- BULLISH -> boleh lanjut cek setup

B. SETUP VALID
1. SIDEWAY ACCUMULATION PREPARE
   hanya jika:
   - is_sideway = True
   - dekat bawah range
   - close >= MA20 * 0.995
   - RSI >= 45
   - MACD histogram tidak terlalu negatif
   - bukan bearish trend
   - bukan dead market
   - bukan post-drop sideway

2. SUPPORT BOUNCE PREPARE
   hanya jika:
   - dekat support / bawah range
   - close >= support
   - close >= MA20 * 0.99
   - RSI >= 45
   - MACD mulai membaik
   - trend tidak bearish

3. VALID_BREAKOUT_EXECUTE
   hanya jika:
   - breakout_attempt = True
   - fake_breakout = False
   - volume_score > 0
   - move_from_base_pct <= 2

4. BREAKOUT RETEST READY
   hanya jika:
   - breakout_attempt = True
   - fake_breakout = False
   - volume_score > 0
   - move_from_base_pct > 2 dan <= 5

5. OVEREXTENDED
   jika:
   - breakout_attempt = True
   - move_from_base_pct > 5
   -> NO TRADE / WAIT RETEST ONLY

C. KONDISI GUGUR
1. DEAD MARKET
   - abs(change_pct) < 1
   - value_traded tidak naik berarti
   - volume_score <= 0

2. BAD SIDEWAY
   - is_sideway = True
   - close < MA20 dan MA20 < MA50
   atau
   - close < MA50 dan MA50 < MA100

3. POST-DROP SIDEWAY
   - recent_drop_pct > 8
   - is_sideway = True

4. LATE PULLBACK
   - timing = LATE
   untuk accumulation / bounce

5. CHASE ENTRY
   - range_position > 0.60
   untuk accumulation / bounce

6. WEAK VOLUME
   - volume_score < 0
   kecuali bounce support yang sangat jelas

7. FAKE BREAKOUT
   - fake_breakout = True

8. OVERBOUGHT NON-BREAKOUT
   - RSI > 78
   tapi bukan breakout valid

D. STATUS FINAL
1. NO TRADE
   - bearish trend
   - bad sideway
   - dead market
   - post-drop sideway
   - overextended
   - fake breakout

2. WAIT
   - setup ada, tapi harga belum di area ideal
   - trend netral
   - range_position terlalu tinggi
   - volume belum meyakinkan

3. ACTIVE BID
   - setup accumulation / bounce valid
   - close di bid zone
   - range_position <= 0.60
   - trend bukan bearish
   - reward > risk

4. WAIT RETEST
   - breakout valid
   - tapi harga sudah jalan
   - tunggu kembali ke area retest

5. BREAKOUT EXECUTE
   - breakout valid
   - belum terlalu jauh
   - volume kuat
   - bukan fake breakout
   - bisa eksekusi agresif

E. RANGE POSITION
Gunakan:
range_position = (close - support) / (resistance - support)

Interpretasi:
- 0.00 - 0.40 = area bawah range -> bagus untuk bid
- 0.40 - 0.60 = tengah -> masih tunggu
- > 0.60 = atas range -> jangan bid untuk setup sideway

F. LOGIC BREAKOUT
- breakout + volume + belum jauh -> BREAKOUT EXECUTE
- breakout + volume + sudah jalan 2% s/d 5% -> WAIT RETEST
- breakout > 5% dari base -> NO TRADE / OVEREXTENDED
- breakout close lemah / upper wick besar -> FAKE BREAKOUT -> gugur

G. CONFIDENCE
HIGH
- bullish trend
- volume kuat
- RSI > 55
- MACD histogram positif
- setup jelas
- posisi entry bagus

MEDIUM
- sebagian besar valid
- ada 1 risiko kecil
- misalnya breakout retest

LOW
- trend belum mendukung
- volume biasa
- setup kurang bersih
- hanya boleh WAIT, bukan ACTIVE BID

H. HASIL AUDIT SAHAM
- PGEO: false accumulation dalam downtrend -> harus gugur
- DOOH: breakout sudah lari -> jangan dikejar
- CPRO: cukup benar -> breakout / retest perlu label lebih spesifik
- MYOR: dead market -> gugur
- TLKM: post-drop weak sideway -> NO TRADE
- KLBF: downtrend continuation -> NO TRADE
- CBRE: sudah benar -> WAIT RETEST

I. TARGET HASIL
- KLBF / TLKM / MYOR harus gugur
- CBRE harus WAIT RETEST
- CPRO bisa valid kalau retest sehat
- DOOH tidak boleh dikejar
- ACTIVE BID hanya muncul di area benar-benar layak