"""
dashboard_api.py
-----------------
Read-only API layer for the frontend dashboard. Reads the output Google
Sheet and returns plain JSON. It does not trigger invoice processing or
write to Google Sheets.

Each vendor tab can have its own dynamic columns. Rows are mapped by that
tab's actual header row rather than assuming a fixed A:P schema.
"""

from __future__ import annotations

import os
import re
import time
from datetime import datetime, timezone
from ssl import SSLError

from fastapi import APIRouter, Header, HTTPException
from googleapiclient.errors import HttpError

from app.oauth_drive import get_sheets_service
from app.sheets import find_or_create_output_spreadsheet

router = APIRouter(prefix="/api")
_cached_spreadsheet_id: str | None = None

# Standard sheet labels map to stable API keys used by the existing dashboard.
HEADER_TO_KEY = {
    "Processed At": "processed_at",
    "Source File": "source_file",
    "Document Type": "document_type",
    "Vendor Name": "vendor_name",
    "Customer Name": "customer_name",
    "Customer Address": "customer_address",
    "Vendor Address": "vendor_address",
    "Tax Registration Number": "tax_registration_number",
    "Payment Terms": "payment_terms",
    "Invoice Number": "invoice_number",
    "Invoice Date": "invoice_date",
    "Due Date": "due_date",
    "PO Number": "po_number",
    "Currency": "currency",
    "Subtotal": "subtotal",
    "Tax Amount": "tax_amount",
    "Total Amount": "total_amount",
    "Line Items": "line_items",
    "Overall Confidence": "overall_confidence",
    "Needs Review": "needs_review",
    "Disagreements": "disagreements",
    "Drive File ID": "drive_file_id",
}


def _get_spreadsheet_id() -> str:
    global _cached_spreadsheet_id
    if _cached_spreadsheet_id is None:
        _, spreadsheet_id = _with_retry(find_or_create_output_spreadsheet)
        _cached_spreadsheet_id = spreadsheet_id
    return _cached_spreadsheet_id


def _with_retry(func, *args, retries: int = 4, delay_seconds: float = 1.5, **kwargs):
    """Retry transient Google API HTTP and SSL failures with backoff."""
    last_error: Exception | None = None
    for attempt in range(retries + 1):
        try:
            return func(*args, **kwargs)
        except (SSLError, HttpError) as exc:
            last_error = exc
            if attempt < retries:
                time.sleep(delay_seconds * (attempt + 1))
    if last_error is not None:
        raise last_error
    raise RuntimeError("Retry loop ended without a result or exception")


def _check_api_key(x_api_key: str | None) -> None:
    expected = os.environ.get("DASHBOARD_API_KEY")
    if not expected:
        raise HTTPException(
            status_code=500,
            detail="DASHBOARD_API_KEY not configured on server",
        )
    if x_api_key != expected:
        raise HTTPException(status_code=401, detail="Invalid or missing API key")


def _header_to_key(header: str) -> str:
    """Convert known headers to stable keys and unknown headers to snake_case."""
    header = (header or "").strip()
    if header in HEADER_TO_KEY:
        return HEADER_TO_KEY[header]
    key = re.sub(r"[^a-zA-Z0-9]+", "_", header).strip("_").lower()
    return key or "unknown_field"


def _parse_bool(value: str) -> bool:
    return str(value).strip().upper() in {"YES", "TRUE", "1", "Y"}


def _row_to_dict(tab_name: str, headers: list[str], row: list[str]) -> dict:
    """Map a row using its worksheet's own headers, tolerating missing cells."""
    padded = list(row) + [""] * max(0, len(headers) - len(row))
    record = {
        str(header).strip(): (padded[index] if index < len(padded) else "")
        for index, header in enumerate(headers)
        if str(header).strip()
    }

    result: dict = {
        "vendor_tab": tab_name,
        "processed_at": record.get("Processed At", ""),
        "source_file": record.get("Source File", ""),
        "document_type": record.get("Document Type", ""),
        "vendor_name": record.get("Vendor Name", "") or tab_name,
        "invoice_number": record.get("Invoice Number", ""),
        "invoice_date": record.get("Invoice Date", ""),
        "due_date": record.get("Due Date", ""),
        "po_number": record.get("PO Number", ""),
        "currency": record.get("Currency", ""),
        "subtotal": record.get("Subtotal", ""),
        "tax_amount": record.get("Tax Amount", ""),
        "total_amount": record.get("Total Amount", ""),
        "overall_confidence": record.get("Overall Confidence", ""),
        "needs_review": _parse_bool(record.get("Needs Review", "")),
        "disagreements": [
            item.strip()
            for item in record.get("Disagreements", "").split(",")
            if item.strip()
        ],
        "drive_file_id": record.get("Drive File ID", ""),
        "drive_file_url": (
            f"https://drive.google.com/file/d/{record['Drive File ID']}/view"
            if record.get("Drive File ID")
            else None
        ),
    }

    # Include every non-standard sheet field in a dedicated object. Also expose
    # additional fields at the top level for backwards-compatible/simple UI use.
    additional_fields: dict[str, str] = {}
    # Only the legacy dashboard columns are consumed above. Other recognized
    # invoice fields (customer details, payment terms, line items, etc.) should
    # still be exposed as dynamic fields.
    standard_headers = {
        "Processed At", "Source File", "Document Type", "Vendor Name",
        "Invoice Number", "Invoice Date", "Due Date", "PO Number", "Currency",
        "Subtotal", "Tax Amount", "Total Amount", "Overall Confidence",
        "Needs Review", "Disagreements", "Drive File ID",
    }
    for header, value in record.items():
        if header in standard_headers:
            continue
        key = _header_to_key(header)
        additional_fields[key] = value
        if key not in result:
            result[key] = value
    result["additional_fields"] = additional_fields

    # Preserve the legacy line_items string when a sheet cell contains JSON.
    # Do not parse or discard arbitrary content if the cell is not valid JSON.
    return result


def _fetch_all_rows() -> list[dict]:
    """
    Read each worksheet's header row and its data rows. The range extends
    through column ZZ so vendor-specific fields beyond P are included.
    """
    global _cached_spreadsheet_id
    spreadsheet_id = _get_spreadsheet_id()
    service = get_sheets_service()

    try:
        meta = _with_retry(
            service.spreadsheets().get(spreadsheetId=spreadsheet_id).execute
        )
    except HttpError as exc:
        if exc.resp.status in (400, 404):
            _cached_spreadsheet_id = None
            spreadsheet_id = _get_spreadsheet_id()
            meta = _with_retry(
                service.spreadsheets().get(spreadsheetId=spreadsheet_id).execute
            )
        else:
            raise

    tab_titles = [sheet["properties"]["title"] for sheet in meta["sheets"]]
    all_rows: list[dict] = []

    for tab in tab_titles:
        header_result = _with_retry(
            service.spreadsheets()
            .values()
            .get(spreadsheetId=spreadsheet_id, range=f"'{tab}'!1:1")
            .execute
        )
        header_values = header_result.get("values", [])
        if not header_values or not any(str(v).strip() for v in header_values[0]):
            continue
        headers = [str(value).strip() for value in header_values[0]]

        data_result = _with_retry(
            service.spreadsheets()
            .values()
            .get(spreadsheetId=spreadsheet_id, range=f"'{tab}'!A2:ZZ")
            .execute
        )
        for row in data_result.get("values", []):
            if not any(str(value).strip() for value in row):
                continue
            all_rows.append(_row_to_dict(tab, headers, row))

    all_rows.sort(key=lambda record: record.get("processed_at", ""), reverse=True)
    return all_rows


@router.get("/sheet-tabs")
def sheet_tabs(x_api_key: str | None = Header(default=None)):
    """List worksheet tabs and direct links to each tab."""
    _check_api_key(x_api_key)
    spreadsheet_id = _get_spreadsheet_id()
    service = get_sheets_service()
    meta = _with_retry(
        service.spreadsheets()
        .get(
            spreadsheetId=spreadsheet_id,
            fields="sheets.properties(sheetId,title)",
        )
        .execute
    )
    tabs = [
        {
            "title": sheet["properties"]["title"],
            "url": (
                f"https://docs.google.com/spreadsheets/d/{spreadsheet_id}/edit"
                f"#gid={sheet['properties']['sheetId']}"
            ),
        }
        for sheet in meta["sheets"]
    ]
    return {
        "spreadsheet_url": f"https://docs.google.com/spreadsheets/d/{spreadsheet_id}/edit",
        "tabs": tabs,
    }


@router.get("/invoices")
def list_invoices(x_api_key: str | None = Header(default=None)):
    """Return all processed invoice rows, most recent first."""
    _check_api_key(x_api_key)
    return {"invoices": _fetch_all_rows()}


@router.get("/review-queue")
def review_queue(x_api_key: str | None = Header(default=None)):
    """Return only invoices flagged for review."""
    _check_api_key(x_api_key)
    rows = [record for record in _fetch_all_rows() if record["needs_review"]]
    return {"invoices": rows}


@router.get("/summary")
def summary(x_api_key: str | None = Header(default=None)):
    """Aggregate stats for dashboard summary cards."""
    _check_api_key(x_api_key)
    rows = _fetch_all_rows()

    total = len(rows)
    needs_review = sum(1 for record in rows if record["needs_review"])
    by_vendor: dict[str, int] = {}
    by_currency_total: dict[str, float] = {}

    for record in rows:
        vendor = record["vendor_name"] or "Unknown"
        by_vendor[vendor] = by_vendor.get(vendor, 0) + 1
        currency = record["currency"] or "Unknown"
        try:
            amount = float(record["total_amount"])
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
