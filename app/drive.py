"""
drive.py
---------
Google Drive integration for the invoice pipeline.

Responsibilities:
  - Authenticate via service account
  - List new files sitting in the watched folder
  - Download a file's bytes + mime type
  - Find-or-create the "Processed" and "NeedsReview" subfolders
  - Move a file into one of those subfolders after processing
  - Share a newly created item with the owner's personal email

Requires env vars:
  GOOGLE_SERVICE_ACCOUNT_JSON   path to the service account key file
  DRIVE_WATCH_FOLDER_ID          folder ID of "J - Invoices"
  OWNER_EMAIL                       your personal Drive email, for auto-share
"""

from __future__ import annotations

import io
import os
from dataclasses import dataclass

from google.oauth2 import service_account
from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseDownload

SCOPES = [
    "https://www.googleapis.com/auth/drive",
    "https://www.googleapis.com/auth/spreadsheets",
]

_service = None


def _get_service():
    global _service
    if _service is None:
        key_path = os.environ["GOOGLE_SERVICE_ACCOUNT_JSON"]
        credentials = service_account.Credentials.from_service_account_file(
            key_path, scopes=SCOPES
        )
        _service = build("drive", "v3", credentials=credentials)
    return _service


@dataclass
class DriveFile:
    id: str
    name: str
    mime_type: str


# ---------------------------------------------------------------------------
# Listing / downloading
# ---------------------------------------------------------------------------

def list_new_files(folder_id: str) -> list[DriveFile]:
    """
    Lists files sitting directly inside `folder_id` (non-recursive —
    matches the "changes within subfolders won't trigger" behavior we
    relied on in the original n8n design, so files already moved into
    Processed/NeedsReview never get picked up again).
    """
    service = _get_service()
    query = (
        f"'{folder_id}' in parents and trashed = false "
        f"and mimeType != 'application/vnd.google-apps.folder'"
    )
    results = (
        service.files()
        .list(q=query, fields="files(id, name, mimeType)", pageSize=100)
        .execute()
    )
    files = results.get("files", [])
    return [DriveFile(id=f["id"], name=f["name"], mime_type=f["mimeType"]) for f in files]


def download_file(file_id: str) -> bytes:
    service = _get_service()
    request = service.files().get_media(fileId=file_id)
    buffer = io.BytesIO()
    downloader = MediaIoBaseDownload(buffer, request)
    done = False
    while not done:
        _, done = downloader.next_chunk()
    return buffer.getvalue()


# ---------------------------------------------------------------------------
# Find-or-create subfolders
# ---------------------------------------------------------------------------

def find_or_create_subfolder(parent_folder_id: str, name: str, owner_email: str | None = None) -> str:
    """
    Returns the folder ID of `name` inside `parent_folder_id`,
    creating it (and sharing it with owner_email) if it doesn't exist.
    """
    service = _get_service()
    query = (
        f"'{parent_folder_id}' in parents and trashed = false "
        f"and mimeType = 'application/vnd.google-apps.folder' "
        f"and name = '{name}'"
    )
    results = service.files().list(q=query, fields="files(id, name)").execute()
    existing = results.get("files", [])
    if existing:
        return existing[0]["id"]

    metadata = {
        "name": name,
        "mimeType": "application/vnd.google-apps.folder",
        "parents": [parent_folder_id],
    }
    folder = service.files().create(body=metadata, fields="id").execute()
    folder_id = folder["id"]

    if owner_email:
        _share_with_owner(folder_id, owner_email)

    return folder_id


def find_or_create_root_folder(name: str, owner_email: str | None = None) -> str:
    """
    Same as find_or_create_subfolder, but searches/creates at the
    Drive root (My Drive) rather than inside a specific parent.
    Used for the top-level "Jaxfor - Finance Automation" output folder.
    """
    service = _get_service()
    query = (
        f"'root' in parents and trashed = false "
        f"and mimeType = 'application/vnd.google-apps.folder' "
        f"and name = '{name}'"
    )
    results = service.files().list(q=query, fields="files(id, name)").execute()
    existing = results.get("files", [])
    if existing:
        return existing[0]["id"]

    metadata = {
        "name": name,
        "mimeType": "application/vnd.google-apps.folder",
    }
    folder = service.files().create(body=metadata, fields="id").execute()
    folder_id = folder["id"]

    if owner_email:
        _share_with_owner(folder_id, owner_email)

    return folder_id


# ---------------------------------------------------------------------------
# Moving files after processing
# ---------------------------------------------------------------------------

def move_file(file_id: str, source_folder_id: str, destination_folder_id: str) -> None:
    service = _get_service()
    service.files().update(
        fileId=file_id,
        addParents=destination_folder_id,
        removeParents=source_folder_id,
        fields="id, parents",
    ).execute()


# ---------------------------------------------------------------------------
# Sharing (service-account-created items need explicit sharing to be
# visible to the human owner's own Drive)
# ---------------------------------------------------------------------------

def _share_with_owner(file_id: str, owner_email: str, role: str = "writer") -> None:
    service = _get_service()
    permission = {"type": "user", "role": role, "emailAddress": owner_email}
    service.permissions().create(
        fileId=file_id, body=permission, sendNotificationEmail=False
    ).execute()


def share_with_owner(file_id: str, owner_email: str, role: str = "writer") -> None:
    """Public wrapper — used by sheets.py when it creates the spreadsheet."""
    _share_with_owner(file_id, owner_email, role)