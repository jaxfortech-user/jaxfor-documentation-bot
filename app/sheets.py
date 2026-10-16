"""
sheets.py
----------
Google Sheets output layer for the invoice pipeline.

Each vendor gets its own worksheet. Standard columns remain in their
existing order; newly discovered invoice fields are added as columns
to that vendor's worksheet only.
"""

from __future__ import annotations

import json
import logging
import os
import re
from datetime import datetime, timezone
from typing import Any

from app.oauth_drive import get_drive_service, get_sheets_service

OUTPUT_FOLDER_NAME = "Jaxfor - Finance Automation"
SPREADSHEET_NAME = "Jaxfor - Invoice Extraction Log"

# Keep these existing columns in exactly this order for compatibility.
HEADER_ROW = [
    "Processed At", "Source File", "Document Type", "Vendor Name",
    "Invoice Number", "Invoice Date", "Due Date", "PO Number", "Currency",
    "Subtotal", "Tax Amount", "Total Amount", "Overall Confidence",
    "Needs Review", "Disagreements", "Drive File ID",
]

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

METADATA_HEADERS = {
    "Processed At", "Source File", "Document Type", "Overall Confidence",
    "Needs Review", "Disagreements", "Drive File ID",
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
    spreadsheet_id = _find_or_create_spreadsheet_in_folder(folder_id, SPREADSHEET_NAME)
    return folder_id, spreadsheet_id


def _find_or_create_folder(parent_folder_id: str, name: str) -> str:
    drive_service = get_drive_service()
    query = (
        f"'{parent_folder_id}' in parents and trashed = false "
        "and mimeType = 'application/vnd.google-apps.folder' "
        f"and name = '{name}'"
    )
    results = drive_service.files().list(q=query, fields="files(id, name)").execute()
    existing = results.get("files", [])
    if existing:
        return existing[0]["id"]
    metadata = {
        "name": name,
        "mimeType": "application/vnd.google-apps.folder",
        "parents": [parent_folder_id],
    }
    folder = drive_service.files().create(body=metadata, fields="id").execute()
    return folder["id"]


def _find_or_create_spreadsheet_in_folder(folder_id: str, name: str) -> str:
    drive_service = get_drive_service()
    query = (
        f"'{folder_id}' in parents and trashed = false "
        "and mimeType = 'application/vnd.google-apps.spreadsheet' "
        f"and name = '{name}'"
    )
    results = drive_service.files().list(q=query, fields="files(id, name)").execute()
    existing = results.get("files", [])
    if existing:
        return existing[0]["id"]
    metadata = {
        "name": name,
        "mimeType": "application/vnd.google-apps.spreadsheet",
        "parents": [folder_id],
    }
    spreadsheet_file = drive_service.files().create(body=metadata, fields="id").execute()
    spreadsheet_id = spreadsheet_file["id"]
    _rename_default_tab(spreadsheet_id, "Unsorted")
    ensure_vendor_tab(spreadsheet_id, "Unsorted")
    return spreadsheet_id


def _rename_default_tab(spreadsheet_id: str, new_title: str) -> None:
    service = get_sheets_service()
    meta = service.spreadsheets().get(spreadsheetId=spreadsheet_id).execute()
    first_sheet_id = meta["sheets"][0]["properties"]["sheetId"]
    service.spreadsheets().batchUpdate(
        spreadsheetId=spreadsheet_id,
        body={"requests": [{"updateSheetProperties": {
            "properties": {"sheetId": first_sheet_id, "title": new_title},
            "fields": "title",
        }}]},
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
    meta = service.spreadsheets().get(spreadsheetId=spreadsheet_id).execute()
    return [sheet["properties"]["title"] for sheet in meta["sheets"]]


def _get_sheet_id(spreadsheet_id: str, tab_name: str) -> int:
    """Return the numeric Google Sheets ID for a worksheet title."""
    service = get_sheets_service()
    meta = service.spreadsheets().get(spreadsheetId=spreadsheet_id).execute()
    for sheet in meta.get("sheets", []):
        properties = sheet.get("properties", {})
        if properties.get("title") == tab_name:
            return int(properties["sheetId"])
    raise ValueError(f"Worksheet not found: {tab_name}")


def _ensure_column_capacity(
    spreadsheet_id: str,
    tab_name: str,
    required_columns: int,
) -> None:
    """Expand a worksheet's grid when more columns are required."""
    if required_columns < 1:
        raise ValueError("required_columns must be >= 1")

    service = get_sheets_service()
    metadata = service.spreadsheets().get(
        spreadsheetId=spreadsheet_id
    ).execute()

    for sheet in metadata.get("sheets", []):
        properties = sheet.get("properties", {})
        if properties.get("title") != tab_name:
            continue

        current_columns = properties.get(
            "gridProperties", {}
        ).get("columnCount", 26)

        if required_columns > current_columns:
            service.spreadsheets().batchUpdate(
                spreadsheetId=spreadsheet_id,
                body={
                    "requests": [
                        {
                            "updateSheetProperties": {
                                "properties": {
                                    "sheetId": properties["sheetId"],
                                    "gridProperties": {
                                        "columnCount": required_columns
                                    },
                                },
                                "fields": "gridProperties.columnCount",
                            }
                        }
                    ]
                },
            ).execute()
        return

    raise ValueError(f"Worksheet not found: {tab_name}")



def ensure_vendor_tab(spreadsheet_id: str, vendor_name: str) -> str:
    """Return the vendor tab name, creating it with standard headers if absent."""
    tab_name = _sanitize_tab_name(vendor_name)
    existing = _list_tab_titles(spreadsheet_id)
    if tab_name not in existing:
        service = get_sheets_service()
        service.spreadsheets().batchUpdate(
            spreadsheetId=spreadsheet_id,
            body={"requests": [{"addSheet": {"properties": {"title": tab_name}}}]},
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
    return " ".join(part.capitalize() for part in field_key.strip().split("_") if part)


def _read_headers(spreadsheet_id: str, tab_name: str) -> list[str]:
    service = get_sheets_service()
    response = service.spreadsheets().values().get(
        spreadsheetId=spreadsheet_id,
        range=f"'{tab_name}'!1:1",
    ).execute()
    values = response.get("values", [])
    headers = [str(value).strip() for value in (values[0] if values else [])]
    if not headers or not any(headers):
        _write_header_row(spreadsheet_id, tab_name)
        return HEADER_ROW.copy()
    while headers and not headers[-1]:
        headers.pop()
    return headers



def _ensure_field_headers(
    spreadsheet_id: str,
    tab_name: str,
    fields: dict[str, Any],
) -> list[str]:
    """Append new field headers while preserving existing columns."""
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

        # Ensure the worksheet has enough columns before writing headers.
        _ensure_column_capacity(
            spreadsheet_id=spreadsheet_id,
            tab_name=tab_name,
            required_columns=end_column,
        )

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
# Review highlighting helpers
# ---------------------------------------------------------------------------

RED_FILL = {"red": 0.988, "green": 0.894, "blue": 0.882}
YELLOW_FILL = {"red": 0.996, "green": 0.969, "blue": 0.820}


def _field_review_color(field: Any) -> dict[str, float] | None:
    """Return a background color based on field-level extraction quality."""
    if isinstance(field, dict):
        confidence = str(field.get("confidence", "")).strip().lower()
        notes = str(field.get("notes", "")).strip()
        agreement = field.get("agreement", True)
    else:
        confidence = str(getattr(field, "confidence", "")).strip().lower()
        notes = str(getattr(field, "notes", "")).strip()
        agreement = getattr(field, "agreement", True)
    if agreement is False or confidence == "low":
        return RED_FILL
    if confidence == "medium" or notes:
        return YELLOW_FILL
    return None


def _cell_format_request(
    sheet_id: int,
    row_index: int,
    column_index: int,
    color: dict[str, float],
) -> dict[str, Any]:
    """Build a Google Sheets formatting request for one zero-based cell."""
    return {"repeatCell": {
        "range": {
            "sheetId": sheet_id,
            "startRowIndex": row_index,
            "endRowIndex": row_index + 1,
            "startColumnIndex": column_index,
            "endColumnIndex": column_index + 1,
        },
        "cell": {"userEnteredFormat": {"backgroundColor": color}},
        "fields": "userEnteredFormat.backgroundColor",
    }}


def _build_review_format_requests(
    sheet_id: int,
    row_index: int,
    headers: list[str],
    fields: dict[str, Any],
    disagreements: list[str],
    validation_reasons: list[str],
) -> list[dict[str, Any]]:
    """Build unique per-cell formatting requests; red takes precedence over yellow."""
    colors_by_column: dict[int, dict[str, float]] = {}
    column_by_header = {header: i for i, header in enumerate(headers)}

    def add_header(header: str, color: dict[str, float]) -> None:
        column_index = column_by_header.get(header)
        if column_index is None:
            return
        previous = colors_by_column.get(column_index)
        if previous == RED_FILL:
            return
        if color == RED_FILL or previous is None:
            colors_by_column[column_index] = color

    for field_key, field in fields.items():
        color = _field_review_color(field)
        if color is not None:
            add_header(_field_header(str(field_key)), color)

    for disagreement in disagreements:
        key = str(disagreement).strip()
        if key in fields:
            add_header(_field_header(key), RED_FILL)

    for reason in validation_reasons:
        normalized = str(reason).lower()
        if "arithmetic mismatch:" in normalized:
            for header in ("Subtotal", "Tax Amount", "Total Amount"):
                add_header(header, RED_FILL)
        if "line items sum to" in normalized:
            add_header("Line Items", RED_FILL)
            add_header("Subtotal", RED_FILL)
        if "no po number" in normalized:
            add_header("PO Number", RED_FILL)

    if any("line_items_arithmetic" in str(item) for item in disagreements):
        add_header("Line Items", RED_FILL)
    if any("line_items_subtotal" in str(item) for item in disagreements):
        add_header("Line Items", RED_FILL)
        add_header("Subtotal", RED_FILL)

    return [
        _cell_format_request(sheet_id, row_index, column, color)
        for column, color in sorted(colors_by_column.items())
    ]


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
        return json.dumps(value, ensure_ascii=False)
    return str(value)


def append_extraction_row(
    spreadsheet_id: str,
    vendor_name: str,
    row: dict,
    source_file: str,
    drive_file_id: str,
) -> str:
    """Append one invoice row and highlight fields requiring review."""
    tab_name = ensure_vendor_tab(spreadsheet_id, vendor_name)
    fields = row.get("fields", {}) or {}
    headers = _ensure_field_headers(spreadsheet_id, tab_name, fields)

    metadata_values = {
        "Processed At": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "Source File": source_file,
        "Document Type": row.get("document_type", ""),
        "Overall Confidence": row.get("overall_confidence", ""),
        "Needs Review": "YES" if row.get("needs_review") else "NO",
        "Disagreements": ", ".join(str(item) for item in (row.get("disagreements") or [])),
        "Drive File ID": drive_file_id,
    }
    values_by_header = dict(metadata_values)
    for field_key, field in fields.items():
        values_by_header[_field_header(str(field_key))] = _field_value(field)
    values = [values_by_header.get(header, "") for header in headers]

    service = get_sheets_service()
    append_response = service.spreadsheets().values().append(
        spreadsheetId=spreadsheet_id,
        range=f"'{tab_name}'!A1",
        valueInputOption="RAW",
        insertDataOption="INSERT_ROWS",
        body={"values": [values]},
    ).execute()


    # Use the actual appended range. If missing, do not guess a row index.
    updated_range = (
        append_response.get("updates", {}).get("updatedRange", "")
        if isinstance(append_response, dict)
        else ""
    )
    match = re.search(r"![A-Z]+(\d+)(?::[A-Z]+(\d+))?$", updated_range)
    if not match:
        return tab_name

    row_index = int(match.group(1)) - 1

    # The row has already been appended. Formatting is best-effort:
    # failures here must not trigger a retry of the invoice append.
    try:
        sheet_id = _get_sheet_id(spreadsheet_id, tab_name)

        formatting_requests = _build_review_format_requests(
            sheet_id=sheet_id,
            row_index=row_index,
            headers=headers,
            fields=fields,
            disagreements=row.get("disagreements", []) or [],
            validation_reasons=row.get("validation_reasons", []) or [],
        )

        reconciliation = row.get("line_item_reconciliation", {}) or {}
        arithmetic = row.get("line_item_arithmetic", {}) or {}
        column_by_header = {
            header: index for index, header in enumerate(headers)
        }

        def add_red_cell(header: str) -> None:
            column_index = column_by_header.get(header)
            if column_index is not None:
                formatting_requests.append(
                    _cell_format_request(
                        sheet_id, row_index, column_index, RED_FILL
                    )
                )

        if reconciliation.get("status") not in (None, "", "matched"):
            add_red_cell("Line Items")
            add_red_cell("Subtotal")

        if arithmetic.get("status") not in (None, "", "matched"):
            add_red_cell("Line Items")

        # Deduplicate by column; red takes precedence over yellow.
        requests_by_column: dict[int, dict[str, Any]] = {}

        for request in formatting_requests:
            repeat_cell = request["repeatCell"]
            column_index = repeat_cell["range"]["startColumnIndex"]
            color = repeat_cell["cell"]["userEnteredFormat"]["backgroundColor"]
            previous = requests_by_column.get(column_index)

            if previous is None:
                requests_by_column[column_index] = request
            else:
                previous_color = (
                    previous["repeatCell"]["cell"]["userEnteredFormat"]
                    ["backgroundColor"]
                )
                if color == RED_FILL and previous_color != RED_FILL:
                    requests_by_column[column_index] = request

        if requests_by_column:
            service.spreadsheets().batchUpdate(
                spreadsheetId=spreadsheet_id,
                body={"requests": list(requests_by_column.values())},
            ).execute()

    except Exception:
        logging.getLogger("jaxfor.sheets").exception(
            "Invoice row was appended, but cell highlighting failed "
            "(spreadsheet=%s, tab=%s, range=%s)",
            spreadsheet_id,
            tab_name,
            updated_range,
        )

    return tab_name


    # Deduplicate requests by column; red takes precedence over yellow.
    requests_by_column: dict[int, dict[str, Any]] = {}
    for request in formatting_requests:
        repeat_cell = request["repeatCell"]
        column_index = repeat_cell["range"]["startColumnIndex"]
        color = repeat_cell["cell"]["userEnteredFormat"]["backgroundColor"]
        previous = requests_by_column.get(column_index)
        if previous is None:
            requests_by_column[column_index] = request
        else:
            previous_color = previous["repeatCell"]["cell"]["userEnteredFormat"]["backgroundColor"]
            if color == RED_FILL and previous_color != RED_FILL:
                requests_by_column[column_index] = request

    if requests_by_column:
        # Appending succeeded already. A formatting failure should not cause
        # the caller to retry and accidentally append a duplicate invoice row.
        try:
            service.spreadsheets().batchUpdate(
                spreadsheetId=spreadsheet_id,
                body={"requests": list(requests_by_column.values())},
            ).execute()
        except Exception:
            logging.getLogger("jaxfor.sheets").exception(
                "Invoice row was appended, but cell highlighting failed "
                "(spreadsheet=%s, tab=%s, range=%s)",
                spreadsheet_id, tab_name, updated_range,
            )

    return tab_name
