"""
extract.py
==========
Tahap: RAW LAYER (Extract) — khusus domain commodity.

Alur: Internet -> Web Scraping -> Raw Data

Modul ini adalah ORKESTRATOR, bukan pemilik logic scraping. Logic
scraping sesungguhnya ada di `src/scraper/commodity_scraper.py`
(mewarisi `BaseScraper`). Tanggung jawab modul ini HANYA:
1. Memanggil `CommodityScraper.scrape()` untuk mendapatkan data mentah.
2. Menyimpan hasil scraping asli ke:
   - CSV: data/raw/commodity_<timestamp>.csv
   - SQL Server: tabel `scrap_raw` (append only)

DILARANG melakukan di file ini:
- remove duplicate
- convert tipe data
- normalisasi nama
- validasi apa pun
- logic parsing HTML (itu tanggung jawab scraper, bukan orkestrator ETL)

Semua itu adalah tanggung jawab `src/scraper/*` (parsing) dan
`transform.py` / `validate.py` (cleaning & validasi).
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd
from sqlalchemy import create_engine
from sqlalchemy.engine import Engine

from config.config import Config
from src.scraper.base_scraper import ScrapingResult
from src.scraper.commodity_scraper import CommodityScraper
from src.utils.logger import get_logger

logger = get_logger(__name__, Config.LOG_DIR)


class RawDataWriter:
    """Menyimpan hasil scraping mentah ke CSV dan SQL Server.

    Class ini murni I/O — tidak boleh mengubah nilai data sama sekali.
    """

    def __init__(self, raw_dir: Path = Config.DATA_RAW_DIR) -> None:
        """Inisialisasi writer.

        Args:
            raw_dir: Direktori tujuan penyimpanan CSV raw.
        """
        self.raw_dir = raw_dir
        self.raw_dir.mkdir(parents=True, exist_ok=True)

    def _build_dataframe(self, result: ScrapingResult) -> pd.DataFrame:
        """Mengubah ScrapingResult menjadi DataFrame tanpa transformasi nilai.

        Args:
            result: Hasil scraping mentah.

        Returns:
            pd.DataFrame: data mentah + kolom metadata (scraped_at, source_url).
        """
        df = pd.DataFrame(result.rows)
        df["scraped_at"] = result.scraped_at
        df["source_url"] = result.source_url
        return df

    def save_to_csv(self, result: ScrapingResult) -> Path:
        """Menyimpan hasil scraping mentah ke file CSV bertimestamp.

        Nama file mengikuti format: commodity_YYYYMMDD_HHMMSS.csv

        Args:
            result: Hasil scraping mentah.

        Returns:
            Path: lokasi file CSV yang baru dibuat.
        """
        timestamp = result.scraped_at.strftime("%Y%m%d_%H%M%S")
        file_path = self.raw_dir / f"commodity_{timestamp}.csv"

        df = self._build_dataframe(result)
        df.to_csv(file_path, index=False, encoding="utf-8")

        logger.info("Raw CSV Saved | path=%s | rows=%d", file_path, len(df))
        return file_path

    def save_to_sql(
        self, result: ScrapingResult, engine: Engine | None = None
    ) -> None:
        """Menyimpan hasil scraping mentah ke tabel SQL Server `scrap_raw`.

        Data selalu di-APPEND, tidak pernah replace/overwrite, karena
        tabel raw adalah audit trail dari setiap eksekusi scraping.

        Args:
            result: Hasil scraping mentah.
            engine: SQLAlchemy engine opsional (untuk keperluan testing/DI).
                Jika None, engine dibuat dari `Config.get_sqlalchemy_uri()`.
        """
        df = self._build_dataframe(result)
        active_engine = engine or create_engine(Config.get_sqlalchemy_uri())

        try:
            df.to_sql(
                Config.TABLE_RAW,
                con=active_engine,
                if_exists="append",
                index=False,
                chunksize=500,  # batch insert, hindari row-by-row
            )
            logger.info(
                "Raw SQL Insert Success | table=%s | rows=%d",
                Config.TABLE_RAW,
                len(df),
            )
        except Exception as exc:
            logger.error(
                "Raw SQL Insert Failed | table=%s | error=%s",
                Config.TABLE_RAW,
                exc,
            )
            raise


def run_extract() -> Path:
    """Entry point tahap extract: scrape -> simpan CSV -> simpan SQL.

    Fungsi ini yang dipanggil oleh orchestrator pipeline utama (main.py).

    Returns:
        Path: lokasi file CSV raw yang dihasilkan, dipakai sebagai input
        eksplisit untuk tahap `transform.py` berikutnya.
    """
    scraper = CommodityScraper()
    result = scraper.scrape()

    writer = RawDataWriter()
    csv_path = writer.save_to_csv(result)

    try:
        writer.save_to_sql(result)
    except Exception:
        # Kegagalan simpan ke SQL tidak menghentikan pipeline raw layer,
        # karena CSV sudah tersimpan sebagai sumber kebenaran (source of truth).
        # Kegagalan tetap tercatat di log untuk ditindaklanjuti.
        logger.warning(
            "Raw data hanya tersimpan di CSV, gagal tersimpan ke SQL Server."
        )

    return csv_path


if __name__ == "__main__":
    run_extract()