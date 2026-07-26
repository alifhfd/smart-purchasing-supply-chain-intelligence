"""
logger.py
=========
Modul logging terpusat untuk seluruh pipeline ETL.

Setiap tahap pipeline (extract, transform, validate, load, warehouse)
menggunakan logger yang sama formatnya, sehingga log bisa ditelusuri
lintas-tahap dengan konsisten.

Log ditulis ke dua tempat:
1. Console (stdout) — untuk monitoring saat development
2. File harian di `logs/pipeline_YYYYMMDD.log` — untuk audit trail
"""

from __future__ import annotations

import logging
import sys
from datetime import datetime
from pathlib import Path


def get_logger(name: str, log_dir: Path) -> logging.Logger:
    """Membuat/mengambil logger dengan konfigurasi standar pipeline.

    Args:
        name: Nama logger, biasanya `__name__` dari modul pemanggil.
        log_dir: Direktori tempat file log harian disimpan.

    Returns:
        logging.Logger: instance logger yang siap dipakai.
    """
    log_dir.mkdir(parents=True, exist_ok=True)
    log_file = log_dir / f"pipeline_{datetime.now().strftime('%Y%m%d')}.log"

    logger = logging.getLogger(name)
    logger.setLevel(logging.INFO)

    # Hindari duplikasi handler kalau get_logger dipanggil berkali-kali
    if not logger.handlers:
        formatter = logging.Formatter(
            fmt="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
        )

        console_handler = logging.StreamHandler(sys.stdout)
        console_handler.setFormatter(formatter)

        file_handler = logging.FileHandler(log_file, encoding="utf-8")
        file_handler.setFormatter(formatter)

        logger.addHandler(console_handler)
        logger.addHandler(file_handler)

    return logger
