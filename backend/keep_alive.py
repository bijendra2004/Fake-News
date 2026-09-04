from __future__ import annotations

import asyncio
import logging
import os
import urllib.error
import urllib.request

logger = logging.getLogger("sachlens.keep_alive")


async def keep_alive_loop(interval_seconds: int = 600) -> None:
    """Periodically ping own health endpoint to keep server awake on free tier hosting."""
    # Grace period on startup before the first ping
    await asyncio.sleep(20)

    while True:
        raw_url = (
            os.getenv("RENDER_EXTERNAL_URL")
            or os.getenv("BACKEND_URL")
            or "https://fake-news-cvzg.onrender.com"
        )
        base_url = raw_url.strip().rstrip("/")
        ping_url = f"{base_url}/health"

        try:
            req = urllib.request.Request(
                ping_url,
                headers={"User-Agent": "SachLens-KeepAlive/1.0"},
                method="GET",
            )

            def _send_ping() -> int:
                with urllib.request.urlopen(req, timeout=10) as resp:
                    return resp.status

            status_code = await asyncio.to_thread(_send_ping)
            logger.info("[Keep-Alive] Ping sent successfully to %s: %d OK", ping_url, status_code)
        except urllib.error.HTTPError as exc:
            logger.warning("[Keep-Alive] Ping to %s returned HTTP %d: %s", ping_url, exc.code, exc.reason)
        except Exception as exc:
            logger.warning("[Keep-Alive] Ping to %s encountered error (non-fatal): %s", ping_url, exc)

        await asyncio.sleep(interval_seconds)


def start_keep_alive_worker(interval_seconds: int = 600) -> asyncio.Task:
    """Spawn the self-ping background task."""
    try:
        loop = asyncio.get_running_loop()
        task = loop.create_task(keep_alive_loop(interval_seconds))
    except RuntimeError:
        loop = asyncio.get_event_loop()
        task = loop.create_task(keep_alive_loop(interval_seconds))
    logger.info("[Keep-Alive] Background self-ping worker registered (interval: %ds)", interval_seconds)
    return task
