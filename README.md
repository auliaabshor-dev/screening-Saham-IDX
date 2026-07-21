# Screening Harian Saham IDX

Screening otomatis saham Indonesia (teknikal + fundamental) dengan level
entry / SL / TP, berjalan sendiri setiap hari kerja lewat GitHub Actions.

## Isi repo

```
.
├── idx_screening.py                     # script screening utama
├── requirements.txt                     # dependency Python
├── signals.json                         # hasil screening (dibuat otomatis)
└── .github/workflows/screening-harian.yml   # otomasi harian
```

## Setup awal (sekali saja)

1. **Buat repo baru di GitHub** (private boleh, gratis).
2. **Upload semua file di folder ini** ke repo tersebut — pastikan struktur
   foldernya sama, terutama `.github/workflows/screening-harian.yml`.
3. **Aktifkan izin write untuk Actions** (agar bot bisa commit `signals.json`):
   - Buka repo → **Settings → Actions → General**
   - Bagian **Workflow permissions** → pilih **Read and write permissions** → Save
4. Selesai. Workflow akan jalan otomatis tiap **Senin–Jumat 16:30 WIB**
   (setelah pasar IDX tutup).

## Menjalankan manual

Buka tab **Actions** di repo → pilih **Screening Harian IDX** →
klik **Run workflow**. Berguna untuk tes pertama kali.

## Hasil

Setiap kali jalan, file `signals.json` di repo akan di-update otomatis
(muncul sebagai commit baru dari `screening-bot`). File ini yang dibaca
oleh dashboard.

Untuk menyambungkan ke dashboard, ambil URL raw-nya:

```
https://raw.githubusercontent.com/USERNAME/NAMA-REPO/main/signals.json
```

(Untuk repo private, URL raw butuh token — paling mudah jadikan repo
public, atau download manual.)

## Catatan

- Jadwal cron GitHub kadang molor 5–30 menit dari jadwal — normal.
- Edit `WATCHLIST` di `idx_screening.py` untuk ganti daftar saham.
- Data dari Yahoo Finance kadang tidak lengkap untuk emiten kecil;
  emiten yang datanya kurang akan otomatis di-skip (lihat log di tab Actions).
