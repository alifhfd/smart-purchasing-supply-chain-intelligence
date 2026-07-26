"""
base_scraper.py
================
Fondasi (abstract base class) untuk seluruh scraper di project ini
(commodity, currency, weather, holiday, trade, shipping, vessel, dst).

Prinsip desain: Template Method Pattern.
- `BaseScraper.scrape()` mengatur ALUR UMUM yang sama untuk semua domain:
  buka browser -> goto url -> tunggu selector -> parsing -> tutup browser
  -> logging -> error handling.
- Method `_parse()` WAJIB diimplementasikan oleh tiap scraper turunan,
  karena struktur HTML/parsing tiap sumber pasti berbeda.

Dengan pola ini, menambah scraper domain baru (misal WeatherScraper)
TIDAK PERLU menulis ulang logic buka-tutup browser, retry, atau logging
— cukup extend class ini dan isi `_parse()` + konfigurasi URL/selector.
"""

from __future__ import annotations

import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from playwright.sync_api import Browser, Page, sync_playwright

from src.utils.logger import get_logger
from config.config import Config

logger = get_logger(__name__, Config.LOG_DIR)


@dataclass
class ScrapingResult:
    """Struktur hasil scraping mentah, dipakai oleh SEMUA domain.

    Attributes:
        rows: List baris data mentah (dict kolom -> nilai string, apa adanya).
        scraped_at: Timestamp saat scraping dieksekusi.
        source_url: URL sumber data.
        source_name: Nama domain sumber (misal "commodity", "weather").
        row_count: Jumlah baris yang berhasil diambil.
    """

    rows: list[dict[str, Any]] = field(default_factory=list)
    scraped_at: datetime = field(default_factory=datetime.now)
    source_url: str = ""
    source_name: str = ""
    row_count: int = 0


class BaseScraper(ABC):
    """Class dasar abstrak untuk seluruh scraper berbasis Playwright.

    Setiap scraper domain (commodity, currency, weather, dst) HARUS
    mewarisi class ini dan mengimplementasikan:
    - `source_name` (property): nama domain, dipakai untuk logging & path.
    - `_parse(page)`: logic ekstraksi data spesifik domain tersebut.

    Contoh pemakaian oleh subclass:

        class WeatherScraper(BaseScraper):
            source_name = "weather"

            def __init__(self):
                super().__init__(
                    url="https://contoh-sumber-cuaca.com",
                    table_selector="table.forecast",
                )

            def _parse(self, page: Page) -> list[dict]:
                # logic parsing khusus halaman cuaca
                ...
    """

    #: Nama domain sumber data, WAJIB di-override oleh subclass.
    #: Dipakai untuk logging dan penamaan folder/file (data/raw/<source_name>/).
    source_name: str = "base"

    def __init__(
        self,
        url: str,
        timeout_ms: int = Config.SCRAPING_TIMEOUT_MS,
        user_agent: str = Config.SCRAPING_USER_AGENT,
        table_selector: str | None = None,
        max_retries: int = 2,
    ) -> None:
        """Inisialisasi scraper dengan parameter target.

        Args:
            url: URL halaman yang akan di-scrape.
            timeout_ms: Batas waktu tunggu navigasi/elemen (milidetik).
            user_agent: User-Agent string yang dipakai browser headless.
            table_selector: CSS selector partial-match elemen target.
                Boleh None jika subclass mengambil elemen dengan cara lain
                (misal beberapa selector berbeda dalam satu `_parse`).
            max_retries: Jumlah percobaan ulang jika scraping gagal
                (di luar kegagalan navigasi timeout pertama).
        """
        self.url = url
        self.timeout_ms = timeout_ms
        self.user_agent = user_agent
        self.table_selector = table_selector
        self.max_retries = max_retries

    @abstractmethod
    def _parse(self, page: Page) -> list[dict[str, Any]]:
        """Mem-parsing elemen halaman menjadi list baris data mentah.

        WAJIB diimplementasikan oleh tiap scraper turunan. Method ini
        HANYA boleh melakukan parsing struktural (ambil teks elemen),
        TIDAK boleh melakukan cleaning/konversi tipe data — itu tanggung
        jawab tahap `transform.py`.

        Args:
            page: Objek Page Playwright yang sudah selesai `goto()`.

        Returns:
            list[dict]: baris data mentah, kolom dalam bahasa/format sumber asli.
        """
        raise NotImplementedError

    def _wait_for_target(self, page: Page) -> None:
        """Menunggu elemen target muncul sebelum parsing dimulai.

        Default implementation menunggu `table_selector` visible. Override
        method ini di subclass jika domain tersebut butuh strategi tunggu
        yang berbeda (misal menunggu network response API, bukan elemen).

        Args:
            page: Objek Page Playwright yang sudah selesai `goto()`.
        """
        if self.table_selector:
            page.locator(self.table_selector).first.wait_for(
                state="visible", timeout=self.timeout_ms
            )

    def scrape(self) -> ScrapingResult:
        """Menjalankan satu siklus scraping penuh, dengan retry otomatis.

        Alur: buka browser -> goto -> tunggu target -> parsing -> tutup browser.
        Alur ini SAMA untuk semua domain; yang beda hanya isi `_parse()`.

        Returns:
            ScrapingResult: hasil scraping mentah beserta metadata.

        Raises:
            RuntimeError: jika semua percobaan (termasuk retry) gagal.
        """
        last_error: Exception | None = None

        for attempt in range(1, self.max_retries + 2):  # percobaan pertama + retry
            logger.info(
                "Scraping Started | source=%s | url=%s | attempt=%d",
                self.source_name,
                self.url,
                attempt,
            )
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

                    self._wait_for_target(page)
                    raw_rows = self._parse(page)
                    browser.close()

                elapsed = time.perf_counter() - start_time
                logger.info(
                    "Scraping Success | source=%s | rows=%d | elapsed=%.2fs",
                    self.source_name,
                    len(raw_rows),
                    elapsed,
                )

                return ScrapingResult(
                    rows=raw_rows,
                    scraped_at=datetime.now(),
                    source_url=self.url,
                    source_name=self.source_name,
                    row_count=len(raw_rows),
                )

            except Exception as exc:
                elapsed = time.perf_counter() - start_time
                last_error = exc
                logger.warning(
                    "Scraping Attempt Failed | source=%s | attempt=%d | "
                    "elapsed=%.2fs | error=%s",
                    self.source_name,
                    attempt,
                    elapsed,
                    exc,
                )
                if browser is not None:
                    try:
                        browser.close()
                    except Exception:
                        pass

        logger.error(
            "Scraping Failed Permanently | source=%s | after %d attempts | error=%s",
            self.source_name,
            self.max_retries + 1,
            last_error,
        )
        raise RuntimeError(
            f"Scraping gagal untuk source='{self.source_name}' "
            f"setelah {self.max_retries + 1} percobaan: {last_error}"
        )