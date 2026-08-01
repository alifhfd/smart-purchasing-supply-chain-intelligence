"""
main.py
=======
Orchestrator utama pipeline ETL Smart Purchasing & Supply Chain
Intelligence Platform.

Alur yang dijalankan (domain commodity):

    Extract -> Transform -> Validate -> Load (Staging)

Setiap tahap dipanggil lewat entry point resminya masing-masing
(`run_extract`, `run_transform`, `run_validate`, `run_load`), sehingga
file ini TIDAK berisi logic bisnis apa pun — murni orkestrasi & logging
level pipeline.

Cara menjalankan (dari root project):

    python main.py
"""

from __future__ import annotations

import time

from config.config import Config
from src.etl.extract import run_extract
from src.etl.load import run_load
from src.etl.transform import run_transform
from src.etl.validate import run_validate
from src.utils.logger import get_logger

logger = get_logger(__name__, Config.LOG_DIR)


def run_pipeline() -> None:
    """Menjalankan seluruh pipeline commodity secara berurutan.

    Kalau ada tahap yang gagal (exception apa pun), pipeline berhenti
    di tahap tersebut — tahap berikutnya TIDAK dijalankan dengan data
    yang berpotensi rusak/tidak lengkap.

    Raises:
        Exception: error asli dari tahap yang gagal, dilempar ulang
            setelah dicatat ke log (supaya penyebab kegagalan jelas
            di file log, bukan cuma di traceback console).
    """
    pipeline_start = time.perf_counter()
    logger.info("Pipeline Started | domain=commodity")

    try:
        raw_csv_path = run_extract()
        df_clean, _processed_path = run_transform(raw_csv_path)
        df_valid = run_validate(df_clean)
        run_load(df_valid)

        elapsed = time.perf_counter() - pipeline_start
        logger.info(
            "Pipeline Success | domain=commodity | Elapsed Time=%.2fs",
            elapsed,
        )

    except Exception as exc:
        elapsed = time.perf_counter() - pipeline_start
        logger.error(
            "Pipeline Failed | domain=commodity | Elapsed Time=%.2fs | error=%s",
            elapsed,
            exc,
        )
        raise


if __name__ == "__main__":
    run_pipeline()