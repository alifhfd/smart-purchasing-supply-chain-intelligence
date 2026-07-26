"""
extract.py
==========
Tahap: RAW LAYER (Extract)

Alur: Internet -> Web Scraping -> Raw Data

Tanggung jawab modul ini HANYA:
1. Mengambil HTML tabel dari sumber (investing.com) via Playwright.
2. Mem-parsing struktur tabel menjadi list of dict (parsing struktural,
   BUKAN cleaning data — nilai string tetap mentah/kotor apa adanya).
3. Menyimpan hasil scraping asli ke:
   - CSV: data/raw/commodity_<timestamp>.csv
   - SQL Server: tabel `scrap_raw` (append only)

DILARANG melakukan di file ini:
- remove duplicate
- convert tipe data
- normalisasi nama
- validasi apa pun

Semua itu adalah tanggung jawab `transform.py` dan `validate.py`.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

import pandas as pd
from playwright.sync_api import Browser, Page, sync_playwright
from sqlalchemy import create_engine
from sqlalchemy.engine import Engine

from config.config import Config
from src.utils.logger import get_logger

logger = get_logger(__name__, Config.LOG_DIR)


@dataclass
class ScrapingResult:
    """Struktur hasil scraping mentah.

    Attributes:
        rows: List baris data mentah (tiap baris = dict kolom -> nilai string).
        scraped_at: Timestamp saat scraping dieksekusi.
        source_url: URL sumber data.
        row_count: Jumlah baris yang berhasil diambil.
    """

    rows: list[dict[str, Any]] = field(default_factory=list)
    scraped_at: datetime = field(default_factory=datetime.now)
    source_url: str = ""
    row_count: int = 0


class CommodityScraper:
    """Scraper untuk data harga komoditas real-time dari investing.com.

    Class ini didesain OOP supaya:
    - browser/page lifecycle terkelola rapi (open -> scrape -> close)
    - mudah diperluas ke sumber lain di masa depan tanpa mengubah
      kontrak (interface) yang dipakai oleh tahap ETL berikutnya
    """

    def __init__(
        self,
        url: str = Config.SCRAPING_URL,
        timeout_ms: int = Config.SCRAPING_TIMEOUT_MS,
        user_agent: str = Config.SCRAPING_USER_AGENT,
        table_selector: str = Config.TABLE_SELECTOR,
    ) -> None:
        """Inisialisasi scraper dengan parameter target.

        Args:
            url: URL halaman yang akan di-scrape.
            timeout_ms: Batas waktu tunggu navigasi/elemen (milidetik).
            user_agent: User-Agent string yang dipakai browser headless.
            table_selector: CSS selector partial-match untuk tabel target.
        """
        self.url = url
        self.timeout_ms = timeout_ms
        self.user_agent = user_agent
        self.table_selector = table_selector

    def _extract_table_rows(self, page: Page) -> list[dict[str, Any]]:
        """Mem-parsing baris tabel dari halaman yang sudah dimuat.

        Args:
            page: Objek Page Playwright yang sudah selesai `goto()`.

        Returns:
            list[dict]: baris data mentah, kolom masih dalam bahasa
            sumber asli (Nama, Bulan, Terakhir, dst) dan format string asli.
        """
        table = page.locator(self.table_selector).first
        table.wait_for(state="visible", timeout=self.timeout_ms)

        rows_locator = table.locator("tbody tr")
        row_count = rows_locator.count()

        raw_rows: list[dict[str, Any]] = []
        for i in range(row_count):
            row = rows_locator.nth(i)
            cells = row.locator("td")
            if cells.count() < 7:
                # baris tidak lengkap (misal header tersembunyi) -> skip
                continue

            raw_rows.append(
                {
                    "nama": cells.nth(1).inner_text(),
                    "bulan": cells.nth(2).inner_text(),
                    "terakhir": cells.nth(3).inner_text(),
                    "tertinggi": cells.nth(4).inner_text(),
                    "terendah": cells.nth(5).inner_text(),
                    "perubahan": cells.nth(6).inner_text(),
                    "perubahan_persen": (
                        cells.nth(7).inner_text() if cells.count() > 7 else None
                    ),
                }
            )
        return raw_rows

    def scrape(self) -> ScrapingResult:
        """Menjalankan satu siklus scraping penuh (buka browser -> ambil -> tutup).

        Returns:
            ScrapingResult: hasil scraping mentah beserta metadata.

        Raises:
            RuntimeError: jika scraping gagal total (elemen tidak ditemukan,
                timeout, atau browser crash).
        """
        logger.info("Scraping Started | url=%s", self.url)
        start_time = time.perf_counter()

        browser: Browser | None = None
        try:
            with sync_playwright() as p:
                browser = p.chromium.launch(headless=True)
                page = browser.new_page(user_agent=self.user_agent)
                page.goto(
                    self.url,
                    wait_until="domcontentloaded",
                    timeout=self.timeout_ms,
                )

                raw_rows = self._extract_table_rows(page)
                browser.close()

            elapsed = time.perf_counter() - start_time
            logger.info(
                "Scraping Success | rows=%d | elapsed=%.2fs",
                len(raw_rows),
                elapsed,
            )

            return ScrapingResult(
                rows=raw_rows,
                scraped_at=datetime.now(),
                source_url=self.url,
                row_count=len(raw_rows),
            )

        except Exception as exc:
            elapsed = time.perf_counter() - start_time
            logger.error(
                "Scraping Failed | elapsed=%.2fs | error=%s", elapsed, exc
            )
            if browser is not None:
                try:
                    browser.close()
                except Exception:
                    pass
            raise RuntimeError(f"Scraping gagal: {exc}") from exc


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
