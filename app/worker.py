"""Background worker — `python -m app.worker`.

Polls the job queue and processes items with retries/backoff. Runs as its own
compose service so slow, bulk AI work never blocks API requests.
"""

from __future__ import annotations

import logging
import time

from app.config import get_settings
from app.db.session import get_session_factory
from app.providers.base import get_provider
from app.services import jobs

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
log = logging.getLogger("worker")


def main() -> None:
    settings = get_settings()
    provider = get_provider(settings)
    session_factory = get_session_factory()
    log.info("worker started (poll=%.1fs)", settings.worker_poll_interval_seconds)
    try:
        while True:
            with session_factory() as session:
                did_work = jobs.run_next_item(session, provider, settings)
            if not did_work:
                time.sleep(settings.worker_poll_interval_seconds)
    except KeyboardInterrupt:
        log.info("worker stopped")


if __name__ == "__main__":
    main()
