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
from datetime import datetime, timezone

from fastapi import APIRouter, Header, HTTPException

from app.oauth_drive import get_sheets_service
from app.sheets import HEADER_ROW, OUTPUT_FOLDER_NAME, SPREADSHEET_NAME, find_or_create_output_spreadsheet

router = APIRouter(prefix="/api")

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
    folder_id, spreadsheet_id = find_or_create_output_spreadsheet()
    service = get_sheets_service()
    meta = service.spreadsheets().get(spreadsheetId=spreadsheet_id).execute()
    tab_titles = [s["properties"]["title"] for s in meta["sheets"]]

    all_rows: list[dict] = []
    for tab in tab_titles:
        result = (
            service.spreadsheets()
            .values()
            .get(spreadsheetId=spreadsheet_id, range=f"'{tab}'!A2:P")
            .execute()
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

    return {
        "total_processed": total,
        "needs_review_count": needs_review,
        "clean_count": total - needs_review,
        "by_vendor": by_vendor,
        "total_amount_by_currency": by_currency_total,
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }