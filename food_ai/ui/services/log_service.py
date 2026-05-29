from __future__ import annotations

from datetime import datetime, timezone
import logging
from pathlib import Path
from typing import Any
from uuid import uuid4

from food_ai.ui.utils.file_io import copy_local_image, ensure_directory, write_json_file


logger = logging.getLogger(__name__)


def save_test_log(
    image_path: str | Path,
    inference_result: dict[str, Any],
    notes: str,
    review_status: str,
    logs_dir: str | Path,
) -> Path:
    log_root = ensure_directory(logs_dir)
    records_dir = ensure_directory(log_root / "records")
    images_dir = ensure_directory(log_root / "images")

    test_id = uuid4().hex
    copied_image_path = copy_local_image(image_path, images_dir)
    record = {
        "test_id": test_id,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "image_path": str(copied_image_path),
        "notes": notes.strip(),
        "review_status": review_status,
        "inference_result": inference_result,
    }
    record_path = write_json_file(records_dir / f"{test_id}.json", record)
    logger.info("Saved UI test log to %s", record_path)
    return record_path
