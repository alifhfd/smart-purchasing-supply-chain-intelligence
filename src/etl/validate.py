"""
validate.py
===========
Tahap: VALIDATION LAYER

Alur: Processed Data -> Validation -> (Valid -> Staging) / (Invalid -> logs/error/)

Tanggung jawab modul ini:
1. Memeriksa data yang SUDAH bersih dari `transform.py`.
2. TIDAK mengubah nilai apa pun — hanya menghakimi valid/tidak valid.
3. Baris yang gagal validasi disimpan ke `logs/error/` LENGKAP dengan
   kolom `validation_reason` yang menjelaskan penyebabnya.
4. Baris yang lolos dikembalikan untuk diteruskan ke `load.py` (Staging).

Urutan pemeriksaan:
- schema check   -> gagal total kalau kolom wajib hilang (structural failure)
- null check     -> kolom kunci wajib terisi
- duplicate check -> jaring pengaman kedua setelah transform.py
- range check / invalid price -> harga harus masuk akal
- invalid currency check -> stub untuk domain lain (kurs, trade)
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

import pandas as pd

from config.config import Config
from src.utils.logger import get_logger

logger = get_logger(__name__, Config.LOG_DIR)


# Kolom yang WAJIB ada di DataFrame sebelum validasi bisa dijalankan.
# Kalau ada yang hilang, ini masalah struktural (pipeline extract/transform
# rusak), bukan masalah satu baris data.
REQUIRED_COLUMNS: list[str] = [
    "commodity_name",
    "detail_url",
    "contract_month",
    "last_price",
    "high_price",
    "low_price",
    "change_value",
    "change_percent",
    "scraped_at",
    "price_date",
]

# Kolom yang tidak boleh kosong per baris (null check)
NOT_NULL_COLUMNS: list[str] = ["commodity_name", "last_price", "price_date"]

# Kode mata uang yang dianggap valid (dipakai domain lain yang punya kolom 'currency')
VALID_CURRENCY_CODES: set[str] = {"USD", "IDR", "EUR", "GBP", "JPY", "CNY"}


@dataclass
class ValidationSummary:
    """Ringkasan hasil validasi satu batch data.

    Attributes:
        total_rows: Jumlah baris sebelum validasi.
        valid_rows: Jumlah baris yang lolos semua pemeriksaan.
        invalid_rows: Jumlah baris yang gagal (dengan alasan apa pun).
        reasons_count: Rekap jumlah baris gagal per jenis alasan.
    """

    total_rows: int = 0
    valid_rows: int = 0
    invalid_rows: int = 0
    reasons_count: dict[str, int] = field(default_factory=dict)


class SchemaValidationError(Exception):
    """Dilempar jika kolom wajib hilang dari DataFrame (structural failure)."""


class CommodityValidator:
    """Memvalidasi data commodity yang sudah bersih, sebelum masuk staging.

    Setiap method `check_*` menambahkan kolom alasan ke baris yang gagal
    pada method tersebut, TANPA menghapus baris apa pun — penghapusan
    (pemisahan valid/invalid) baru terjadi di akhir lewat `validate()`.
    """

    def __init__(self, error_dir: Path = Config.LOG_ERROR_DIR) -> None:
        """Inisialisasi validator.

        Args:
            error_dir: Direktori tujuan penyimpanan baris yang gagal validasi.
        """
        self.error_dir = error_dir
        self.error_dir.mkdir(parents=True, exist_ok=True)

    def check_schema(self, df: pd.DataFrame) -> None:
        """Memastikan semua kolom wajib ada di DataFrame.

        Args:
            df: DataFrame hasil cleaning.

        Raises:
            SchemaValidationError: jika ada kolom wajib yang hilang.
        """
        missing = [col for col in REQUIRED_COLUMNS if col not in df.columns]
        if missing:
            raise SchemaValidationError(
                f"Kolom wajib hilang dari data: {missing}"
            )

    def check_nulls(
        self, df: pd.DataFrame, reasons: list[list[str]]
    ) -> None:
        """Menandai baris dengan kolom kunci yang kosong.

        Args:
            df: DataFrame yang diperiksa.
            reasons: List penampung alasan, di-mutasi langsung (append).
        """
        for col in NOT_NULL_COLUMNS:
            mask = df[col].isna()
            for idx in df.index[mask]:
                reasons[idx].append(f"null_check_failed:{col}")

    def check_duplicates(
        self, df: pd.DataFrame, reasons: list[list[str]]
    ) -> None:
        """Menandai baris duplikat berdasarkan kunci bisnis (instrumen + tanggal).

        Kunci dedup memakai `detail_url` (bukan `commodity_name` saja),
        karena instrumen berbeda bisa punya nama identik (misal "Tembaga"
        untuk Comex vs LME) tapi merujuk komoditas/bursa yang berbeda.
        `detail_url` unik per instrumen sehingga tidak salah anggap duplikat.

        Ini jaring pengaman KEDUA — idealnya `transform.py` sudah
        menghapus duplikat, tapi validasi tetap cek ulang secara independen.

        Args:
            df: DataFrame yang diperiksa.
            reasons: List penampung alasan, di-mutasi langsung (append).
        """
        dup_mask = df.duplicated(
            subset=["detail_url", "price_date"], keep="first"
        )
        for idx in df.index[dup_mask]:
            reasons[idx].append("duplicate_check_failed")

    def check_price_range(
        self, df: pd.DataFrame, reasons: list[list[str]]
    ) -> None:
        """Menandai harga yang tidak masuk akal (invalid price / range check).

        Aturan:
        - last_price, high_price, low_price harus > 0 (kalau tidak null)
        - high_price harus >= low_price (kalau keduanya terisi)

        Args:
            df: DataFrame yang diperiksa.
            reasons: List penampung alasan, di-mutasi langsung (append).
        """
        for price_col in ["last_price", "high_price", "low_price"]:
            invalid_mask = df[price_col].notna() & (df[price_col] <= 0)
            for idx in df.index[invalid_mask]:
                reasons[idx].append(f"invalid_price:{price_col}<=0")

        both_filled = df["high_price"].notna() & df["low_price"].notna()
        inverted_mask = both_filled & (df["high_price"] < df["low_price"])
        for idx in df.index[inverted_mask]:
            reasons[idx].append("invalid_price:high_price<low_price")

    def check_currency(
        self, df: pd.DataFrame, reasons: list[list[str]]
    ) -> None:
        """Menandai kode mata uang yang tidak dikenali (invalid currency).

        Domain commodity saat ini belum memiliki kolom `currency` eksplisit,
        jadi pemeriksaan ini otomatis di-skip (no-op) untuk data commodity.
        Method ini disiapkan sebagai kontrak yang sama untuk domain lain
        (kurs, trade, shipping) yang PASTI memiliki kolom currency.

        Args:
            df: DataFrame yang diperiksa.
            reasons: List penampung alasan, di-mutasi langsung (append).
        """
        if "currency" not in df.columns:
            return

        invalid_mask = ~df["currency"].isin(VALID_CURRENCY_CODES)
        for idx in df.index[invalid_mask]:
            reasons[idx].append(f"invalid_currency:{df.at[idx, 'currency']!r}")

    def validate(
        self, df: pd.DataFrame
    ) -> tuple[pd.DataFrame, pd.DataFrame, ValidationSummary]:
        """Menjalankan seluruh pemeriksaan dan memisahkan valid/invalid.

        Args:
            df: DataFrame hasil cleaning dari `transform.py`.

        Returns:
            tuple berisi:
            - DataFrame baris yang VALID (siap ke staging)
            - DataFrame baris yang INVALID (dengan kolom `validation_reason`)
            - ValidationSummary ringkasan hasil validasi

        Raises:
            SchemaValidationError: jika kolom wajib hilang (gagal total,
                tidak menghasilkan valid/invalid split apa pun).
        """
        logger.info("Validation Started | rows_in=%d", len(df))

        # Schema check dilakukan duluan dan terpisah -> kalau gagal,
        # seluruh proses dihentikan (bukan per-baris).
        self.check_schema(df)

        df = df.reset_index(drop=True)
        reasons: list[list[str]] = [[] for _ in range(len(df))]

        self.check_nulls(df, reasons)
        self.check_duplicates(df, reasons)
        self.check_price_range(df, reasons)
        self.check_currency(df, reasons)

        df = df.copy()
        df["validation_reason"] = [
            "; ".join(r) if r else "" for r in reasons
        ]

        invalid_mask = df["validation_reason"] != ""
        df_invalid = df[invalid_mask].copy()
        df_valid = df[~invalid_mask].drop(columns=["validation_reason"]).copy()

        reasons_count: dict[str, int] = {}
        for reason_list in reasons:
            for reason in reason_list:
                key = reason.split(":")[0]
                reasons_count[key] = reasons_count.get(key, 0) + 1

        summary = ValidationSummary(
            total_rows=len(df),
            valid_rows=len(df_valid),
            invalid_rows=len(df_invalid),
            reasons_count=reasons_count,
        )

        logger.info(
            "Validation Success | valid=%d | invalid=%d | reasons=%s",
            summary.valid_rows,
            summary.invalid_rows,
            summary.reasons_count,
        )

        return df_valid, df_invalid, summary

    def save_invalid_rows(self, df_invalid: pd.DataFrame) -> Path | None:
        """Menyimpan baris yang gagal validasi ke logs/error/.

        Args:
            df_invalid: DataFrame baris invalid beserta kolom `validation_reason`.

        Returns:
            Path | None: lokasi file CSV error, atau None jika tidak ada
            baris invalid (tidak membuat file kosong).
        """
        if df_invalid.empty:
            return None

        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        file_path = self.error_dir / f"commodity_errors_{timestamp}.csv"
        df_invalid.to_csv(file_path, index=False, encoding="utf-8")

        logger.warning(
            "Invalid rows disimpan | path=%s | rows=%d",
            file_path,
            len(df_invalid),
        )
        return file_path


def run_validate(df_clean: pd.DataFrame) -> pd.DataFrame:
    """Entry point tahap validate: validasi -> simpan error -> return valid.

    Dipanggil oleh orchestrator pipeline utama (main.py) dengan DataFrame
    bersih dari `run_transform()` di `transform.py`.

    Args:
        df_clean: DataFrame hasil cleaning.

    Returns:
        pd.DataFrame: hanya baris yang VALID, siap diteruskan ke `load.py`.

    Raises:
        SchemaValidationError: jika kolom wajib hilang dari data.
    """
    validator = CommodityValidator()
    df_valid, df_invalid, summary = validator.validate(df_clean)
    validator.save_invalid_rows(df_invalid)

    return df_valid


if __name__ == "__main__":
    # Contoh manual run: ambil file processed TERBARU di data/processed/
    processed_files = sorted(
        Config.DATA_PROCESSED_DIR.glob("commodity_*.csv")
    )
    if not processed_files:
        logger.error(
            "Tidak ada file processed ditemukan di %s",
            Config.DATA_PROCESSED_DIR,
        )
    else:
        latest_processed = processed_files[-1]
        df_input = pd.read_csv(latest_processed, parse_dates=["scraped_at"])
        run_validate(df_input)