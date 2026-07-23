"""
Screening harian saham IDX — VERSI FINAL hasil kalibrasi backtest 2 tahun.

Setiap sinyal punya status:
  "aktif"  : lolos backtest, layak dipertimbangkan sebagai sinyal trade
  "pantau" : TIDAK lolos backtest — ditampilkan hanya untuk observasi

Hasil riset (backtest 2 tahun, 20 emiten, model konservatif sadar-gap):
  AKTIF:
  - Swing BREAKOUT  : PF 1.69, +1.73%/trade (target utama TP2 = 3R)
  - BSJP            : PF 3.09, +0.37%/trade (ambang optimal cp>=0.6, vol>=1.2)
                      * butuh broker fee rendah (<0.30% round trip)
  PANTAU (gagal backtest, jangan ditrade):
  - Demand Zone Bounce : PF 0.79  (v2 dengan konfirmasi: PF 0.54)
  - BOS Konfirmasi     : PF 0.74-0.77
  - BPJS               : PF 0.59  (v2 ORB intraday: avg di bawah fee)
  - Konsolidasi        : bukan sinyal, sekadar pemantauan

Sesi (otomatis dari jam WIB):
  pagi  (<12:00)      : Swing + BPJS(pantau)
  siang (12:00-15:00) : Swing
  sore  (>15:00)      : Swing + BSJP
    * Dijadwalkan ±15:30-15:45 WIB (SEBELUM tutup 15:50) agar sinyal BSJP
      masih bisa dieksekusi. Konsekuensi: candle hari itu belum 100% final —
      volume sedikit undercounted dan close bisa bergeser di menit akhir.

Cara pakai:
    python idx_screening.py            # sesi otomatis
    python idx_screening.py --sesi sore

Output: signals.json (dibaca dashboard)
"""

import argparse
import json
from datetime import datetime, timezone, timedelta

import numpy as np
import pandas as pd
import yfinance as yf

# ---------------------------------------------------------------------------
# 1. KONFIGURASI
# ---------------------------------------------------------------------------

WATCHLIST = [
    "BBCA", "BBRI", "BMRI", "BBNI", "TLKM", "ASII", "ANTM", "ICBP",
    "UNVR", "INDF", "PGAS", "SMGR", "KLBF", "INCO", "PTBA", "ADRO",
    "MDKA", "AMRT", "CPIN", "EXCL",
]

LOOKBACK_DAYS = 150
SWING_WINDOW = 5
VOLUME_AVG_WINDOW = 20

# Ambang BSJP — hasil sweep backtest (PF 3.09 di kombinasi ini)
BSJP_CLOSE_POS_MIN = 0.6
BSJP_VOL_RATIO_MIN = 1.2

WIB = timezone(timedelta(hours=7))


def detect_session(forced: str | None = None) -> str:
    if forced in ("pagi", "siang", "sore"):
        return forced
    hour = datetime.now(WIB).hour
    if hour < 12:
        return "pagi"
    if hour < 15:
        return "siang"
    return "sore"


# ---------------------------------------------------------------------------
# 2. DATA & UTIL
# ---------------------------------------------------------------------------

def fetch_price_history(ticker: str) -> pd.DataFrame:
    df = yf.Ticker(f"{ticker}.JK").history(
        period=f"{LOOKBACK_DAYS}d", interval="1d"
    )
    return df.dropna()


def find_swings(df: pd.DataFrame, window: int = SWING_WINDOW):
    highs, lows = [], []
    h, l = df["High"].values, df["Low"].values
    for i in range(window, len(df) - window):
        if h[i] == max(h[i - window:i + window + 1]):
            highs.append((i, h[i]))
        if l[i] == min(l[i - window:i + window + 1]):
            lows.append((i, l[i]))
    return highs, lows


def base_metrics(df: pd.DataFrame) -> dict:
    close, volume = df["Close"], df["Volume"]
    last = df.iloc[-1]
    prev = df.iloc[-2]

    ma20 = close.rolling(20).mean().iloc[-1]
    ma50 = close.rolling(50).mean().iloc[-1] if len(df) >= 50 else ma20
    vol_avg = volume.rolling(VOLUME_AVG_WINDOW).mean().iloc[-1]
    vol_ratio = float(last["Volume"] / vol_avg) if vol_avg else 1.0

    day_range = last["High"] - last["Low"]
    close_position = (
        (last["Close"] - last["Low"]) / day_range if day_range > 0 else 0.5
    )

    return {
        "price": float(last["Close"]),
        "open": float(last["Open"]),
        "high": float(last["High"]),
        "low": float(last["Low"]),
        "prev_close": float(prev["Close"]),
        "prev_high": float(prev["High"]),
        "prev_bullish": bool(prev["Close"] > prev["Open"]),
        "ma20": float(ma20),
        "ma50": float(ma50),
        "vol_ratio": vol_ratio,
        "close_position": float(close_position),
    }


# ---------------------------------------------------------------------------
# 3. STRATEGI SWING
#    Hanya BREAKOUT yang berstatus "aktif" (lolos backtest: PF 1.69 di TP2).
#    Setup lain tetap dihitung tapi berstatus "pantau".
# ---------------------------------------------------------------------------

def swing_strategy(df: pd.DataFrame, m: dict):
    highs, lows = find_swings(df)
    last_swing_high = highs[-1][1] if highs else df["Close"].max()
    last_swing_low = lows[-1][1] if lows else df["Close"].min()
    price = m["price"]

    trend_score = 0
    trend_score += 15 if price > m["ma20"] else 0
    trend_score += 15 if price > m["ma50"] else 0
    trend_score += 10 if m["ma20"] > m["ma50"] else 0

    structure_score = 0
    broke_resistance = price > last_swing_high * 0.999
    # Harga harus DI ATAS swing low (toleransi 0.99x) — kalau sudah jatuh
    # di bawahnya, demand zone-nya jebol, bukan bounce.
    near_demand = last_swing_low * 0.99 <= price <= last_swing_low * 1.03
    if broke_resistance:
        structure_score += 30
    if near_demand:
        structure_score += 25

    volume_score = min(30, max(0, (m["vol_ratio"] - 1) * 30))
    technical_score = int(min(100, trend_score + structure_score + volume_score))

    if broke_resistance and m["vol_ratio"] > 1.2:
        setup, sl, status = "Breakout", last_swing_high * 0.985, "aktif"
    elif near_demand:
        setup, sl, status = "Demand Zone Bounce", last_swing_low * 0.98, "pantau"
    elif price > m["ma20"] and m["ma20"] > m["ma50"]:
        setup, sl, status = "BOS Konfirmasi", min(last_swing_low, m["ma50"]) * 0.99, "pantau"
    else:
        setup, sl, status = "Konsolidasi", price * 0.95, "pantau"

    entry = price
    if sl >= entry:
        # Geometri tidak valid (SL di atas entry) — turunkan jadi Konsolidasi
        setup, sl, status = "Konsolidasi", price * 0.95, "pantau"
    risk = max(entry - sl, entry * 0.01)
    return {
        "strategy": "Swing",
        "setup": setup,
        "status": status,
        "entry": round(entry),
        "sl": round(sl),
        "tp1": round(entry + 2 * risk),
        "tp2": round(entry + 3 * risk),
        "technicalScore": technical_score,
    }


# ---------------------------------------------------------------------------
# 4. STRATEGI BSJP — Beli Sore Jual Pagi (sesi SORE) — AKTIF
#    Ambang hasil sweep backtest: close_position >= 0.6, volume >= 1.2x.
#    PF 3.09, +0.37%/trade. Perhatikan fee broker: butuh <0.30% round trip.
# ---------------------------------------------------------------------------

def bsjp_strategy(m: dict):
    price = m["price"]
    strong_close = m["close_position"] >= BSJP_CLOSE_POS_MIN
    green_day = price > m["open"]
    up_vs_yesterday = price > m["prev_close"]

    if not (strong_close and green_day):
        return None

    score = 0
    score += 35 if strong_close else 0
    score += 15 if green_day else 0
    score += 10 if up_vs_yesterday else 0
    if m["vol_ratio"] >= 1.5:
        score += 25
    elif m["vol_ratio"] >= BSJP_VOL_RATIO_MIN:
        score += 15
    if price > m["ma20"]:
        score += 15

    if m["vol_ratio"] >= BSJP_VOL_RATIO_MIN:
        setup, status = "Strong Close + Volume", "aktif"
    else:
        # Tanpa volume: statistik backtest lebih lemah — pantau saja
        setup, status = "Strong Close", "pantau"

    entry = price
    return {
        "strategy": "BSJP",
        "setup": setup,
        "status": status,
        "entry": round(entry),
        "sl": round(entry * 0.98),
        "tp1": round(entry * 1.015),
        "tp2": round(entry * 1.03),
        "technicalScore": int(min(100, score)),
    }


# ---------------------------------------------------------------------------
# 5. STRATEGI BPJS — Beli Pagi Jual Sore (sesi pagi) — PANTAU SAJA
#    Gagal backtest 2x (gap-follow PF 0.59; ORB intraday avg < fee).
#    Tetap dihitung untuk observasi, JANGAN ditrade.
# ---------------------------------------------------------------------------

def bpjs_strategy(m: dict):
    price, prev_close = m["price"], m["prev_close"]
    gap_pct = (m["open"] - prev_close) / prev_close * 100

    healthy_gap = 0.5 <= gap_pct <= 3.0
    broke_prev_high = price > m["prev_high"]
    if not (healthy_gap or broke_prev_high):
        return None

    score = 0
    if healthy_gap:
        score += 35
    if broke_prev_high:
        score += 30
    if m["prev_bullish"]:
        score += 15
    if price > m["ma20"]:
        score += 10
    if m["vol_ratio"] > 1.0:
        score += 10

    if healthy_gap and broke_prev_high:
        setup = "Gap Up + Breakout"
    elif healthy_gap:
        setup = "Gap Up Lanjutan"
    else:
        setup = "Breakout High Kemarin"

    entry = price
    sl = min(m["open"], entry * 0.985)
    return {
        "strategy": "BPJS",
        "setup": setup,
        "status": "pantau",
        "entry": round(entry),
        "sl": round(sl),
        "tp1": round(entry * 1.02),
        "tp2": round(entry * 1.035),
        "technicalScore": int(min(100, score)),
    }


# ---------------------------------------------------------------------------
# 6. FUNDAMENTAL (pelengkap skor untuk Swing)
# ---------------------------------------------------------------------------

def fundamental_analysis(ticker: str):
    info = yf.Ticker(f"{ticker}.JK").info
    per = info.get("trailingPE")
    pbv = info.get("priceToBook")
    roe = info.get("returnOnEquity")
    div_yield = info.get("dividendYield")

    score = 0
    if per is not None:
        score += 25 if per < 10 else 15 if per < 18 else 5 if per < 25 else 0
    if pbv is not None:
        score += 25 if pbv < 1.5 else 15 if pbv < 3 else 5 if pbv < 5 else 0
    if roe is not None:
        score += 30 if roe > 0.15 else 15 if roe > 0.08 else 0
    if div_yield is not None:
        score += 20 if div_yield > 0.04 else 10 if div_yield > 0.02 else 0

    return {
        "fundamentalScore": int(min(100, score)),
        "sector": info.get("sector", "Lainnya"),
        "name": info.get("longName", ticker),
        "per": per,
        "roe": roe,
    }


def build_notes(sig: dict, fund: dict, m: dict) -> str:
    parts = []
    if sig["strategy"] == "BPJS":
        parts.append(
            f"{sig['setup']} — PANTAU SAJA: strategi ini gagal backtest "
            "(PF 0.59; versi ORB pun tidak melampaui fee). Untuk observasi."
        )
    elif sig["strategy"] == "BSJP":
        if sig["status"] == "aktif":
            parts.append(
                f"{sig['setup']} — tutup di area atas rentang harian "
                f"(posisi close {m['close_position']*100:.0f}%) dengan volume. "
                "Target jual di pembukaan besok pagi. "
                "Backtest: PF 3.09, +0.37%/trade (sebelum fee)."
            )
        else:
            parts.append(
                f"{sig['setup']} — strong close TANPA volume di atas rata-rata. "
                "Statistik lebih lemah, pantau saja."
            )
    else:  # Swing
        if sig["setup"] == "Breakout":
            parts.append(
                "Breakout swing high dengan volume. Backtest: PF 1.69, "
                "+1.73%/trade — TARGET UTAMA TP2 (3R), bukan TP1."
            )
        elif sig["setup"] == "Demand Zone Bounce":
            parts.append(
                "PANTAU SAJA: setup ini gagal backtest (PF 0.79; "
                "versi dengan konfirmasi pun PF 0.54). Untuk observasi."
            )
        elif sig["setup"] == "BOS Konfirmasi":
            parts.append(
                "PANTAU SAJA: setup ini gagal backtest (PF 0.74-0.77, "
                "rata-rata hold 16 hari untuk hasil negatif)."
            )
        else:
            parts.append("Belum ada konfirmasi arah — pemantauan kondisi.")
        if fund.get("per") is not None:
            parts.append(f"PER {fund['per']:.1f}x.")
        if fund.get("roe") is not None:
            parts.append(f"ROE {fund['roe']*100:.1f}%.")
    return " ".join(parts)


# ---------------------------------------------------------------------------
# 7. MAIN
# ---------------------------------------------------------------------------

def run_screening(session: str):
    print(f"Sesi: {session.upper()} ({datetime.now(WIB):%H:%M} WIB)\n")
    results = []

    for ticker in WATCHLIST:
        try:
            df = fetch_price_history(ticker)
            if len(df) < 30:
                print(f"[skip] {ticker}: data historis kurang")
                continue

            m = base_metrics(df)
            fund = fundamental_analysis(ticker)

            candidates = [swing_strategy(df, m)]
            if session == "pagi":
                candidates.append(bpjs_strategy(m))
            elif session == "sore":
                candidates.append(bsjp_strategy(m))

            for sig in candidates:
                if sig is None:
                    continue
                results.append({
                    "ticker": ticker,
                    "name": fund["name"],
                    "sector": fund["sector"],
                    "price": round(m["price"]),
                    "entry": sig["entry"],
                    "sl": sig["sl"],
                    "tp1": sig["tp1"],
                    "tp2": sig["tp2"],
                    "technicalScore": sig["technicalScore"],
                    "fundamentalScore": fund["fundamentalScore"],
                    "strategy": sig["strategy"],
                    "setup": sig["setup"],
                    "status": sig["status"],
                    "notes": build_notes(sig, fund, m),
                })

            print(f"[ok] {ticker}")

        except Exception as e:
            print(f"[error] {ticker}: {e}")

    # Sinyal aktif di atas, lalu urut skor gabungan
    results.sort(
        key=lambda s: (
            0 if s["status"] == "aktif" else 1,
            -(s["technicalScore"] + s["fundamentalScore"]) / 2,
        )
    )

    output = {
        "generatedAt": datetime.now(timezone.utc).isoformat(),
        "session": session,
        "signals": results,
    }
    with open("signals.json", "w", encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False, indent=2)

    n_aktif = sum(1 for r in results if r["status"] == "aktif")
    print(f"\nSelesai. {len(results)} sinyal ({n_aktif} aktif, "
          f"{len(results) - n_aktif} pantau) disimpan ke signals.json")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--sesi", choices=["pagi", "siang", "sore"], default=None)
    args = parser.parse_args()
    run_screening(detect_session(args.sesi))
