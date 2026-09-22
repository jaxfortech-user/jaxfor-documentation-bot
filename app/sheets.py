"""
sheets.py
----------
Google Sheets output layer for the invoice pipeline.

Responsibilities:
  - Find-or-create the output folder ("Jaxfor - Finance Automation")
    inside "J - Invoices", auto-created under the customer's own
    Google account via OAuth (see app.oauth_drive for why).
  - Find-or-create the output spreadsheet ("Jaxfor - Invoice Extraction Log")
    inside that folder.
  - Find-or-create a tab per vendor (dynamic tab creation) with a
    fixed header row.
  - Append one row per processed invoice to the correct tab.

Auth: this module uses app.oauth_drive exclusively (the customer's
own Google identity, via a one-time browser consent + a refreshable
token) rather than the service account in app.drive. Reason: service
accounts have zero Drive storage quota on a personal (non-Workspace)
account, so they can create nothing new — every create() call fails
with `storageQuotaExceeded` regardless of which folder it's parented
under. The service account (app.drive) is still what watches
"J - Invoices", downloads invoices, and moves processed files — this
module only owns the pieces that require *creating* new Drive
objects.

Requires env vars:
  DRIVE_WATCH_FOLDER_ID         folder ID of "J - Invoices" — the
                                 parent the output folder is created
                                 under
  OAUTH_CLIENT_SECRET_JSON      see app/oauth_drive.py (optional,
                                 has a default path)
  OAUTH_TOKEN_JSON               see app/oauth_drive.py (optional,
                                 has a default path)
"""

from __future__ import annotations

import os
import re
from datetime import datetime, timezone

from app.oauth_drive import get_drive_service, get_sheets_service

OUTPUT_FOLDER_NAME = "Jaxfor - Finance Automation"
SPREADSHEET_NAME = "Jaxfor - Invoice Extraction Log"

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


# ---------------------------------------------------------------------------
# Folder + spreadsheet bootstrap
# ---------------------------------------------------------------------------

def find_or_create_output_spreadsheet(parent_folder_id: str | None = None) -> tuple[str, str]:
    """
    Ensures the output folder and spreadsheet both exist under the
    customer's own Google account (auto-creating whichever pieces
    are missing), nested inside "J - Invoices" for tidy organization.
    Returns (folder_id, spreadsheet_id).
    """
    parent_folder_id = parent_folder_id or os.environ["DRIVE_WATCH_FOLDER_ID"]
    folder_id = _find_or_create_folder(parent_folder_id, OUTPUT_FOLDER_NAME)
    spreadsheet_id = _find_or_create_spreadsheet_in_folder(folder_id, SPREADSHEET_NAME)
    return folder_id, spreadsheet_id


def _find_or_create_folder(parent_folder_id: str, name: str) -> str:
    drive_service = get_drive_service()
    query = (
        f"'{parent_folder_id}' in parents and trashed = false "
        f"and mimeType = 'application/vnd.google-apps.folder' "
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
        f"and mimeType = 'application/vnd.google-apps.spreadsheet' "
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

    # Rename the default "Sheet1" tab to something less confusing and
    # write its header row, so an empty spreadsheet is never left with
    # a bare, unlabeled tab.
    _rename_default_tab(spreadsheet_id, "Unsorted")
    ensure_vendor_tab(spreadsheet_id, "Unsorted")

    return spreadsheet_id


def _rename_default_tab(spreadsheet_id: str, new_title: str) -> None:
    service = get_sheets_service()
    meta = service.spreadsheets().get(spreadsheetId=spreadsheet_id).execute()
    first_sheet_id = meta["sheets"][0]["properties"]["sheetId"]
    service.spreadsheets().batchUpdate(
        spreadsheetId=spreadsheet_id,
        body={
            "requests": [
                {
                    "updateSheetProperties": {
                        "properties": {"sheetId": first_sheet_id, "title": new_title},
                        "fields": "title",
                    }
                }
            ]
        },
    ).execute()


# ---------------------------------------------------------------------------
# Dynamic tab creation (one tab per vendor)
# ---------------------------------------------------------------------------

def _sanitize_tab_name(name: str) -> str:
    """
    Sheet tab names can't contain: : \\ / ? * [ ]  and are capped at
    100 chars. Falls back to "Unknown Vendor" for an empty/blank name.
    """
    name = (name or "").strip() or "Unknown Vendor"
    name = re.sub(r"[:\\/?*\[\]]", "-", name)
    return name[:100]


def _list_tab_titles(spreadsheet_id: str) -> list[str]:
    service = get_sheets_service()
    meta = service.spreadsheets().get(spreadsheetId=spreadsheet_id).execute()
    return [s["properties"]["title"] for s in meta["sheets"]]


def ensure_vendor_tab(spreadsheet_id: str, vendor_name: str) -> str:
    """
    Returns the sanitized tab name for `vendor_name`, creating the tab
    (with header row) if it doesn't already exist. Vendor name is used
    as-is for the tab title (sanitized for Sheets' naming rules) so
    each vendor's invoices land on their own tab.
    """
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
    service = get_sheets_service()
    service.spreadsheets().values().update(
        spreadsheetId=spreadsheet_id,
        range=f"'{tab_name}'!A1",
        valueInputOption="RAW",
        body={"values": [HEADER_ROW]},
    ).execute()


# ---------------------------------------------------------------------------
# Row append
# ---------------------------------------------------------------------------

def append_extraction_row(
    spreadsheet_id: str,
    vendor_name: str,
    row: dict,
    source_file: str,
    drive_file_id: str,
) -> str:
    """
    Appends one processed-invoice row to the vendor's tab (creating
    the tab if needed). `row` is expected to be the dict form of an
    ExtractionResult (see app.extract) — this function pulls the
    fields it needs out of it defensively so a missing key doesn't
    blow up the whole pipeline run.

    Returns the tab name the row was written to.
    """
    tab_name = ensure_vendor_tab(spreadsheet_id, vendor_name)

    fields = row.get("fields", {})

    def field_value(key: str) -> str:
        return (fields.get(key) or {}).get("value", "")

    values = [
        datetime.now(timezone.utc).isoformat(timespec="seconds"),
        source_file,
        row.get("document_type", ""),
        field_value("vendor_name"),
        field_value("invoice_number"),
        field_value("invoice_date"),
        field_value("due_date"),
        field_value("po_number"),
        field_value("currency"),
        field_value("subtotal"),
        field_value("tax_amount"),
        field_value("total_amount"),
        row.get("overall_confidence", ""),
        "YES" if row.get("needs_review") else "NO",
        ", ".join(row.get("disagreements", [])),
        drive_file_id,
    ]

    service = get_sheets_service()
    service.spreadsheets().values().append(
        spreadsheetId=spreadsheet_id,
        range=f"'{tab_name}'!A1",
        valueInputOption="RAW",
        insertDataOption="INSERT_ROWS",
        body={"values": [values]},
    ).execute()

    return tab_name