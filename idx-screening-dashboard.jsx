import React, { useState, useMemo, useEffect } from "react";

// ---------------------------------------------------------------------------
// SUMBER DATA — signals.json dari repo GitHub-mu (di-update otomatis 3x sehari
// oleh GitHub Actions: pagi 09:45, siang 12:15, sore 16:30 WIB).
// Kalau fetch gagal (mis. repo private / offline), dashboard memakai data contoh.
// ---------------------------------------------------------------------------

const DATA_URL =
  "https://raw.githubusercontent.com/auliaabshor-dev/screening-Saham-IDX/main/signals.json";

const SAMPLE_DATA = {
  generatedAt: null,
  session: "sore",
  signals: [
    {
      ticker: "BBCA",
      name: "Bank Central Asia",
      sector: "Perbankan",
      price: 9850,
      entry: 9825,
      sl: 9650,
      tp1: 10100,
      tp2: 10400,
      technicalScore: 82,
      fundamentalScore: 74,
      strategy: "Swing",
      setup: "Demand Zone Bounce",
      notes:
        "Contoh data. Harga memantul dari demand zone, struktur masih bullish.",
    },
    {
      ticker: "ANTM",
      name: "Aneka Tambang",
      sector: "Pertambangan",
      price: 1685,
      entry: 1685,
      sl: 1651,
      tp1: 1710,
      tp2: 1736,
      technicalScore: 85,
      fundamentalScore: 68,
      strategy: "BSJP",
      setup: "Strong Close + Volume",
      notes:
        "Contoh data. Tutup di area atas rentang harian dengan volume tinggi — target jual di pembukaan besok pagi.",
    },
    {
      ticker: "BMRI",
      name: "Bank Mandiri",
      sector: "Perbankan",
      price: 6425,
      entry: 6425,
      sl: 6329,
      tp1: 6554,
      tp2: 6650,
      technicalScore: 78,
      fundamentalScore: 72,
      strategy: "BPJS",
      setup: "Gap Up + Breakout",
      notes:
        "Contoh data. Gap up sehat menembus high kemarin — target jual sebelum penutupan hari ini.",
    },
  ],
};

const STRATEGY_META = {
  Semua: { color: "#8A97A8", desc: "Semua sinyal dari sesi terakhir" },
  Swing: {
    color: "#C9A227",
    desc: "Posisi beberapa hari — teknikal + fundamental",
  },
  BPJS: {
    color: "#4E9AD4",
    desc: "Beli Pagi Jual Sore — momentum intraday, keluar sebelum closing",
  },
  BSJP: {
    color: "#B07CD8",
    desc: "Beli Sore Jual Pagi — strong close, target gap up pembukaan besok",
  },
};

const SESSION_LABEL = {
  pagi: "Sesi Pagi · 09:45 WIB",
  siang: "Sesi Siang · 12:15 WIB",
  sore: "Sesi Sore · 16:30 WIB",
};

const scoreColor = (v) => {
  if (v >= 75) return "#3FB68C";
  if (v >= 55) return "#C9A227";
  return "#E2574C";
};

const fmt = (n) => new Intl.NumberFormat("id-ID").format(n);

function ConvictionBar({ value, label }) {
  return (
    <div className="flex items-center gap-2">
      <span className="w-20 shrink-0 text-[10px] uppercase tracking-widest text-[#8A97A8]">
        {label}
      </span>
      <div className="h-1.5 flex-1 rounded-full bg-[#1B2C40]">
        <div
          className="h-1.5 rounded-full transition-all"
          style={{ width: `${value}%`, backgroundColor: scoreColor(value) }}
        />
      </div>
      <span
        className="w-8 shrink-0 text-right font-mono text-xs"
        style={{ color: scoreColor(value) }}
      >
        {value}
      </span>
    </div>
  );
}

function RiskMap({ sl, entry, tp1, tp2 }) {
  const min = sl;
  const max = tp2;
  const range = max - min || 1;
  const pos = (v) => ((v - min) / range) * 100;
  const rr = ((tp1 - entry) / Math.max(entry - sl, 1)).toFixed(2);

  return (
    <div className="mt-4">
      <div className="mb-1.5 flex items-center justify-between">
        <span className="text-[10px] uppercase tracking-widest text-[#8A97A8]">
          Peta Risiko
        </span>
        <span className="font-mono text-[11px] text-[#8A97A8]">
          R:R ke TP1 &nbsp;
          <span className="text-[#EDEDE5]">{rr}</span>
        </span>
      </div>
      <div className="relative h-8 rounded-md bg-[#0B1420]">
        <div
          className="absolute top-0 h-full rounded-l-md bg-[#E2574C]/20"
          style={{ left: 0, width: `${pos(entry)}%` }}
        />
        <div
          className="absolute top-0 h-full bg-[#3FB68C]/15"
          style={{ left: `${pos(entry)}%`, width: `${100 - pos(entry)}%` }}
        />
        {[
          { v: sl, color: "#E2574C" },
          { v: entry, color: "#C9A227" },
          { v: tp1, color: "#3FB68C" },
          { v: tp2, color: "#3FB68C" },
        ].map((m, i) => (
          <div
            key={i}
            className="absolute top-0 h-full w-px"
            style={{
              left: `${pos(m.v)}%`,
              backgroundColor: m.color,
              opacity: 0.9,
            }}
          />
        ))}
      </div>
      <div className="relative mt-1 h-8">
        {[
          { v: sl, label: "SL" },
          { v: entry, label: "Entry" },
          { v: tp1, label: "TP1" },
          { v: tp2, label: "TP2" },
        ].map((m) => (
          <div
            key={m.label}
            className="absolute top-0 flex -translate-x-1/2 flex-col items-center"
            style={{ left: `${pos(m.v)}%` }}
          >
            <span className="text-[9px] uppercase tracking-wider text-[#8A97A8]">
              {m.label}
            </span>
            <span className="font-mono text-[11px] text-[#EDEDE5]">
              {fmt(m.v)}
            </span>
          </div>
        ))}
      </div>
    </div>
  );
}

function StrategyBadge({ strategy }) {
  const meta = STRATEGY_META[strategy] || STRATEGY_META.Semua;
  return (
    <span
      className="rounded-full px-2 py-0.5 font-mono text-[10px] font-semibold"
      style={{
        color: meta.color,
        border: `1px solid ${meta.color}55`,
        backgroundColor: `${meta.color}14`,
      }}
    >
      {strategy}
    </span>
  );
}

export default function IDXScreeningDashboard() {
  const [data, setData] = useState(null);
  const [status, setStatus] = useState("loading"); // loading | live | sample
  const [query, setQuery] = useState("");
  const [strategyTab, setStrategyTab] = useState("Semua");
  const [selected, setSelected] = useState(null);

  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const res = await fetch(`${DATA_URL}?t=${Date.now()}`);
        if (!res.ok) throw new Error(`HTTP ${res.status}`);
        const json = await res.json();
        if (!cancelled && Array.isArray(json.signals)) {
          setData(json);
          setStatus("live");
          return;
        }
        throw new Error("format tidak dikenal");
      } catch {
        if (!cancelled) {
          setData(SAMPLE_DATA);
          setStatus("sample");
        }
      }
    })();
    return () => {
      cancelled = true;
    };
  }, []);

  const signals = data?.signals ?? [];

  const filtered = useMemo(() => {
    return signals
      .filter((s) => {
        const matchQuery =
          s.ticker.toLowerCase().includes(query.toLowerCase()) ||
          (s.name || "").toLowerCase().includes(query.toLowerCase());
        const matchStrategy =
          strategyTab === "Semua" || s.strategy === strategyTab;
        return matchQuery && matchStrategy;
      })
      .sort(
        (a, b) =>
          (b.technicalScore + b.fundamentalScore) / 2 -
          (a.technicalScore + a.fundamentalScore) / 2
      );
  }, [signals, query, strategyTab]);

  const active =
    filtered.find((s) => `${s.ticker}-${s.strategy}` === selected) ||
    filtered[0];

  const generatedLabel = data?.generatedAt
    ? new Intl.DateTimeFormat("id-ID", {
        weekday: "long",
        day: "numeric",
        month: "long",
        hour: "2-digit",
        minute: "2-digit",
        timeZone: "Asia/Jakarta",
      }).format(new Date(data.generatedAt)) + " WIB"
    : "—";

  return (
    <div
      className="min-h-screen w-full text-[#EDEDE5]"
      style={{
        backgroundColor: "#0B1420",
        fontFamily: "'IBM Plex Sans', sans-serif",
      }}
    >
      <style>{`
        @import url('https://fonts.googleapis.com/css2?family=IBM+Plex+Sans:wght@400;500;600;700&family=IBM+Plex+Mono:wght@400;500;600&display=swap');
        .font-mono { font-family: 'IBM Plex Mono', monospace; }
        ::selection { background: #C9A227; color: #0B1420; }
      `}</style>

      <header className="border-b border-[#1B2C40] bg-[#0E1826]">
        <div className="mx-auto flex max-w-6xl flex-col gap-2 px-5 py-4 sm:flex-row sm:items-end sm:justify-between">
          <div>
            <p className="text-[10px] uppercase tracking-[0.25em] text-[#C9A227]">
              Bursa Efek Indonesia · Screening 3 Sesi
            </p>
            <h1 className="mt-1 text-2xl font-semibold tracking-tight">
              Papan Sinyal
            </h1>
          </div>
          <div className="text-right">
            <p className="font-mono text-xs text-[#8A97A8]">
              {SESSION_LABEL[data?.session] || "…"}
            </p>
            <p className="mt-0.5 font-mono text-[11px] text-[#5E6B7C]">
              {status === "loading" && "Memuat data…"}
              {status === "live" && `Update terakhir: ${generatedLabel}`}
              {status === "sample" &&
                "⚠ Gagal memuat data repo — menampilkan data contoh"}
            </p>
          </div>
        </div>
      </header>

      <main className="mx-auto max-w-6xl px-5 py-6">
        {/* Strategy tabs */}
        <div className="mb-4 flex flex-wrap gap-2">
          {Object.keys(STRATEGY_META).map((s) => {
            const isActive = strategyTab === s;
            const meta = STRATEGY_META[s];
            return (
              <button
                key={s}
                onClick={() => setStrategyTab(s)}
                className="rounded-md border px-3 py-1.5 text-xs font-medium transition-colors"
                style={{
                  borderColor: isActive ? meta.color : "#1B2C40",
                  backgroundColor: isActive ? `${meta.color}14` : "#0E1826",
                  color: isActive ? meta.color : "#8A97A8",
                }}
              >
                {s}
              </button>
            );
          })}
          <p className="ml-1 self-center text-[11px] text-[#5E6B7C]">
            {STRATEGY_META[strategyTab].desc}
          </p>
        </div>

        {/* Controls */}
        <div className="mb-5 flex flex-wrap items-center gap-3">
          <input
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            placeholder="Cari ticker atau nama emiten…"
            className="w-56 rounded-md border border-[#1B2C40] bg-[#0E1826] px-3 py-2 text-sm placeholder:text-[#5E6B7C] focus:border-[#C9A227] focus:outline-none"
          />
          <span className="ml-auto font-mono text-[11px] text-[#8A97A8]">
            {filtered.length} sinyal
          </span>
        </div>

        <div className="grid grid-cols-1 gap-5 lg:grid-cols-5">
          {/* Table */}
          <div className="overflow-hidden rounded-lg border border-[#1B2C40] lg:col-span-3">
            <table className="w-full text-left text-sm">
              <thead>
                <tr className="border-b border-[#1B2C40] bg-[#0E1826] text-[10px] uppercase tracking-widest text-[#8A97A8]">
                  <th className="px-4 py-3 font-medium">Emiten</th>
                  <th className="px-4 py-3 font-medium">Strategi</th>
                  <th className="px-4 py-3 text-right font-medium">Harga</th>
                  <th className="px-4 py-3 text-right font-medium">Skor</th>
                </tr>
              </thead>
              <tbody>
                {filtered.map((s) => {
                  const key = `${s.ticker}-${s.strategy}`;
                  const combined = Math.round(
                    (s.technicalScore + s.fundamentalScore) / 2
                  );
                  const isActive =
                    active &&
                    `${active.ticker}-${active.strategy}` === key;
                  return (
                    <tr
                      key={key}
                      onClick={() => setSelected(key)}
                      className={`cursor-pointer border-b border-[#1B2C40] last:border-0 transition-colors ${
                        isActive ? "bg-[#152238]" : "hover:bg-[#111E30]"
                      }`}
                    >
                      <td className="px-4 py-3">
                        <div className="font-mono font-semibold">
                          {s.ticker}
                        </div>
                        <div className="text-xs text-[#8A97A8]">{s.name}</div>
                      </td>
                      <td className="px-4 py-3">
                        <StrategyBadge strategy={s.strategy} />
                        <div className="mt-1 text-[11px] text-[#8A97A8]">
                          {s.setup}
                        </div>
                      </td>
                      <td className="px-4 py-3 text-right font-mono">
                        {fmt(s.price)}
                      </td>
                      <td className="px-4 py-3 text-right">
                        <span
                          className="font-mono font-semibold"
                          style={{ color: scoreColor(combined) }}
                        >
                          {combined}
                        </span>
                      </td>
                    </tr>
                  );
                })}
                {status !== "loading" && filtered.length === 0 && (
                  <tr>
                    <td
                      colSpan={4}
                      className="px-4 py-10 text-center text-sm text-[#5E6B7C]"
                    >
                      Tidak ada sinyal {strategyTab !== "Semua" && strategyTab}{" "}
                      di sesi ini.
                    </td>
                  </tr>
                )}
                {status === "loading" && (
                  <tr>
                    <td
                      colSpan={4}
                      className="px-4 py-10 text-center text-sm text-[#5E6B7C]"
                    >
                      Memuat sinyal…
                    </td>
                  </tr>
                )}
              </tbody>
            </table>
          </div>

          {/* Detail panel */}
          <div className="rounded-lg border border-[#1B2C40] bg-[#0E1826] p-5 lg:col-span-2">
            {active ? (
              <>
                <div className="flex items-start justify-between">
                  <div>
                    <h2 className="font-mono text-xl font-semibold">
                      {active.ticker}
                    </h2>
                    <p className="text-xs text-[#8A97A8]">
                      {active.name} · {active.sector}
                    </p>
                  </div>
                  <div className="flex flex-col items-end gap-1.5">
                    <StrategyBadge strategy={active.strategy} />
                    <span className="text-[10px] uppercase tracking-wider text-[#8A97A8]">
                      {active.setup}
                    </span>
                  </div>
                </div>

                <div className="mt-5 space-y-2.5">
                  <ConvictionBar
                    value={active.technicalScore}
                    label="Teknikal"
                  />
                  <ConvictionBar
                    value={active.fundamentalScore}
                    label="Fundamental"
                  />
                </div>

                <RiskMap
                  sl={active.sl}
                  entry={active.entry}
                  tp1={active.tp1}
                  tp2={active.tp2}
                />

                <div className="mt-5 border-t border-[#1B2C40] pt-4">
                  <p className="text-[10px] uppercase tracking-widest text-[#8A97A8]">
                    Catatan
                  </p>
                  <p className="mt-1.5 text-sm leading-relaxed text-[#C7CEDA]">
                    {active.notes}
                  </p>
                </div>
              </>
            ) : (
              status !== "loading" && (
                <p className="text-sm text-[#5E6B7C]">
                  Pilih emiten di tabel untuk melihat detail.
                </p>
              )
            )}
          </div>
        </div>

        <p className="mt-6 text-center text-[11px] text-[#5E6B7C]">
          Sinyal dihasilkan otomatis dari screening — bukan rekomendasi
          investasi. Keputusan transaksi tetap di tanganmu.
        </p>
      </main>
    </div>
  );
}
