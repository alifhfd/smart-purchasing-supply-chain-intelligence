"""
load.py
=======
Tahap: STAGING LAYER (Load)

Alur: Valid Data -> Staging Database (stg_commodity_price)

Tanggung jawab modul ini:
1. Meng-TRUNCATE tabel staging (karena staging bersifat transient,
   bukan histori permanen — histori ada di commodity_history/fact_*).
2. Melakukan BATCH INSERT data yang sudah valid ke `stg_commodity_price`
   menggunakan SQLAlchemy + pandas.to_sql (chunksize), BUKAN INSERT
   satu-satu.

DILARANG melakukan di file ini:
- cleaning/validasi ulang -> itu tanggung jawab transform.py & validate.py
- feature engineering -> tahap terpisah setelah staging
"""

from __future__ import annotations

import pandas as pd
from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine

from config.config import Config
from src.utils.logger import get_logger

logger = get_logger(__name__, Config.LOG_DIR)


class StagingLoader:
    """Memuat data valid ke tabel staging SQL Server via batch insert."""

    def __init__(self, engine: Engine | None = None) -> None:
        """Inisialisasi loader.

        Args:
            engine: SQLAlchemy engine opsional (untuk keperluan testing/DI).
                Jika None, engine dibuat dari `Config.get_sqlalchemy_uri()`.
        """
        self.engine = engine or create_engine(Config.get_sqlalchemy_uri())

    def truncate_staging_table(self) -> None:
        """Mengosongkan tabel staging sebelum diisi batch data terbaru.

        Staging bersifat transient — TRUNCATE aman dilakukan di sini karena
        data permanen sudah tersimpan di layer sebelumnya (raw) dan akan
        tersimpan lagi di layer sesudahnya (warehouse/history).
        """
        with self.engine.begin() as conn:
            conn.execute(text(f"TRUNCATE TABLE {Config.TABLE_STAGING}"))
        logger.info("Staging table di-TRUNCATE | table=%s", Config.TABLE_STAGING)

    def batch_insert(self, df: pd.DataFrame, chunksize: int = 500) -> None:
        """Melakukan batch insert DataFrame ke tabel staging.

        Args:
            df: DataFrame berisi data yang sudah valid (dari validate.py).
            chunksize: Jumlah baris per batch insert (hindari row-by-row).

        Raises:
            Exception: jika insert gagal, error asli dilempar ulang setelah
                dicatat ke log (supaya orchestrator tahu staging gagal).
        """
        try:
            df.to_sql(
                Config.TABLE_STAGING,
                con=self.engine,
                if_exists="append",
                index=False,
                chunksize=chunksize,
            )
            logger.info(
                "Insert Success | table=%s | rows=%d | chunksize=%d",
                Config.TABLE_STAGING,
                len(df),
                chunksize,
            )
        except Exception as exc:
            logger.error(
                "Insert Failed | table=%s | error=%s",
                Config.TABLE_STAGING,
                exc,
            )
            raise

    def load(self, df: pd.DataFrame) -> None:
        """Menjalankan alur staging lengkap: truncate -> batch insert.

        Args:
            df: DataFrame berisi data yang sudah valid.
        """
        if df.empty:
            logger.warning(
                "Load dilewati | tidak ada baris valid untuk dimuat ke staging"
            )
            return

        self.truncate_staging_table()
        self.batch_insert(df)


def run_load(df_valid: pd.DataFrame) -> None:
    """Entry point tahap load: memuat data valid ke staging.

    Dipanggil oleh orchestrator pipeline utama (main.py) dengan DataFrame
    valid dari `run_validate()` di `validate.py`.

    Args:
        df_valid: DataFrame berisi hanya baris yang lolos validasi.
    """
    loader = StagingLoader()
    loader.load(df_valid)


if __name__ == "__main__":
    # Contoh manual run: ambil file processed TERBARU, validasi, lalu load
    from src.etl.validate import run_validate

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
        df_valid = run_validate(df_input)
        run_load(df_valid)