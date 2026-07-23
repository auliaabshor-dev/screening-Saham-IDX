"""
Jurnal otomatis performa sinyal — validasi berjalan di data live.

Tugasnya tiga:
  1. INGEST : setiap sesi SORE, sinyal berstatus "aktif" dari signals.json
              dicatat sebagai posisi terbuka (entry = harga close sore itu,
              sama dengan asumsi backtest).
  2. UPDATE : setiap kali jalan (pagi/siang/sore), semua posisi terbuka
              dicek terhadap harga aktual:
              - Swing Breakout: SL / TP2 (target utama) / timeout 20 hari,
                dengan model sadar-gap yang sama seperti backtest
              - BSJP: keluar di OPEN hari bursa berikutnya
  3. STATS  : hitung statistik berjalan dari semua posisi tertutup
              (win rate, rata-rata, profit factor) per strategi.

Hasil disimpan ke journal.json — dibaca dashboard.

Dijalankan otomatis oleh GitHub Actions SETELAH idx_screening.py
(lihat screening-harian.yml). Bisa juga manual: python jurnal.py

Catatan kejujuran: entry dicatat di harga close screening — realisasi
kamu bisa sedikit berbeda (slippage, antrean). Jurnal ini memvalidasi
SINYALNYA; hasil akun sungguhan tetap perlu kamu catat sendiri.
"""

import json
import os
import uuid
from datetime import datetime, timezone, timedelta, date

import numpy as np
import pandas as pd
import yfinance as yf

WIB = timezone(timedelta(hours=7))
SIGNALS_FILE = "signals.json"
JOURNAL_FILE = "journal.json"
MAX_HOLD = 20          # hari bursa — sama dengan backtest Swing
FETCH_DAYS = 90


# ---------------------------------------------------------------------------
# IO
# ---------------------------------------------------------------------------

def load_json(path, default):
    if not os.path.exists(path):
        return default
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def save_json(path, obj):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=2)


def fetch_daily(ticker: str) -> pd.DataFrame:
    df = yf.Ticker(f"{ticker}.JK").history(period=f"{FETCH_DAYS}d", interval="1d")
    return df.dropna()


# ---------------------------------------------------------------------------
# 1. INGEST — catat sinyal aktif sesi sore sebagai posisi terbuka
# ---------------------------------------------------------------------------

def ingest(journal: dict, signals: dict) -> int:
    if signals.get("session") != "sore":
        return 0  # entry hanya dicatat dari sesi sore (harga close final)

    gen = signals.get("generatedAt")
    try:
        day = datetime.fromisoformat(gen).astimezone(WIB).date()
    except Exception:
        day = datetime.now(WIB).date()
    day_str = day.isoformat()

    open_keys = {(e["ticker"], e["strategy"]) for e in journal["open"]}
    closed_today = {
        (c["ticker"], c["strategy"], c["date_in"]) for c in journal["closed"]
    }

    added = 0
    for s in signals.get("signals", []):
        if s.get("status") != "aktif":
            continue
        key = (s["ticker"], s["strategy"])
        if key in open_keys:
            continue  # masih ada posisi terbuka di emiten+strategi ini
        if (s["ticker"], s["strategy"], day_str) in closed_today:
            continue  # sudah pernah dicatat & ditutup hari yang sama (re-run)

        journal["open"].append({
            "id": uuid.uuid4().hex[:8],
            "ticker": s["ticker"],
            "strategy": s["strategy"],
            "setup": s.get("setup", ""),
            "date_in": day_str,
            "entry": s["entry"],
            "sl": s["sl"],
            "tp1": s["tp1"],
            "tp2": s["tp2"],
            "technicalScore": s.get("technicalScore"),
            "fundamentalScore": s.get("fundamentalScore"),
        })
        open_keys.add(key)
        added += 1
    return added


# ---------------------------------------------------------------------------
# 2. UPDATE — evaluasi posisi terbuka terhadap harga aktual
# ---------------------------------------------------------------------------

def bars_after(df: pd.DataFrame, date_in: str) -> pd.DataFrame:
    d = date.fromisoformat(date_in)
    mask = [idx.date() > d for idx in df.index]
    return df.loc[mask]


def evaluate_swing(entry: dict, df: pd.DataFrame):
    """Model sama dengan backtest: sadar-gap, target TP2, timeout MAX_HOLD."""
    bars = bars_after(df, entry["date_in"])
    if bars.empty:
        return None
    sl, tgt = entry["sl"], entry["tp2"]

    for i, (idx, day) in enumerate(bars.iterrows(), start=1):
        if day["Open"] <= sl:
            return _close(entry, idx, float(day["Open"]), "gap-sl", i)
        if day["Low"] <= sl:
            return _close(entry, idx, sl, "sl", i)
        if day["Open"] >= tgt:
            return _close(entry, idx, float(day["Open"]), "gap-tp", i)
        if day["High"] >= tgt:
            return _close(entry, idx, tgt, "tp", i)
        if i >= MAX_HOLD:
            return _close(entry, idx, float(day["Close"]), "timeout", i)
    return None  # masih terbuka


def evaluate_bsjp(entry: dict, df: pd.DataFrame):
    """BSJP: keluar di OPEN hari bursa pertama setelah tanggal entry."""
    bars = bars_after(df, entry["date_in"])
    if bars.empty:
        return None
    idx = bars.index[0]
    return _close(entry, idx, float(bars.iloc[0]["Open"]), "open-besok", 1)


def _close(entry: dict, idx, exit_price: float, via: str, hold: int) -> dict:
    ret = (exit_price - entry["entry"]) / entry["entry"] * 100
    out = dict(entry)
    out.update({
        "date_out": idx.date().isoformat(),
        "exit": round(exit_price),
        "exit_via": via,
        "hold_days": hold,
        "return_pct": round(ret, 3),
        "outcome": "win" if ret > 0 else "loss",
    })
    return out


def update_positions(journal: dict) -> int:
    if not journal["open"]:
        return 0
    tickers = sorted({e["ticker"] for e in journal["open"]})
    prices = {}
    for t in tickers:
        try:
            prices[t] = fetch_daily(t)
        except Exception as e:
            print(f"[error] harga {t}: {e}")

    still_open, closed_now = [], []
    for e in journal["open"]:
        df = prices.get(e["ticker"])
        if df is None or df.empty:
            still_open.append(e)
            continue
        result = (
            evaluate_bsjp(e, df) if e["strategy"] == "BSJP"
            else evaluate_swing(e, df)
        )
        if result is None:
            still_open.append(e)
        else:
            closed_now.append(result)

    journal["open"] = still_open
    journal["closed"].extend(closed_now)
    return len(closed_now)


# ---------------------------------------------------------------------------
# 3. STATS — statistik berjalan dari posisi tertutup
# ---------------------------------------------------------------------------

def calc_stats(closed: list) -> dict:
    def stat(trades):
        if not trades:
            return {"trades": 0}
        r = np.array([t["return_pct"] for t in trades])
        wins, losses = r[r > 0], r[r <= 0]
        gw = float(wins.sum()) if len(wins) else 0.0
        gl = float(abs(losses.sum())) if len(losses) else 0.0
        return {
            "trades": int(len(r)),
            "win_rate_pct": round(len(wins) / len(r) * 100, 1),
            "avg_return_pct": round(float(r.mean()), 3),
            "profit_factor": (round(gw / gl, 2) if gl > 0 else None),
        }

    out = {"total": stat(closed)}
    for strat in sorted({t["strategy"] for t in closed}):
        out[strat] = stat([t for t in closed if t["strategy"] == strat])
    return out


# ---------------------------------------------------------------------------
# MAIN
# ---------------------------------------------------------------------------

def main():
    signals = load_json(SIGNALS_FILE, {})
    journal = load_json(JOURNAL_FILE, {"open": [], "closed": [], "stats": {}})
    journal.setdefault("open", [])
    journal.setdefault("closed", [])

    n_new = ingest(journal, signals)
    n_closed = update_positions(journal)
    journal["stats"] = calc_stats(journal["closed"])
    journal["updatedAt"] = datetime.now(timezone.utc).isoformat()

    save_json(JOURNAL_FILE, journal)

    print(f"Jurnal: +{n_new} posisi baru, {n_closed} ditutup, "
          f"{len(journal['open'])} masih terbuka, "
          f"{len(journal['closed'])} total tertutup")
    tot = journal["stats"].get("total", {})
    if tot.get("trades"):
        print(f"Performa berjalan: {tot['trades']} trade, "
              f"win {tot['win_rate_pct']}%, avg {tot['avg_return_pct']:+.2f}%, "
              f"PF {tot['profit_factor']}")


if __name__ == "__main__":
    main()
