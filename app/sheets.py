"""
sheets.py
----------
Google Sheets output layer for the invoice pipeline.

Each vendor gets its own worksheet. Standard columns remain in their
existing order; newly discovered invoice fields are added as columns
to that vendor's worksheet only.
"""

from __future__ import annotations

import os
import re
from datetime import datetime, timezone
from typing import Any

from app.oauth_drive import get_drive_service, get_sheets_service

OUTPUT_FOLDER_NAME = "Jaxfor - Finance Automation"
SPREADSHEET_NAME = "Jaxfor - Invoice Extraction Log"

# Keep these existing columns in exactly this order for compatibility.
HEADER_ROW = [
    "Processed At",
    "Source File",
    "Document Type",
    "Vendor Name",
    "Invoice Number",
    "Invoice Date",
    "Due Date",
    "PO Number",
    "Currency",
    "Subtotal",
    "Tax Amount",
    "Total Amount",
    "Overall Confidence",
    "Needs Review",
    "Disagreements",
    "Drive File ID",
]

# Known extraction keys map to stable, human-readable sheet headers.
FIELD_HEADERS = {
    "vendor_name": "Vendor Name",
    "customer_name": "Customer Name",
    "customer_address": "Customer Address",
    "vendor_address": "Vendor Address",
    "tax_registration_number": "Tax Registration Number",
    "payment_terms": "Payment Terms",
    "invoice_number": "Invoice Number",
    "invoice_date": "Invoice Date",
    "due_date": "Due Date",
    "po_number": "PO Number",
    "currency": "Currency",
    "subtotal": "Subtotal",
    "tax_amount": "Tax Amount",
    "total_amount": "Total Amount",
    "line_items": "Line Items",
}

# These are generated from row-level metadata rather than result.fields.
METADATA_HEADERS = {
    "Processed At",
    "Source File",
    "Document Type",
    "Overall Confidence",
    "Needs Review",
    "Disagreements",
    "Drive File ID",
}


# ---------------------------------------------------------------------------
# Folder + spreadsheet bootstrap
# ---------------------------------------------------------------------------

def find_or_create_output_spreadsheet(
    parent_folder_id: str | None = None,
) -> tuple[str, str]:
    """Return (output_folder_id, spreadsheet_id), creating them if needed."""
    parent_folder_id = parent_folder_id or os.environ["DRIVE_WATCH_FOLDER_ID"]
    folder_id = _find_or_create_folder(parent_folder_id, OUTPUT_FOLDER_NAME)
    spreadsheet_id = _find_or_create_spreadsheet_in_folder(
        folder_id, SPREADSHEET_NAME
    )
    return folder_id, spreadsheet_id


def _find_or_create_folder(parent_folder_id: str, name: str) -> str:
    drive_service = get_drive_service()
    query = (
        f"'{parent_folder_id}' in parents and trashed = false "
        "and mimeType = 'application/vnd.google-apps.folder' "
        f"and name = '{name}'"
    )
    results = drive_service.files().list(
        q=query, fields="files(id, name)"
    ).execute()
    existing = results.get("files", [])
    if existing:
        return existing[0]["id"]

    metadata = {
        "name": name,
        "mimeType": "application/vnd.google-apps.folder",
        "parents": [parent_folder_id],
    }
    folder = drive_service.files().create(
        body=metadata, fields="id"
    ).execute()
    return folder["id"]


def _find_or_create_spreadsheet_in_folder(folder_id: str, name: str) -> str:
    drive_service = get_drive_service()
    query = (
        f"'{folder_id}' in parents and trashed = false "
        "and mimeType = 'application/vnd.google-apps.spreadsheet' "
        f"and name = '{name}'"
    )
    results = drive_service.files().list(
        q=query, fields="files(id, name)"
    ).execute()
    existing = results.get("files", [])
    if existing:
        return existing[0]["id"]

    metadata = {
        "name": name,
        "mimeType": "application/vnd.google-apps.spreadsheet",
        "parents": [folder_id],
    }
    spreadsheet_file = drive_service.files().create(
        body=metadata, fields="id"
    ).execute()
    spreadsheet_id = spreadsheet_file["id"]

    _rename_default_tab(spreadsheet_id, "Unsorted")
    ensure_vendor_tab(spreadsheet_id, "Unsorted")
    return spreadsheet_id


def _rename_default_tab(spreadsheet_id: str, new_title: str) -> None:
    service = get_sheets_service()
    meta = service.spreadsheets().get(
        spreadsheetId=spreadsheet_id
    ).execute()
    first_sheet_id = meta["sheets"][0]["properties"]["sheetId"]
    service.spreadsheets().batchUpdate(
        spreadsheetId=spreadsheet_id,
        body={
            "requests": [
                {
                    "updateSheetProperties": {
                        "properties": {
                            "sheetId": first_sheet_id,
                            "title": new_title,
                        },
                        "fields": "title",
                    }
                }
            ]
        },
    ).execute()


# ---------------------------------------------------------------------------
# Vendor tabs and headers
# ---------------------------------------------------------------------------

def _sanitize_tab_name(name: str) -> str:
    """Sanitize a vendor name for Google Sheets tab-title rules."""
    name = (name or "").strip() or "Unknown Vendor"
    name = re.sub(r"[:\\/?*\[\]]", "-", name)
    return name[:100]


def _list_tab_titles(spreadsheet_id: str) -> list[str]:
    service = get_sheets_service()
    meta = service.spreadsheets().get(
        spreadsheetId=spreadsheet_id
    ).execute()
    return [sheet["properties"]["title"] for sheet in meta["sheets"]]


def ensure_vendor_tab(spreadsheet_id: str, vendor_name: str) -> str:
    """Return the vendor tab name, creating it with standard headers if absent."""
    tab_name = _sanitize_tab_name(vendor_name)
    existing = _list_tab_titles(spreadsheet_id)

    if tab_name not in existing:
        service = get_sheets_service()
        service.spreadsheets().batchUpdate(
            spreadsheetId=spreadsheet_id,
            body={
                "requests": [
                    {"addSheet": {"properties": {"title": tab_name}}}
                ]
            },
        ).execute()
        _write_header_row(spreadsheet_id, tab_name)

    return tab_name


def _write_header_row(spreadsheet_id: str, tab_name: str) -> None:
    """Write the standard header row to a newly created or empty tab."""
    service = get_sheets_service()
    service.spreadsheets().values().update(
        spreadsheetId=spreadsheet_id,
        range=f"'{tab_name}'!A1",
        valueInputOption="RAW",
        body={"values": [HEADER_ROW]},
    ).execute()


def _column_letter(column_number: int) -> str:
    """Convert a 1-based column number to its Google Sheets A1 column label."""
    if column_number < 1:
        raise ValueError("column_number must be >= 1")
    result = ""
    number = column_number
    while number:
        number, remainder = divmod(number - 1, 26)
        result = chr(65 + remainder) + result
    return result


def _field_header(field_key: str) -> str:
    """Get a stable display header for a field, including unknown fields."""
    if field_key in FIELD_HEADERS:
        return FIELD_HEADERS[field_key]
    # Unknown fields are stored under readable headers, e.g. delivery_note
    # becomes "Delivery Note". The extraction key remains the source of truth.
    return " ".join(part.capitalize() for part in field_key.strip().split("_") if part)


def _read_headers(spreadsheet_id: str, tab_name: str) -> list[str]:
    service = get_sheets_service()
    response = service.spreadsheets().values().get(
        spreadsheetId=spreadsheet_id,
        range=f"'{tab_name}'!1:1",
    ).execute()
    values = response.get("values", [])
    headers = values[0] if values else []
    headers = [str(value).strip() for value in headers]

    # A pre-existing blank tab gets the standard headers. Existing populated
    # tabs are never reset or rewritten wholesale.
    if not headers or not any(headers):
        _write_header_row(spreadsheet_id, tab_name)
        return HEADER_ROW.copy()

    # If a header row has trailing blank cells, trim only those trailing cells.
    while headers and not headers[-1]:
        headers.pop()
    return headers


def _ensure_field_headers(
    spreadsheet_id: str,
    tab_name: str,
    fields: dict[str, Any],
) -> list[str]:
    """
    Ensure every extracted field has a column on this vendor tab only.
    Existing columns are preserved; new columns are appended at the right.
    """
    headers = _read_headers(spreadsheet_id, tab_name)
    existing = set(headers)
    new_headers: list[str] = []

    for field_key in fields:
        header = _field_header(field_key)
        if header not in existing:
            existing.add(header)
            new_headers.append(header)

    if new_headers:
        start_column = len(headers) + 1
        end_column = len(headers) + len(new_headers)
        start_letter = _column_letter(start_column)
        end_letter = _column_letter(end_column)
        service = get_sheets_service()
        service.spreadsheets().values().update(
            spreadsheetId=spreadsheet_id,
            range=f"'{tab_name}'!{start_letter}1:{end_letter}1",
            valueInputOption="RAW",
            body={"values": [new_headers]},
        ).execute()
        headers.extend(new_headers)

    return headers


# ---------------------------------------------------------------------------
# Row append
# ---------------------------------------------------------------------------

def _field_value(field: Any) -> str:
    """Extract a value from a FieldResult-like object or a raw value."""
    if isinstance(field, dict):
        value = field.get("value", "")
    else:
        value = getattr(field, "value", field if field is not None else "")
    if value is None:
        return ""
    if isinstance(value, (dict, list)):
        import json
        return json.dumps(value, ensure_ascii=False)
    return str(value)


def append_extraction_row(
    spreadsheet_id: str,
    vendor_name: str,
    row: dict,
    source_file: str,
    drive_file_id: str,
) -> str:
    """
    Append one invoice row under matching headers in the vendor's worksheet.

    `row["fields"]` may contain standard and document-specific fields. Missing
    values are written as blank cells; newly discovered field columns are
    appended to this vendor's worksheet without altering other vendor tabs.
    """
    tab_name = ensure_vendor_tab(spreadsheet_id, vendor_name)
    fields = row.get("fields", {}) or {}
    headers = _ensure_field_headers(spreadsheet_id, tab_name, fields)

    metadata_values = {
        "Processed At": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "Source File": source_file,
        "Document Type": row.get("document_type", ""),
        "Overall Confidence": row.get("overall_confidence", ""),
        "Needs Review": "YES" if row.get("needs_review") else "NO",
        "Disagreements": ", ".join(
            str(item) for item in (row.get("disagreements") or [])
        ),
        "Drive File ID": drive_file_id,
    }

    values_by_header = dict(metadata_values)
    for field_key, field in fields.items():
        values_by_header[_field_header(str(field_key))] = _field_value(field)

    values = [values_by_header.get(header, "") for header in headers]

    service = get_sheets_service()
    service.spreadsheets().values().append(
        spreadsheetId=spreadsheet_id,
        range=f"'{tab_name}'!A1",
        valueInputOption="RAW",
        insertDataOption="INSERT_ROWS",
        body={"values": [values]},
    ).execute()

    return tab_name
