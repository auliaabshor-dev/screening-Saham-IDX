"""
Screening harian saham IDX — 3 sesi (pagi/siang/sore) dengan 3 strategi:

  - SWING : posisi beberapa hari, teknikal + fundamental (logika lama)
  - BPJS  : Beli Pagi Jual Sore  -> dicari saat sesi PAGI
  - BSJP  : Beli Sore Jual Pagi  -> dicari saat sesi SORE

Sesi ditentukan otomatis dari jam WIB saat script dijalankan:
  pagi  : sebelum 12:00 WIB  -> Swing + BPJS
  siang : 12:00 - 15:00 WIB  -> Swing (update posisi)
  sore  : setelah 15:00 WIB  -> Swing + BSJP

Cara pakai:
    pip install yfinance pandas numpy
    python idx_screening.py            # sesi otomatis dari jam WIB
    python idx_screening.py --sesi sore   # paksa sesi tertentu (untuk tes)

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
    """Metrik dasar yang dipakai semua strategi."""
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
        "close_position": float(close_position),  # 0 = tutup di low, 1 = di high
    }


# ---------------------------------------------------------------------------
# 3. STRATEGI SWING (teknikal + fundamental, logika lama)
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
    near_demand = price <= last_swing_low * 1.03
    if broke_resistance:
        structure_score += 30
    if near_demand:
        structure_score += 25

    volume_score = min(30, max(0, (m["vol_ratio"] - 1) * 30))
    technical_score = int(min(100, trend_score + structure_score + volume_score))

    if broke_resistance and m["vol_ratio"] > 1.2:
        setup, sl = "Breakout", last_swing_high * 0.985
    elif near_demand:
        setup, sl = "Demand Zone Bounce", last_swing_low * 0.98
    elif price > m["ma20"] and m["ma20"] > m["ma50"]:
        setup, sl = "BOS Konfirmasi", min(last_swing_low, m["ma50"]) * 0.99
    else:
        setup, sl = "Konsolidasi", price * 0.95

    entry = price
    risk = max(entry - sl, entry * 0.01)
    return {
        "strategy": "Swing",
        "setup": setup,
        "entry": round(entry),
        "sl": round(sl),
        "tp1": round(entry + 2 * risk),
        "tp2": round(entry + 3 * risk),
        "technicalScore": technical_score,
    }


# ---------------------------------------------------------------------------
# 4. STRATEGI BPJS — Beli Pagi Jual Sore (dicari sesi PAGI)
# ---------------------------------------------------------------------------
# Logika: cari saham yang PAGI ini menunjukkan momentum lanjutan dari kemarin,
# untuk dijual sebelum penutupan hari yang sama.
#   1. Gap up sehat: open hari ini 0.5% - 3% di atas close kemarin
#      (gap terlalu besar >3% rawan profit taking / "gap and crap")
#   2. ATAU harga pagi ini sudah menembus high kemarin (breakout intraday)
#   3. Kemarin candle bullish (momentum ada yang mendasari)
#   4. Masih dalam uptrend (harga > MA20)
# TP/SL sempit karena horizon hanya 1 hari:
#   SL  : di bawah open hari ini / -1.5%
#   TP1 : +2%   TP2 : +3.5%

def bpjs_strategy(m: dict):
    price, prev_close = m["price"], m["prev_close"]
    gap_pct = (m["open"] - prev_close) / prev_close * 100

    score = 0
    healthy_gap = 0.5 <= gap_pct <= 3.0
    broke_prev_high = price > m["prev_high"]

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

    # Minimal harus ada gap sehat ATAU breakout high kemarin
    if not (healthy_gap or broke_prev_high):
        return None

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
        "entry": round(entry),
        "sl": round(sl),
        "tp1": round(entry * 1.02),
        "tp2": round(entry * 1.035),
        "technicalScore": int(min(100, score)),
    }


# ---------------------------------------------------------------------------
# 5. STRATEGI BSJP — Beli Sore Jual Pagi (dicari sesi SORE)
# ---------------------------------------------------------------------------
# Logika: cari saham yang hari ini ditutup KUAT — historisnya, closing kuat
# dengan volume cenderung berlanjut ke gap up / kenaikan di pembukaan besok.
#   1. Strong close: tutup di 30% teratas rentang hari ini (close_position >= 0.7)
#   2. Candle hari ini hijau (close > open) dan naik vs kemarin
#   3. Volume >= 1.2x rata-rata 20 hari (ada akumulasi, bukan naik kosong)
#   4. Masih uptrend (harga > MA20)
# TP/SL sangat sempit karena target hanya pembukaan besok pagi:
#   SL  : -2% dari entry
#   TP1 : +1.5%  TP2 : +3%

def bsjp_strategy(m: dict):
    price = m["price"]
    strong_close = m["close_position"] >= 0.7
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
    elif m["vol_ratio"] >= 1.2:
        score += 15
    if price > m["ma20"]:
        score += 15

    setup = (
        "Strong Close + Volume"
        if m["vol_ratio"] >= 1.2
        else "Strong Close"
    )

    entry = price
    return {
        "strategy": "BSJP",
        "setup": setup,
        "entry": round(entry),
        "sl": round(entry * 0.98),
        "tp1": round(entry * 1.015),
        "tp2": round(entry * 1.03),
        "technicalScore": int(min(100, score)),
    }


# ---------------------------------------------------------------------------
# 6. FUNDAMENTAL (dipakai strategi Swing)
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
            f"{sig['setup']} — momentum pagi lanjutan dari kemarin. "
            "Target jual sebelum penutupan hari ini."
        )
    elif sig["strategy"] == "BSJP":
        parts.append(
            f"{sig['setup']} — tutup di area atas rentang harian "
            f"(posisi close {m['close_position']*100:.0f}%). "
            "Target jual di pembukaan besok pagi."
        )
    else:
        setup_notes = {
            "Breakout": "Breakout dengan volume di atas rata-rata 20 hari.",
            "Demand Zone Bounce": "Harga memantul dari demand zone terdekat.",
            "BOS Konfirmasi": "Struktur bullish (harga di atas MA20 & MA50).",
            "Konsolidasi": "Belum ada konfirmasi arah yang kuat.",
        }
        parts.append(setup_notes.get(sig["setup"], ""))
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

            # Strategi yang dijalankan tergantung sesi
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
                    "notes": build_notes(sig, fund, m),
                })

            print(f"[ok] {ticker}")

        except Exception as e:
            print(f"[error] {ticker}: {e}")

    results.sort(
        key=lambda s: (s["technicalScore"] + s["fundamentalScore"]) / 2,
        reverse=True,
    )

    output = {
        "generatedAt": datetime.now(timezone.utc).isoformat(),
        "session": session,
        "signals": results,
    }
    with open("signals.json", "w", encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False, indent=2)

    print(f"\nSelesai. {len(results)} sinyal disimpan ke signals.json")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--sesi", choices=["pagi", "siang", "sore"], default=None)
    args = parser.parse_args()
    run_screening(detect_session(args.sesi))
