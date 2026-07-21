"""
Screening harian saham IDX — teknikal + fundamental, dengan entry/SL/TP.

Cara pakai:
    pip install yfinance pandas numpy
    python idx_screening.py

Output:
    signals.json  -> siap dibaca oleh dashboard (idx-screening-dashboard.jsx)

Edit WATCHLIST di bawah untuk menambah/mengurangi emiten yang di-screening.
Data harga & fundamental diambil gratis dari Yahoo Finance (yfinance).
"""

import json
from datetime import datetime, timezone

import numpy as np
import pandas as pd
import yfinance as yf

# ---------------------------------------------------------------------------
# 1. KONFIGURASI
# ---------------------------------------------------------------------------

# Watchlist starter — saham-saham likuid IDX30. Silakan tambah/kurangi.
WATCHLIST = [
    "BBCA", "BBRI", "BMRI", "BBNI", "TLKM", "ASII", "ANTM", "ICBP",
    "UNVR", "INDF", "PGAS", "SMGR", "KLBF", "INCO", "PTBA", "ADRO",
    "MDKA", "AMRT", "CPIN", "EXCL",
]

LOOKBACK_DAYS = 150          # histori harga yang diambil
SWING_WINDOW = 5             # jendela untuk deteksi swing high/low
VOLUME_AVG_WINDOW = 20
MIN_COMBINED_SCORE = 0       # ambang skor gabungan agar masuk signals.json


# ---------------------------------------------------------------------------
# 2. DATA HARGA & INDIKATOR TEKNIKAL
# ---------------------------------------------------------------------------

def fetch_price_history(ticker: str) -> pd.DataFrame:
    """Ambil OHLCV harian dari Yahoo Finance untuk ticker IDX (format .JK)."""
    df = yf.Ticker(f"{ticker}.JK").history(period=f"{LOOKBACK_DAYS}d", interval="1d")
    df = df.dropna()
    return df


def find_swings(df: pd.DataFrame, window: int = SWING_WINDOW):
    """Deteksi swing high & swing low sederhana (local extrema)."""
    highs, lows = [], []
    h, l = df["High"].values, df["Low"].values
    for i in range(window, len(df) - window):
        if h[i] == max(h[i - window:i + window + 1]):
            highs.append((i, h[i]))
        if l[i] == min(l[i - window:i + window + 1]):
            lows.append((i, l[i]))
    return highs, lows


def technical_analysis(df: pd.DataFrame):
    """
    Hitung skor teknikal (0-100) dan tentukan setup + level entry/SL/TP,
    memakai logika sederhana yang meniru indikator S&D / BOS / Fib:
      - Trend: posisi harga vs MA20 & MA50
      - Struktur: break of structure terhadap swing high/low terakhir
      - Volume: volume hari ini vs rata-rata 20 hari
    """
    close = df["Close"]
    volume = df["Volume"]
    last_price = float(close.iloc[-1])

    ma20 = close.rolling(20).mean().iloc[-1]
    ma50 = close.rolling(50).mean().iloc[-1] if len(df) >= 50 else ma20
    vol_avg = volume.rolling(VOLUME_AVG_WINDOW).mean().iloc[-1]
    vol_ratio = volume.iloc[-1] / vol_avg if vol_avg else 1.0

    highs, lows = find_swings(df)
    last_swing_high = highs[-1][1] if highs else close.max()
    last_swing_low = lows[-1][1] if lows else close.min()

    # --- Sub-skor teknikal ---
    trend_score = 0
    trend_score += 15 if last_price > ma20 else 0
    trend_score += 15 if last_price > ma50 else 0
    trend_score += 10 if ma20 > ma50 else 0  # MA20 di atas MA50 = bullish alignment

    structure_score = 0
    broke_resistance = last_price > last_swing_high * 0.999
    near_demand = last_price <= last_swing_low * 1.03  # dekat/di atas swing low <=3%
    if broke_resistance:
        structure_score += 30
    if near_demand:
        structure_score += 25

    volume_score = min(30, max(0, (vol_ratio - 1) * 30))  # volume di atas rata-rata

    technical_score = int(min(100, trend_score + structure_score + volume_score))

    # --- Tentukan setup & level entry/SL/TP ---
    if broke_resistance and vol_ratio > 1.2:
        setup = "Breakout"
        entry = last_price
        sl = last_swing_high * 0.985  # breakout level jadi support baru
    elif near_demand:
        setup = "Demand Zone Bounce"
        entry = last_price
        sl = last_swing_low * 0.98
    elif last_price > ma20 and ma20 > ma50:
        setup = "BOS Konfirmasi"
        entry = last_price
        sl = min(last_swing_low, ma50) * 0.99
    else:
        setup = "Konsolidasi"
        entry = last_price
        sl = last_price * 0.95

    risk = max(entry - sl, entry * 0.01)  # jaga risk tidak nol/negatif
    tp1 = entry + 2 * risk
    tp2 = entry + 3 * risk

    return {
        "price": round(last_price),
        "entry": round(entry),
        "sl": round(sl),
        "tp1": round(tp1),
        "tp2": round(tp2),
        "technicalScore": technical_score,
        "setup": setup,
    }


# ---------------------------------------------------------------------------
# 3. SKOR FUNDAMENTAL
# ---------------------------------------------------------------------------

def fundamental_analysis(ticker: str):
    """
    Skor fundamental sederhana (0-100) dari PER, PBV, ROE, dividend yield.
    Sumber: field `info` dari yfinance (ketersediaan data bervariasi per emiten).
    """
    info = yf.Ticker(f"{ticker}.JK").info

    per = info.get("trailingPE")
    pbv = info.get("priceToBook")
    roe = info.get("returnOnEquity")
    div_yield = info.get("dividendYield")
    sector = info.get("sector", "Lainnya")
    name = info.get("longName", ticker)

    score = 0
    # PER rendah lebih baik (skala kasar untuk saham IDX)
    if per is not None:
        if per < 10:
            score += 25
        elif per < 18:
            score += 15
        elif per < 25:
            score += 5

    # PBV rendah lebih baik
    if pbv is not None:
        if pbv < 1.5:
            score += 25
        elif pbv < 3:
            score += 15
        elif pbv < 5:
            score += 5

    # ROE tinggi lebih baik
    if roe is not None:
        if roe > 0.15:
            score += 30
        elif roe > 0.08:
            score += 15

    # Dividend yield jadi nilai tambah
    if div_yield is not None:
        if div_yield > 0.04:
            score += 20
        elif div_yield > 0.02:
            score += 10

    return {
        "fundamentalScore": int(min(100, score)),
        "sector": sector,
        "name": name,
        "per": per,
        "pbv": pbv,
        "roe": roe,
    }


# ---------------------------------------------------------------------------
# 4. JALANKAN SCREENING
# ---------------------------------------------------------------------------

def build_notes(tech: dict, fund: dict) -> str:
    parts = []
    if tech["setup"] == "Breakout":
        parts.append("Breakout dengan volume di atas rata-rata 20 hari.")
    elif tech["setup"] == "Demand Zone Bounce":
        parts.append("Harga memantul dari demand zone terdekat.")
    elif tech["setup"] == "BOS Konfirmasi":
        parts.append("Struktur bullish (harga di atas MA20 & MA50).")
    else:
        parts.append("Belum ada konfirmasi arah yang kuat, masih konsolidasi.")

    if fund.get("per") is not None:
        parts.append(f"PER {fund['per']:.1f}x.")
    if fund.get("roe") is not None:
        parts.append(f"ROE {fund['roe']*100:.1f}%.")

    return " ".join(parts)


def run_screening():
    results = []
    for ticker in WATCHLIST:
        try:
            df = fetch_price_history(ticker)
            if len(df) < 30:
                print(f"[skip] {ticker}: data historis kurang")
                continue

            tech = technical_analysis(df)
            fund = fundamental_analysis(ticker)

            combined = round((tech["technicalScore"] + fund["fundamentalScore"]) / 2)
            if combined < MIN_COMBINED_SCORE:
                continue

            results.append({
                "ticker": ticker,
                "name": fund["name"],
                "sector": fund["sector"],
                "price": tech["price"],
                "entry": tech["entry"],
                "sl": tech["sl"],
                "tp1": tech["tp1"],
                "tp2": tech["tp2"],
                "technicalScore": tech["technicalScore"],
                "fundamentalScore": fund["fundamentalScore"],
                "setup": tech["setup"],
                "notes": build_notes(tech, fund),
            })
            print(f"[ok] {ticker}: skor {combined} ({tech['setup']})")

        except Exception as e:
            print(f"[error] {ticker}: {e}")

    results.sort(
        key=lambda s: (s["technicalScore"] + s["fundamentalScore"]) / 2,
        reverse=True,
    )

    output = {
        "generatedAt": datetime.now(timezone.utc).isoformat(),
        "signals": results,
    }

    with open("signals.json", "w", encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False, indent=2)

    print(f"\nSelesai. {len(results)} emiten disimpan ke signals.json")


if __name__ == "__main__":
    run_screening()
