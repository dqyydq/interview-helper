import argparse
import uuid

import anyio

from app.core.config import settings
from app.db.session import dispose_engine
from app.workers.context_summary_jobs import run_once as run_summary_once
from app.workers.plan_jobs import run_once as run_plan_once
from app.workers.resume_jobs import run_once as run_resume_once


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run Interview Helper background jobs")
    parser.add_argument("--once", action="store_true", help="Process at most one queued job")
    return parser.parse_args()


async def async_main(once: bool) -> None:
    worker_id = f"local-{uuid.uuid4().hex[:10]}"
    try:
        while True:
            processed = await run_summary_once(worker_id)
            if not processed:
                processed = await run_resume_once(worker_id)
            if not processed:
                processed = await run_plan_once(worker_id)
            if once:
                return
            if not processed:
                await anyio.sleep(settings.job_poll_interval_seconds)
    finally:
        await dispose_engine()


def main() -> None:
    args = parse_args()
    anyio.run(async_main, args.once)


if __name__ == "__main__":
    main()
