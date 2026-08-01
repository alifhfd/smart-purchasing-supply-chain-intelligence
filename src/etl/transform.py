"""
transform.py
============
Tahap: CLEANING LAYER

Alur: Raw Data -> Cleaning -> Processed (siap divalidasi)

Tanggung jawab modul ini:
1. Membaca data mentah (dari CSV raw hasil `extract.py`).
2. Membersihkan data TANPA mengubah maknanya:
   - rename kolom ke snake_case yang deskriptif
   - trim whitespace
   - remove duplicate
   - convert angka format Indonesia (locale) -> float
   - convert tanggal -> datetime, turunkan price_date
   - handling missing value (dicatat, tidak dikarang)
   - normalisasi nama commodity & negara
3. Menyimpan hasil ke `data/processed/commodity_<timestamp>.csv`.

DILARANG melakukan di file ini:
- validasi bisnis (range check, invalid price, dsb) -> itu tanggung jawab
  `validate.py`
- feature engineering (moving average, dsb) -> tahap terpisah setelah
  validasi lolos
"""

from __future__ import annotations

import re
from datetime import datetime
from pathlib import Path

import pandas as pd

from config.config import Config
from src.utils.logger import get_logger

logger = get_logger(__name__, Config.LOG_DIR)


# Mapping rename kolom: nama kolom mentah (bahasa sumber) -> snake_case standar
COLUMN_RENAME_MAP: dict[str, str] = {
    "nama": "commodity_name",
    "bulan": "contract_month",
    "terakhir": "last_price",
    "tertinggi": "high_price",
    "terendah": "low_price",
    "perubahan": "change_value",
    "perubahan_persen": "change_percent",
    "scraped_at": "scraped_at",
    "source_url": "source_url",
}

# Kolom yang berisi angka format Indonesia (titik=ribuan, koma=desimal)
NUMERIC_COLUMNS: list[str] = [
    "last_price",
    "high_price",
    "low_price",
    "change_value",
    "change_percent",
]


def parse_indonesian_number(value: object) -> float | None:
    """Mengubah string angka format Indonesia menjadi float.

    Contoh: "4.070,80" -> 4070.80 | "+20,60" -> 20.60 | "-0,29%" -> -0.29

    Args:
        value: Nilai mentah, bisa string, None, atau sudah numerik.

    Returns:
        float | None: hasil konversi, atau None jika nilai kosong/tidak valid.
    """
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)

    text = str(value).strip()
    if not text:
        return None

    # Buang simbol non-angka kecuali digit, koma, titik, dan tanda minus
    text = re.sub(r"[^\d,.\-]", "", text)
    if not text:
        return None

    # Format Indonesia: titik = pemisah ribuan, koma = pemisah desimal
    text = text.replace(".", "").replace(",", ".")

    try:
        return float(text)
    except ValueError:
        logger.warning("Gagal parsing angka: nilai_asli=%r", value)
        return None


class CommodityCleaner:
    """Membersihkan data mentah commodity menjadi data siap validasi.

    Setiap method mengerjakan SATU langkah cleaning secara eksplisit,
    supaya urutan dan alasan tiap transformasi mudah ditelusuri/di-test.
    """

    def __init__(self, processed_dir: Path = Config.DATA_PROCESSED_DIR) -> None:
        """Inisialisasi cleaner.

        Args:
            processed_dir: Direktori tujuan penyimpanan CSV hasil cleaning.
        """
        self.processed_dir = processed_dir
        self.processed_dir.mkdir(parents=True, exist_ok=True)

    def rename_columns(self, df: pd.DataFrame) -> pd.DataFrame:
        """Mengganti nama kolom mentah menjadi snake_case standar.

        Args:
            df: DataFrame mentah dari raw layer.

        Returns:
            pd.DataFrame: DataFrame dengan nama kolom sudah standar.
        """
        df = df.rename(columns=COLUMN_RENAME_MAP)
        logger.info("Rename kolom selesai | kolom=%s", list(df.columns))
        return df

    def trim_whitespace(self, df: pd.DataFrame) -> pd.DataFrame:
        """Menghapus spasi berlebih di awal/akhir semua kolom bertipe teks.

        Args:
            df: DataFrame input.

        Returns:
            pd.DataFrame: DataFrame dengan whitespace ter-trim.
        """
        text_columns = df.select_dtypes(include="object").columns
        for col in text_columns:
            # Simpan mask nilai kosong SEBELUM di-cast ke string, supaya
            # None/NaN asli tertangkap tanpa bergantung ke representasi
            # string-nya ("nan" vs "None" bisa beda tergantung tipe asal).
            is_empty = df[col].isna()

            df[col] = df[col].astype(str).str.strip()
            df.loc[is_empty | (df[col] == ""), col] = None
        return df

    def remove_duplicates(self, df: pd.DataFrame) -> pd.DataFrame:
        """Menghapus baris yang identik persis di semua kolom.

        Args:
            df: DataFrame input.

        Returns:
            pd.DataFrame: DataFrame tanpa baris duplikat.
        """
        before = len(df)
        df = df.drop_duplicates()
        removed = before - len(df)
        if removed > 0:
            logger.info("Remove duplicate | baris_dihapus=%d", removed)
        return df

    def convert_numeric_columns(self, df: pd.DataFrame) -> pd.DataFrame:
        """Mengonversi kolom harga/perubahan dari string locale ID ke float.

        Args:
            df: DataFrame input.

        Returns:
            pd.DataFrame: DataFrame dengan kolom numerik bertipe float64.
        """
        for col in NUMERIC_COLUMNS:
            if col in df.columns:
                df[col] = df[col].apply(parse_indonesian_number)
        return df

    def convert_dates(self, df: pd.DataFrame) -> pd.DataFrame:
        """Mengonversi kolom scraped_at ke datetime dan menurunkan price_date.

        Args:
            df: DataFrame input.

        Returns:
            pd.DataFrame: DataFrame dengan `scraped_at` bertipe datetime64
            dan kolom baru `price_date` (tanggal saja, tanpa jam).
        """
        df["scraped_at"] = pd.to_datetime(df["scraped_at"], errors="coerce")
        df["price_date"] = df["scraped_at"].dt.date
        return df

    def handle_missing_values(self, df: pd.DataFrame) -> pd.DataFrame:
        """Menangani nilai kosong: catat di log, drop baris tanpa kunci utama.

        Prinsip: TIDAK mengarang nilai numerik yang hilang (tetap NaN),
        karena itu bisa menyesatkan analisis. Baris tanpa `commodity_name`
        di-drop karena tidak punya kunci identitas yang valid.

        Args:
            df: DataFrame input.

        Returns:
            pd.DataFrame: DataFrame setelah penanganan missing value.
        """
        missing_summary = df.isna().sum()
        missing_summary = missing_summary[missing_summary > 0]
        if not missing_summary.empty:
            logger.info(
                "Missing value ditemukan:\n%s", missing_summary.to_string()
            )

        before = len(df)
        df = df.dropna(subset=["commodity_name"])
        dropped = before - len(df)
        if dropped > 0:
            logger.warning(
                "Baris di-drop karena commodity_name kosong | jumlah=%d",
                dropped,
            )
        return df

    def normalize_commodity_names(self, df: pd.DataFrame) -> pd.DataFrame:
        """Merapikan nama commodity: collapse whitespace ganda, tanpa ubah casing.

        Casing sengaja TIDAK dipaksa seragam (misal title-case), karena ada
        nama seperti "XAU/USD" yang rusak maknanya kalau di-title-case.

        Args:
            df: DataFrame input.

        Returns:
            pd.DataFrame: DataFrame dengan `commodity_name` sudah rapi.
        """
        df["commodity_name"] = (
            df["commodity_name"]
            .astype(str)
            .str.replace(r"\s+", " ", regex=True)
            .str.strip()
        )
        return df

    def normalize_country_names(self, df: pd.DataFrame) -> pd.DataFrame:
        """Placeholder normalisasi nama negara.

        Domain commodity saat ini belum memiliki kolom negara eksplisit
        dari hasil scraping (informasi bendera belum diekstrak sebagai
        data terstruktur). Method ini disiapkan sebagai kontrak yang
        konsisten untuk domain lain (kurs, trade, shipping) yang PASTI
        memiliki kolom negara.

        Args:
            df: DataFrame input.

        Returns:
            pd.DataFrame: DataFrame tanpa perubahan (no-op untuk saat ini).
        """
        if "country" in df.columns:
            df["country"] = df["country"].astype(str).str.strip().str.title()
        return df

    def run(self, df: pd.DataFrame) -> pd.DataFrame:
        """Menjalankan seluruh langkah cleaning secara berurutan.

        Urutan ini penting: rename dulu supaya langkah berikutnya bisa
        merujuk nama kolom standar, trim sebelum parsing angka (supaya
        tidak ada spasi nyasar), lalu baru convert & handle missing value.

        Args:
            df: DataFrame mentah dari raw layer.

        Returns:
            pd.DataFrame: DataFrame bersih, siap masuk tahap validasi.
        """
        logger.info("Cleaning Started | rows_in=%d", len(df))

        df = self.rename_columns(df)
        df = self.trim_whitespace(df)
        df = self.remove_duplicates(df)
        df = self.convert_numeric_columns(df)
        df = self.convert_dates(df)
        df = self.handle_missing_values(df)
        df = self.normalize_commodity_names(df)
        df = self.normalize_country_names(df)

        df = df.reset_index(drop=True)
        logger.info("Cleaning Success | rows_out=%d", len(df))
        return df

    def save(self, df: pd.DataFrame) -> Path:
        """Menyimpan DataFrame bersih ke CSV bertimestamp di data/processed/.

        Args:
            df: DataFrame yang sudah dibersihkan.

        Returns:
            Path: lokasi file CSV yang baru dibuat.
        """
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        file_path = self.processed_dir / f"commodity_{timestamp}.csv"
        df.to_csv(file_path, index=False, encoding="utf-8")
        logger.info("Processed CSV Saved | path=%s | rows=%d", file_path, len(df))
        return file_path


def run_transform(raw_csv_path: Path) -> tuple[pd.DataFrame, Path]:
    """Entry point tahap transform: baca raw CSV -> bersihkan -> simpan.

    Dipanggil oleh orchestrator pipeline utama (main.py) dengan path CSV
    yang dihasilkan oleh `run_extract()` di `extract.py`.

    Args:
        raw_csv_path: Path file CSV mentah hasil tahap extract.

    Returns:
        tuple[pd.DataFrame, Path]: DataFrame bersih beserta lokasi CSV
        hasil cleaning, dipakai sebagai input eksplisit tahap `validate.py`.
    """
    df_raw = pd.read_csv(raw_csv_path)

    cleaner = CommodityCleaner()
    df_clean = cleaner.run(df_raw)
    processed_path = cleaner.save(df_clean)

    return df_clean, processed_path


if __name__ == "__main__":
    # Contoh manual run: ambil file raw TERBARU di data/raw/
    raw_files = sorted(Config.DATA_RAW_DIR.glob("commodity_*.csv"))
    if not raw_files:
        logger.error("Tidak ada file raw ditemukan di %s", Config.DATA_RAW_DIR)
    else:
        latest_raw = raw_files[-1]
        run_transform(latest_raw)