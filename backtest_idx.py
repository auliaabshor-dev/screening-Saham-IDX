"""
Backtest strategi BSJP & BPJS terhadap data historis saham IDX.

Menguji aturan yang sama persis dengan idx_screening.py terhadap 1-2 tahun
data harian, lalu melaporkan: jumlah trade, win rate, rata-rata return per
trade, total return kumulatif, profit factor, dan drawdown maksimum.

Cara pakai:
    pip install yfinance pandas numpy
    python backtest_idx.py                  # backtest 2 tahun, aturan default
    python backtest_idx.py --tahun 1        # backtest 1 tahun
    python backtest_idx.py --sweep          # uji beberapa ambang sekaligus
                                            # (untuk kalibrasi parameter)

Output:
    - Ringkasan di terminal
    - backtest_trades.csv  (daftar semua trade, bisa dibuka di Excel)

CATATAN PEMODELAN (penting untuk membaca hasilnya secara jujur):
- BSJP: beli di CLOSE hari sinyal, jual di OPEN hari berikutnya.
  Ini model paling bersih untuk menguji premis "strong close -> gap up".
- BPJS: beli di OPEN hari gap, jual di CLOSE hari yang sama.
  TP/SL intraday dimodelkan dari High/Low harian dengan asumsi KONSERVATIF:
  jika SL dan TP sama-sama tersentuh di hari yang sama, dianggap SL kena
  duluan (karena urutan intraday tidak diketahui dari data harian).
- Belum termasuk biaya transaksi. Fee jual+beli di broker Indonesia umumnya
  sekitar 0.25-0.40% bolak-balik — kurangkan sendiri dari rata-rata return
  per trade untuk melihat hasil bersih.
"""

import argparse
import itertools

import numpy as np
import pandas as pd
import yfinance as yf

# ---------------------------------------------------------------------------
# KONFIGURASI
# ---------------------------------------------------------------------------

WATCHLIST = [
    "BBCA", "BBRI", "BMRI", "BBNI", "TLKM", "ASII", "ANTM", "ICBP",
    "UNVR", "INDF", "PGAS", "SMGR", "KLBF", "INCO", "PTBA", "ADRO",
    "MDKA", "AMRT", "CPIN", "EXCL",
]

VOLUME_AVG_WINDOW = 20

# Ambang default — sama dengan idx_screening.py
BSJP_RULES = {"close_pos_min": 0.7, "vol_ratio_min": 1.2, "need_ma20": True}
BPJS_RULES = {"gap_min": 0.5, "gap_max": 3.0, "tp_pct": 2.0, "sl_pct": 1.5,
              "need_prev_green": True, "need_ma20": True}


# ---------------------------------------------------------------------------
# DATA
# ---------------------------------------------------------------------------

def fetch(ticker: str, years: int) -> pd.DataFrame:
    df = yf.Ticker(f"{ticker}.JK").history(period=f"{years}y", interval="1d")
    df = df.dropna()
    if df.empty:
        return df
    df["MA20"] = df["Close"].rolling(20).mean()
    df["VolAvg"] = df["Volume"].rolling(VOLUME_AVG_WINDOW).mean()
    rng = (df["High"] - df["Low"]).replace(0, np.nan)
    df["ClosePos"] = (df["Close"] - df["Low"]) / rng
    return df


# ---------------------------------------------------------------------------
# BACKTEST BSJP: sinyal di close hari-i -> beli close hari-i, jual open hari-i+1
# ---------------------------------------------------------------------------

def backtest_bsjp(df: pd.DataFrame, ticker: str, rules: dict) -> list:
    trades = []
    for i in range(VOLUME_AVG_WINDOW, len(df) - 1):
        row, nxt = df.iloc[i], df.iloc[i + 1]
        if pd.isna(row["ClosePos"]) or pd.isna(row["VolAvg"]) or row["VolAvg"] == 0:
            continue

        strong_close = row["ClosePos"] >= rules["close_pos_min"]
        green = row["Close"] > row["Open"]
        vol_ok = (row["Volume"] / row["VolAvg"]) >= rules["vol_ratio_min"]
        ma_ok = (not rules["need_ma20"]) or (
            not pd.isna(row["MA20"]) and row["Close"] > row["MA20"]
        )

        if strong_close and green and vol_ok and ma_ok:
            entry = row["Close"]
            exit_ = nxt["Open"]
            ret = (exit_ - entry) / entry * 100
            trades.append({
                "strategy": "BSJP", "ticker": ticker,
                "date": df.index[i].date(), "entry": round(entry),
                "exit": round(exit_), "return_pct": round(ret, 3),
                "outcome": "win" if ret > 0 else "loss",
            })
    return trades


# ---------------------------------------------------------------------------
# BACKTEST BPJS: gap up di open hari-i -> beli open, TP/SL intraday, else close
# ---------------------------------------------------------------------------

def backtest_bpjs(df: pd.DataFrame, ticker: str, rules: dict) -> list:
    trades = []
    for i in range(VOLUME_AVG_WINDOW, len(df)):
        row, prev = df.iloc[i], df.iloc[i - 1]

        gap = (row["Open"] - prev["Close"]) / prev["Close"] * 100
        gap_ok = rules["gap_min"] <= gap <= rules["gap_max"]
        prev_green_ok = (not rules["need_prev_green"]) or (prev["Close"] > prev["Open"])
        ma_ok = (not rules["need_ma20"]) or (
            not pd.isna(prev["MA20"]) and prev["Close"] > prev["MA20"]
        )

        if not (gap_ok and prev_green_ok and ma_ok):
            continue

        entry = row["Open"]
        tp = entry * (1 + rules["tp_pct"] / 100)
        sl = entry * (1 - rules["sl_pct"] / 100)

        # Asumsi konservatif: kalau SL & TP sama-sama tersentuh, SL duluan.
        if row["Low"] <= sl:
            exit_, tag = sl, "sl"
        elif row["High"] >= tp:
            exit_, tag = tp, "tp"
        else:
            exit_, tag = row["Close"], "close"

        ret = (exit_ - entry) / entry * 100
        trades.append({
            "strategy": "BPJS", "ticker": ticker,
            "date": df.index[i].date(), "entry": round(entry),
            "exit": round(exit_), "return_pct": round(ret, 3),
            "outcome": "win" if ret > 0 else "loss", "exit_via": tag,
        })
    return trades


# ---------------------------------------------------------------------------
# STATISTIK
# ---------------------------------------------------------------------------

def summarize(trades: list, label: str) -> dict:
    if not trades:
        return {"label": label, "trades": 0}
    r = np.array([t["return_pct"] for t in trades])
    wins, losses = r[r > 0], r[r <= 0]

    equity = np.cumprod(1 + r / 100)
    peak = np.maximum.accumulate(equity)
    max_dd = float(((equity - peak) / peak).min() * 100)

    gross_win = wins.sum() if len(wins) else 0.0
    gross_loss = abs(losses.sum()) if len(losses) else 0.0
    pf = round(gross_win / gross_loss, 2) if gross_loss > 0 else float("inf")

    return {
        "label": label,
        "trades": len(r),
        "win_rate_pct": round(len(wins) / len(r) * 100, 1),
        "avg_return_pct": round(float(r.mean()), 3),
        "avg_win_pct": round(float(wins.mean()), 3) if len(wins) else 0.0,
        "avg_loss_pct": round(float(losses.mean()), 3) if len(losses) else 0.0,
        "total_return_pct": round(float((equity[-1] - 1) * 100), 1),
        "profit_factor": pf,
        "max_drawdown_pct": round(max_dd, 1),
    }


def print_summary(s: dict):
    if s["trades"] == 0:
        print(f"  {s['label']}: tidak ada trade yang memenuhi syarat")
        return
    print(f"  {s['label']}")
    print(f"    Jumlah trade      : {s['trades']}")
    print(f"    Win rate          : {s['win_rate_pct']}%")
    print(f"    Rata-rata/trade   : {s['avg_return_pct']:+.3f}%  "
          f"(win {s['avg_win_pct']:+.2f}% / loss {s['avg_loss_pct']:+.2f}%)")
    print(f"    Total kumulatif   : {s['total_return_pct']:+.1f}%  "
          f"(compounding, 1 posisi berurutan)")
    print(f"    Profit factor     : {s['profit_factor']}")
    print(f"    Max drawdown      : {s['max_drawdown_pct']}%")


# ---------------------------------------------------------------------------
# MAIN
# ---------------------------------------------------------------------------

def run(years: int, sweep: bool):
    print(f"Mengambil data {years} tahun untuk {len(WATCHLIST)} emiten…\n")
    data = {}
    for t in WATCHLIST:
        try:
            df = fetch(t, years)
            if len(df) > VOLUME_AVG_WINDOW + 10:
                data[t] = df
                print(f"[ok] {t}: {len(df)} hari")
            else:
                print(f"[skip] {t}: data kurang")
        except Exception as e:
            print(f"[error] {t}: {e}")

    if not data:
        print("Tidak ada data yang berhasil diambil.")
        return

    # ---- Backtest dengan aturan default ----
    all_trades = []
    for t, df in data.items():
        all_trades += backtest_bsjp(df, t, BSJP_RULES)
        all_trades += backtest_bpjs(df, t, BPJS_RULES)

    bsjp = [x for x in all_trades if x["strategy"] == "BSJP"]
    bpjs = [x for x in all_trades if x["strategy"] == "BPJS"]

    print("\n" + "=" * 60)
    print(f"HASIL BACKTEST ({years} tahun, aturan default)")
    print("=" * 60)
    print_summary(summarize(bsjp, "BSJP — Beli Sore Jual Pagi"))
    print()
    print_summary(summarize(bpjs, "BPJS — Beli Pagi Jual Sore"))

    if bpjs:
        via = pd.Series([t["exit_via"] for t in bpjs]).value_counts()
        print(f"\n    Exit BPJS via     : "
              + ", ".join(f"{k} {v}x" for k, v in via.items()))

    pd.DataFrame(all_trades).to_csv("backtest_trades.csv", index=False)
    print(f"\nDetail {len(all_trades)} trade disimpan ke backtest_trades.csv")
    print("\nCatatan: hasil BELUM termasuk fee broker (~0.25-0.40% per "
          "round trip).\nKurangkan fee dari rata-rata/trade untuk hasil bersih.")

    # ---- Mode sweep: uji beberapa ambang untuk kalibrasi ----
    if sweep:
        print("\n" + "=" * 60)
        print("SWEEP BSJP — variasi ambang close_position & volume")
        print("=" * 60)
        rows = []
        for cp, vr in itertools.product([0.6, 0.7, 0.8], [1.0, 1.2, 1.5]):
            rules = dict(BSJP_RULES, close_pos_min=cp, vol_ratio_min=vr)
            trades = []
            for t, df in data.items():
                trades += backtest_bsjp(df, t, rules)
            s = summarize(trades, f"cp>={cp}, vol>={vr}")
            rows.append(s)
        _print_sweep(rows)

        print("\n" + "=" * 60)
        print("SWEEP BPJS — variasi rentang gap & TP/SL")
        print("=" * 60)
        rows = []
        for (gmin, gmax), (tp, sl) in itertools.product(
            [(0.3, 2.0), (0.5, 3.0), (1.0, 4.0)],
            [(1.5, 1.0), (2.0, 1.5), (3.0, 2.0)],
        ):
            rules = dict(BPJS_RULES, gap_min=gmin, gap_max=gmax,
                         tp_pct=tp, sl_pct=sl)
            trades = []
            for t, df in data.items():
                trades += backtest_bpjs(df, t, rules)
            s = summarize(trades, f"gap {gmin}-{gmax}%, TP {tp}%/SL {sl}%")
            rows.append(s)
        _print_sweep(rows)

        print("\nPeringatan kalibrasi: memilih ambang terbaik dari sweep di "
              "data yang sama\nberisiko overfitting. Idealnya: pilih ambang "
              "dari sweep, lalu uji ulang di\nperiode berbeda (mis. sweep di "
              "tahun 1, validasi di tahun 2).")


def _print_sweep(rows: list):
    header = f"{'Aturan':<28}{'Trade':>7}{'Win%':>8}{'Avg%':>9}{'PF':>7}{'MaxDD%':>9}"
    print(header)
    print("-" * len(header))
    for s in rows:
        if s["trades"] == 0:
            print(f"{s['label']:<28}{'0':>7}")
            continue
        print(f"{s['label']:<28}{s['trades']:>7}{s['win_rate_pct']:>8}"
              f"{s['avg_return_pct']:>9}{s['profit_factor']:>7}"
              f"{s['max_drawdown_pct']:>9}")


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--tahun", type=int, default=2, choices=[1, 2, 3])
    p.add_argument("--sweep", action="store_true")
    args = p.parse_args()
    run(args.tahun, args.sweep)
