"""
commodity_scraper.py
=====================
Scraper untuk data harga komoditas real-time dari investing.com.

Mewarisi `BaseScraper` — hanya mengimplementasikan hal yang spesifik
untuk domain commodity: URL sumber, selector tabel, dan logic parsing
kolom-kolom tabel investing.com.

Alur umum (buka browser, retry, logging, error handling) sudah
ditangani sepenuhnya oleh `BaseScraper.scrape()`.
"""

from __future__ import annotations

from typing import Any

from playwright.sync_api import Page

from config.config import Config
from src.scraper.base_scraper import BaseScraper


class CommodityScraper(BaseScraper):
    """Scraper harga komoditas real-time (investing.com).

    Contoh pemakaian:
        scraper = CommodityScraper()
        result = scraper.scrape()  # -> ScrapingResult
    """

    #: Nama domain, dipakai untuk logging dan penamaan folder data/raw/commodity/
    source_name: str = "commodity"

    def __init__(
        self,
        url: str = Config.SCRAPING_URL,
        timeout_ms: int = Config.SCRAPING_TIMEOUT_MS,
        user_agent: str = Config.SCRAPING_USER_AGENT,
        table_selector: str = Config.TABLE_SELECTOR,
    ) -> None:
        """Inisialisasi scraper commodity dengan parameter target.

        Args:
            url: URL halaman commodity yang akan di-scrape.
            timeout_ms: Batas waktu tunggu navigasi/elemen (milidetik).
            user_agent: User-Agent string yang dipakai browser headless.
            table_selector: CSS selector partial-match untuk tabel target.
        """
        super().__init__(
            url=url,
            timeout_ms=timeout_ms,
            user_agent=user_agent,
            table_selector=table_selector,
        )

    def _parse(self, page: Page) -> list[dict[str, Any]]:
        """Mem-parsing baris tabel commodity dari halaman yang sudah dimuat.

        Args:
            page: Objek Page Playwright yang sudah selesai `goto()`.

        Returns:
            list[dict]: baris data mentah, kolom masih dalam bahasa
            sumber asli (nama, bulan, terakhir, dst) dan format string asli.
        """
        table = page.locator(self.table_selector).first
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