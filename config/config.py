"""
config.py
=========
Konfigurasi terpusat untuk seluruh pipeline ETL.

Semua nilai sensitif (connection string, credential) TIDAK boleh
di-hardcode di sini — nilai tersebut diambil dari environment
variable (.env) menggunakan python-dotenv.

File ini hanya boleh berisi:
- konstanta konfigurasi (path, URL, nama tabel)
- pembacaan environment variable
- TIDAK boleh berisi logika bisnis apa pun
"""

from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

# Load .env dari root project
BASE_DIR: Path = Path(__file__).resolve().parent.parent
load_dotenv(BASE_DIR / ".env")


class Config:
    """Wadah konfigurasi statis untuk seluruh pipeline."""

    # === Path Project ===
    BASE_DIR: Path = BASE_DIR
    DATA_RAW_DIR: Path = BASE_DIR / "data" / "raw"
    DATA_PROCESSED_DIR: Path = BASE_DIR / "data" / "processed"
    DATA_WAREHOUSE_DIR: Path = BASE_DIR / "data" / "warehouse"
    LOG_DIR: Path = BASE_DIR / "logs"
    LOG_ERROR_DIR: Path = BASE_DIR / "logs" / "error"

    # === Scraping Target ===
    SCRAPING_URL: str = os.getenv(
        "SCRAPING_URL",
        "https://id.investing.com/commodities/real-time-futures",
    )
    SCRAPING_TIMEOUT_MS: int = int(os.getenv("SCRAPING_TIMEOUT_MS", "30000"))
    SCRAPING_USER_AGENT: str = os.getenv(
        "SCRAPING_USER_AGENT",
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    )
    TABLE_SELECTOR: str = os.getenv(
        "TABLE_SELECTOR", 'table[class*="dynamic-table"]'
    )

    # === Database (SQL Server) ===
    DB_SERVER: str = os.getenv("DB_SERVER", "localhost")
    DB_NAME: str = os.getenv("DB_NAME", "raw_projects")
    DB_USER: str = os.getenv("DB_USER", "")
    DB_PASSWORD: str = os.getenv("DB_PASSWORD", "")
    DB_DRIVER: str = os.getenv("DB_DRIVER", "ODBC Driver 17 for SQL Server")
    # True  -> pakai Windows Authentication (Trusted_Connection), abaikan DB_USER/DB_PASSWORD
    # False -> pakai SQL Server Authentication, wajib isi DB_USER & DB_PASSWORD
    DB_TRUSTED_CONNECTION: bool = os.getenv(
        "DB_TRUSTED_CONNECTION", "yes"
    ).strip().lower() in ("yes", "true", "1")

    # === Nama Tabel per Layer ===
    TABLE_RAW: str = "scrap_raw"
    TABLE_STAGING: str = "stg_commodity_price"
    TABLE_FACT: str = "fact_commodity_price"
    TABLE_DIM_DATE: str = "dim_date"
    TABLE_DIM_COUNTRY: str = "dim_country"
    TABLE_DIM_COMMODITY: str = "dim_commodity"
    TABLE_CURRENT: str = "commodity_current"
    TABLE_HISTORY: str = "commodity_history"

    @classmethod
    def get_sqlalchemy_uri(cls) -> str:
        """Membangun connection URI SQLAlchemy untuk SQL Server via pyodbc.

        Mendukung dua mode autentikasi, dikontrol lewat `DB_TRUSTED_CONNECTION`
        di `.env`:
        - Windows Authentication (Trusted_Connection=yes): tidak perlu
          DB_USER/DB_PASSWORD, login pakai akun Windows yang sedang aktif.
        - SQL Server Authentication: wajib DB_USER & DB_PASSWORD terisi.

        Returns:
            str: connection URI siap pakai oleh `create_engine`.
        """
        driver_encoded = cls.DB_DRIVER.replace(" ", "+")

        if cls.DB_TRUSTED_CONNECTION:
            return (
                f"mssql+pyodbc://@{cls.DB_SERVER}/{cls.DB_NAME}"
                f"?driver={driver_encoded}&trusted_connection=yes"
            )

        return (
            f"mssql+pyodbc://{cls.DB_USER}:{cls.DB_PASSWORD}"
            f"@{cls.DB_SERVER}/{cls.DB_NAME}?driver={driver_encoded}"
        )