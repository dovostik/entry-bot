DASHBOARD EVALUASI PERFORMA STATUS BOT
=====================================

TUJUAN
------
Dashboard ini dipakai untuk membaca performa bot berdasarkan STATUS sinyal,
bukan hanya berdasarkan nama saham.

Dengan ini kita bisa jawab pertanyaan penting:
- ACTIVE BID EARLY sering berhasil atau tidak?
- WAIT RETEST benar-benar memberi entry bagus atau malah terlalu konservatif?
- WATCH DEEP PULLBACK sering naik jadi valid atau justru buang waktu?
- MOMENTUM CONTINUATION terlalu telat atau masih layak?

STATUS YANG DIEVALUASI
----------------------
1. ACTIVE BID
2. ACTIVE BID EARLY
3. ACTIVE BID PULLBACK
4. WAIT RETEST
5. WATCH WAIT
6. WATCH DEEP PULLBACK
7. MOMENTUM CONTINUATION
8. OVEREXTENDED MOMENTUM

METRIK UTAMA PER STATUS
-----------------------
A. Jumlah sinyal
- total berapa kali status itu muncul

B. Hasil 15m / 30m / 60m
- BENAR
- GAGAL
- NETRAL
- SALAH

C. Win rate
- BENAR / total evaluasi yang valid

D. Average follow-through
- rata-rata % perubahan setelah 15m
- rata-rata % perubahan setelah 30m
- rata-rata % perubahan setelah 60m

E. Failure rate
- seberapa sering status itu gagal

F. Upgrade rate
- khusus status WATCH:
  - berapa kali naik menjadi status actionable

G. Retest success rate
- khusus WAIT RETEST:
  - berapa kali harga benar-benar masuk area bid lalu mantul

H. Pullback activation rate
- khusus WATCH DEEP PULLBACK:
  - berapa kali berubah menjadi ACTIVE BID PULLBACK

OUTPUT YANG DIINGINKAN
----------------------

1) COMMAND: /dashboardstatus

Contoh output:

DASHBOARD STATUS BOT

ACTIVE BID EARLY
- Total sinyal: 24
- BENAR 15m: 15
- GAGAL 15m: 4
- NETRAL 15m: 5
- Win rate 15m: 62.5%
- Avg move 15m: +0.84%
- Avg move 30m: +1.21%
- Avg move 60m: +1.76%

WAIT RETEST
- Total sinyal: 18
- BENAR 30m: 7
- GAGAL 30m: 3
- NETRAL 30m: 8
- Retest success rate: 38.9%
- Avg move 60m: +0.62%

WATCH DEEP PULLBACK
- Total sinyal: 12
- Upgrade jadi ACTIVE BID PULLBACK: 4
- Activation rate: 33.3%
- Avg move 60m: +0.48%

2) COMMAND: /dashboardringkas

Contoh output:

DASHBOARD RINGKAS

Status terbaik:
1. ACTIVE BID PULLBACK -> win rate 71.4%
2. ACTIVE BID EARLY -> win rate 62.5%
3. WAIT RETEST -> win rate 55.6%

Status terlemah:
1. OVEREXTENDED MOMENTUM -> win rate 18.2%
2. WATCH WAIT -> win rate 22.0%

3) COMMAND: /dashboardstatus ACTIVE BID EARLY

Contoh output:

DASHBOARD ACTIVE BID EARLY

Total sinyal: 24
15m:
- BENAR: 15
- GAGAL: 4
- NETRAL: 5
- Avg move: +0.84%

30m:
- BENAR: 17
- GAGAL: 4
- NETRAL: 3
- Avg move: +1.21%

60m:
- BENAR: 18
- GAGAL: 5
- NETRAL: 1
- Avg move: +1.76%

Win rate final: 62.5%

ATURAN PERHITUNGAN
------------------

A. ACTIVE BID / ACTIVE BID EARLY / ACTIVE BID PULLBACK / MOMENTUM CONTINUATION
- BENAR jika:
  - evaluasi 15m/30m/60m = BENAR
- Win rate dihitung dari:
  - jumlah BENAR / total (BENAR + GAGAL + NETRAL)

B. WAIT RETEST
- Fokus:
  - apakah retest benar-benar terjadi
  - apakah setelah retest harga mantul
- Bisa dibuat 2 metrik:
  - retest occurrence rate
  - retest success rate

C. WATCH WAIT
- Fokus:
  - apakah keputusan menunggu ternyata tepat
- Kalau status ini sering SALAH,
  artinya bot terlalu takut

D. WATCH DEEP PULLBACK
- Fokus:
  - apakah status ini sering berubah jadi entry valid
- Kalau activation rate tinggi,
  artinya jalur pullback sudah sehat

E. OVEREXTENDED MOMENTUM
- Fokus:
  - apakah bot benar menahan diri
- Kalau win rate status ini justru tinggi,
  filter panas mungkin terlalu keras

DATA YANG DIPERLUKAN
--------------------
Semua data sebenarnya sudah dekat dengan struktur jurnal yang ada.

Yang perlu ditambahkan atau dipastikan:
- status saat sinyal dibuat
- hasil evaluasi 15m/30m/60m
- pct perubahan
- setup
- apakah status berubah kemudian
- apakah kandidat naik jadi status actionable

MANFAAT PRAKTIS
---------------
1. Bot bisa di-tuning pakai bukti
2. Tidak perlu menebak-nebak apakah status tertentu bagus
3. Bisa ketahuan mana status yang layak dipertahankan
4. Bisa ketahuan mana status yang harus diperketat atau dilonggarkan

CONTOH KEPUTUSAN DARI DASHBOARD
-------------------------------
- Jika ACTIVE BID EARLY win rate tinggi:
  -> pertahankan breakout early

- Jika WAIT RETEST terlalu banyak NETRAL:
  -> mungkin terlalu konservatif

- Jika WATCH DEEP PULLBACK sering upgrade:
  -> jalur pullback sudah efektif

- Jika MOMENTUM CONTINUATION banyak gagal:
  -> filter panas perlu diperketat lagi

REKOMENDASI IMPLEMENTASI
------------------------
Tahap 1:
- buat command /dashboardstatus
- agregasi sederhana berdasarkan signal_evaluations.json

Tahap 2:
- buat command /dashboardringkas
- tampilkan top status terbaik & terburuk

Tahap 3:
- buat command /dashboardstatus NAMA_STATUS
- drill down per status

KESIMPULAN
----------
Setelah ranking dan trigger makin sehat, langkah paling penting berikutnya memang bukan menambah setup baru,
melainkan mengukur status-status yang sudah ada.

Dashboard ini akan jadi panel kontrol utama untuk menilai apakah:
- breakout early benar-benar bekerja
- retest terlalu lambat atau justru presisi
- pullback yang baru kamu bangun sudah efektif atau belum