"""
dashboard_api.py
-----------------
Read-only API layer for the frontend dashboard. Reads directly from
the output Google Sheet (via app.oauth_drive / app.sheets) and
returns plain JSON — no writes, no pipeline triggering.

Mounted onto the main FastAPI app in main.py under /api/*.

Auth: a single shared secret (DASHBOARD_API_KEY) checked against the
`X-API-Key` header. This is meant to be called server-to-server from
the Next.js app's own API routes (which hold the secret in a
server-only env var), not directly from the browser — the browser
side is protected separately by Clerk. Keeping this simple avoids
standing up a second OAuth flow just for read-only dashboard data.

Requires env vars:
  DASHBOARD_API_KEY     shared secret the frontend sends back
  DRIVE_WATCH_FOLDER_ID same as the rest of the app
"""

from __future__ import annotations

import os
import time
from datetime import datetime, timezone

from fastapi import APIRouter, Header, HTTPException
from googleapiclient.errors import HttpError
from ssl import SSLError

from app.oauth_drive import get_sheets_service
from app.sheets import HEADER_ROW, OUTPUT_FOLDER_NAME, SPREADSHEET_NAME, find_or_create_output_spreadsheet

router = APIRouter(prefix="/api")

# Cached like pipeline.py's module-level caching — avoids a Drive API
# find-or-create round trip on every dashboard request (which was also
# hitting occasional transient SSLError: record layer failure from
# Google's API under repeated connection reuse). Resets on process
# restart, same as the pipeline's cache.
_cached_spreadsheet_id: str | None = None


def _get_spreadsheet_id() -> str:
    global _cached_spreadsheet_id
    if _cached_spreadsheet_id is None:
        _, spreadsheet_id = _with_retry(find_or_create_output_spreadsheet)
        _cached_spreadsheet_id = spreadsheet_id
    return _cached_spreadsheet_id


def _with_retry(func, *args, retries: int = 4, delay_seconds: float = 1.5, **kwargs):
    """
    Retries a Google API call on transient network/SSL failures
    (HttpError, SSLError) before giving up, with linearly increasing
    backoff. Two known transient cases this covers:
      - `ssl.SSLError: record layer failure` on otherwise-healthy
        connections.
      - A spreadsheet just created via Drive's files().create() isn't
        immediately openable through the Sheets API — Google's own
        indexing lags by a few seconds, and the Sheets API returns a
        plain HttpError 400 ("Request contains an invalid argument")
        during that window rather than a clearer "not ready yet" — so
        HttpError is retried broadly here, not just on 5xx.
    """
    last_error: Exception | None = None
    for attempt in range(retries + 1):
        try:
            return func(*args, **kwargs)
        except (SSLError, HttpError) as exc:
            last_error = exc
            if attempt < retries:
                time.sleep(delay_seconds * (attempt + 1))
    raise last_error

# HEADER_ROW columns, for reference:
# Processed At, Source File, Document Type, Vendor Name, Invoice Number,
# Invoice Date, Due Date, PO Number, Currency, Subtotal, Tax Amount,
# Total Amount, Overall Confidence, Needs Review, Disagreements, Drive File ID


def _check_api_key(x_api_key: str | None) -> None:
    expected = os.environ.get("DASHBOARD_API_KEY")
    if not expected:
        # Fail closed: if no key is configured, the API is not usable,
        # rather than silently open.
        raise HTTPException(status_code=500, detail="DASHBOARD_API_KEY not configured on server")
    if x_api_key != expected:
        raise HTTPException(status_code=401, detail="Invalid or missing API key")


def _row_to_dict(tab_name: str, row: list[str]) -> dict:
    padded = row + [""] * (len(HEADER_ROW) - len(row))
    record = dict(zip(HEADER_ROW, padded))
    return {
        "vendor_tab": tab_name,
        "processed_at": record["Processed At"],
        "source_file": record["Source File"],
        "document_type": record["Document Type"],
        "vendor_name": record["Vendor Name"],
        "invoice_number": record["Invoice Number"],
        "invoice_date": record["Invoice Date"],
        "due_date": record["Due Date"],
        "po_number": record["PO Number"],
        "currency": record["Currency"],
        "subtotal": record["Subtotal"],
        "tax_amount": record["Tax Amount"],
        "total_amount": record["Total Amount"],
        "overall_confidence": record["Overall Confidence"],
        "needs_review": record["Needs Review"] == "YES",
        "disagreements": [d.strip() for d in record["Disagreements"].split(",") if d.strip()],
        "drive_file_id": record["Drive File ID"],
        "drive_file_url": (
            f"https://drive.google.com/file/d/{record['Drive File ID']}/view"
            if record["Drive File ID"]
            else None
        ),
    }


def _fetch_all_rows() -> list[dict]:
    """
    Reads every data row from every vendor tab in the output
    spreadsheet. Skips the "Unsorted" placeholder tab if it's empty,
    and skips any tab's header row.
    """
    global _cached_spreadsheet_id
    spreadsheet_id = _get_spreadsheet_id()
    service = get_sheets_service()

    try:
        meta = _with_retry(service.spreadsheets().get(spreadsheetId=spreadsheet_id).execute)
    except HttpError as exc:
        if exc.resp.status in (400, 404):
            # Cached ID is stale (spreadsheet moved/deleted), or the
            # ID we just got back from find-or-create hasn't finished
            # indexing on Google's side yet (surfaces as a plain 400).
            # Clear the cache and look it up fresh once more — by now
            # _with_retry's own backoff has usually given it enough
            # time to become readable.
            _cached_spreadsheet_id = None
            spreadsheet_id = _get_spreadsheet_id()
            meta = _with_retry(service.spreadsheets().get(spreadsheetId=spreadsheet_id).execute)
        else:
            raise

    tab_titles = [s["properties"]["title"] for s in meta["sheets"]]

    all_rows: list[dict] = []
    for tab in tab_titles:
        result = _with_retry(
            service.spreadsheets()
            .values()
            .get(spreadsheetId=spreadsheet_id, range=f"'{tab}'!A2:P")
            .execute
        )
        values = result.get("values", [])
        for row in values:
            if not any(row):
                continue
            all_rows.append(_row_to_dict(tab, row))

    # Most recent first.
    all_rows.sort(key=lambda r: r["processed_at"], reverse=True)
    return all_rows


@router.get("/invoices")
def list_invoices(x_api_key: str | None = Header(default=None)):
    """All processed invoice rows, most recent first."""
    _check_api_key(x_api_key)
    return {"invoices": _fetch_all_rows()}


@router.get("/review-queue")
def review_queue(x_api_key: str | None = Header(default=None)):
    """Only the rows flagged needs_review = True."""
    _check_api_key(x_api_key)
    rows = [r for r in _fetch_all_rows() if r["needs_review"]]
    return {"invoices": rows}


@router.get("/summary")
def summary(x_api_key: str | None = Header(default=None)):
    """Aggregate stats for the dashboard's summary cards."""
    _check_api_key(x_api_key)
    rows = _fetch_all_rows()

    total = len(rows)
    needs_review = sum(1 for r in rows if r["needs_review"])
    by_vendor: dict[str, int] = {}
    by_currency_total: dict[str, float] = {}

    for r in rows:
        by_vendor[r["vendor_name"] or "Unknown"] = by_vendor.get(r["vendor_name"] or "Unknown", 0) + 1
        currency = r["currency"] or "Unknown"
        try:
            amount = float(r["total_amount"])
        except (TypeError, ValueError):
            amount = 0.0
        by_currency_total[currency] = by_currency_total.get(currency, 0.0) + amount

    spreadsheet_id = _get_spreadsheet_id()

    return {
        "total_processed": total,
        "needs_review_count": needs_review,
        "clean_count": total - needs_review,
        "by_vendor": by_vendor,
        "total_amount_by_currency": by_currency_total,
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "spreadsheet_url": f"https://docs.google.com/spreadsheets/d/{spreadsheet_id}/edit",
    }