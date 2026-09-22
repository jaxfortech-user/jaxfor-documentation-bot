"""
main.py
--------
App entry point for Railway. Runs a lightweight FastAPI app (so we
have a health-check endpoint Railway can ping) plus a background
scheduler that polls the watched Drive folder every POLL_INTERVAL_SECONDS
and kicks off the pipeline whenever a new invoice shows up.
"""

from __future__ import annotations

import logging
import os

from apscheduler.schedulers.background import BackgroundScheduler
from dotenv import load_dotenv
from fastapi import FastAPI

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("jaxfor.main")

from app.pipeline import poll_and_process  # noqa: E402  (after load_dotenv)

app = FastAPI(title="jaxfor-documentation")

POLL_INTERVAL_SECONDS = int(os.environ.get("POLL_INTERVAL_SECONDS", "60"))

scheduler = BackgroundScheduler()


@app.on_event("startup")
def start_scheduler():
    scheduler.add_job(
        poll_and_process,
        "interval",
        seconds=POLL_INTERVAL_SECONDS,
        id="poll_drive_folder",
        max_instances=1,  # don't overlap runs if one poll takes longer than the interval
        next_run_time=None,  # first run scheduled below, not immediately at import time
    )
    scheduler.start()
    logger.info("Scheduler started — polling every %d seconds", POLL_INTERVAL_SECONDS)


@app.on_event("shutdown")
def stop_scheduler():
    scheduler.shutdown(wait=False)


@app.get("/")
def health_check():
    return {"status": "ok", "service": "jaxfor-documentation"}


@app.post("/poll-now")
def trigger_poll_now():
    """Manual trigger — useful for testing without waiting for the interval."""
    poll_and_process()
    return {"status": "poll triggered"}