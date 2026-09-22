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
from fastapi.middleware.cors import CORSMiddleware

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("jaxfor.main")

# Must run before anything imports app.drive / app.oauth_drive, both of
# which read credential files from disk — on Railway those files don't
# exist until this writes them out from env vars. No-op locally (the
# *_CONTENT env vars are only set on Railway). See app/bootstrap_credentials.py.
from app.bootstrap_credentials import bootstrap_credentials  # noqa: E402

bootstrap_credentials()

from app.pipeline import poll_and_process  # noqa: E402  (after load_dotenv + bootstrap_credentials)
from app.dashboard_api import router as dashboard_router  # noqa: E402

app = FastAPI(title="jaxfor-documentation")

# CORS: allow the Vercel-hosted dashboard (and local dev) to call
# these endpoints. DASHBOARD_ORIGIN should be the deployed Vercel URL,
# e.g. https://jaxfor-dashboard.vercel.app — set it in Railway's env
# vars once the frontend is deployed. A comma-separated list is also
# accepted if you need to allow more than one origin (e.g. a preview
# deployment URL alongside the production one).
_dashboard_origins = [
    o.strip() for o in os.environ.get("DASHBOARD_ORIGIN", "http://localhost:3000").split(",") if o.strip()
]
app.add_middleware(
    CORSMiddleware,
    allow_origins=_dashboard_origins,
    allow_methods=["GET"],
    allow_headers=["X-API-Key", "Content-Type"],
)

app.include_router(dashboard_router)

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
        # NOTE: do NOT pass next_run_time=None here — APScheduler treats
        # that as "leave this job unscheduled" (paused), not "use the
        # default". That was silently disabling automatic polling
        # entirely; only the manual /poll-now endpoint ever worked,
        # because it calls poll_and_process() directly and bypasses the
        # scheduler. Omitting the argument lets APScheduler schedule the
        # first run normally (now + interval), which is what we want.
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