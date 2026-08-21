from __future__ import annotations

import os
import threading
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path


UPLOAD_DIR = Path(os.getenv("UPLOAD_DIR", "./uploads"))
UPLOAD_TTL_HOURS = int(os.getenv("UPLOAD_TTL_HOURS", "24"))
CLEANUP_INTERVAL_SECONDS = int(os.getenv("UPLOAD_CLEANUP_INTERVAL_SECONDS", str(60 * 60)))


def cleanup_expired_uploads() -> None:
    if not UPLOAD_DIR.exists():
        return

    cutoff = datetime.now(timezone.utc) - timedelta(hours=UPLOAD_TTL_HOURS)
    for path in UPLOAD_DIR.glob("**/*"):
        if not path.is_file():
            continue
        modified_at = datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc)
        if modified_at < cutoff:
            try:
                path.unlink()
            except OSError:
                continue


def start_cleanup_worker() -> None:
    def worker() -> None:
        while True:
            cleanup_expired_uploads()
            time.sleep(CLEANUP_INTERVAL_SECONDS)

    thread = threading.Thread(target=worker, daemon=True, name="sachlens-upload-cleanup")
    thread.start()
