"""
oauth_drive.py
---------------
User-authenticated (OAuth 2.0) Drive/Sheets access, used ONLY for
creating the output folder + spreadsheet under the customer's own
Google account.

Why this exists: a service account has ZERO Drive storage quota of
its own. On a personal (non-Workspace) Google account there is no
Shared Drive to fall back on either, so a service account can never
create a new folder or file that persists — it fails with
`storageQuotaExceeded` regardless of which folder you try to parent
it under. The only way to auto-create new Drive objects on a personal
account is to act as a real, quota-bearing user — hence this module.

Everything else in the pipeline (watching "J - Invoices", downloading
invoices, moving processed files) keeps using the service account via
app.drive — this module is deliberately narrow in scope: it only
creates/writes the output folder and spreadsheet.

One-time setup (per deployment):
  1. In the same Google Cloud project, create an OAuth 2.0 Client ID
     of type "Desktop app" (Cloud Console -> APIs & Services ->
     Credentials -> Create Credentials -> OAuth client ID).
  2. Download it and save as credentials/client_secret.json.
  3. The first call to get_credentials() opens a browser window
     asking you to sign in (as the Drive account that owns
     "J - Invoices") and approve access. This creates
     credentials/token.json, which is then reused and silently
     refreshed on every later run — no repeated browser logins,
     including on a headless server, as long as token.json is
     present there. Treat token.json like a secret (it's in
     .gitignore already, same as the service account key).

Env vars (both optional, sensible defaults shown):
  OAUTH_CLIENT_SECRET_JSON   path to client_secret.json
                             (default: credentials/client_secret.json)
  OAUTH_TOKEN_JSON           path to the cached token
                             (default: credentials/token.json)
"""

from __future__ import annotations

import os
import threading

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build

SCOPES = [
    "https://www.googleapis.com/auth/drive",
    "https://www.googleapis.com/auth/spreadsheets",
]

# googleapiclient's transport (httplib2) is NOT thread-safe — Google's
# own docs say to give each thread its own service/http instance
# rather than share one across threads. This module used to cache a
# single process-wide service object, which is fine for a
# single-threaded script but not for a web server: FastAPI runs sync
# endpoints in a thread pool, and the dashboard's summary/invoices
# calls plus the background scheduler's poll thread could all end up
# calling into the same shared httplib2 connection at once. In
# production that caused a hard `Fatal Python error: Segmentation
# fault`, crashing the whole process (and the scheduler with it).
#
# Fix: cache one service instance per thread instead of one globally.
# The token file itself is still shared/reused normally — only the
# built service object (and its underlying HTTP connection) is
# per-thread.
_local = threading.local()
_token_lock = threading.Lock()


def _client_secret_path() -> str:
    return os.environ.get("OAUTH_CLIENT_SECRET_JSON", "credentials/client_secret.json")


def _token_path() -> str:
    return os.environ.get("OAUTH_TOKEN_JSON", "credentials/token.json")


def get_credentials() -> Credentials:
    """
    Loads the cached user token, refreshing it silently if expired.
    Runs the interactive consent flow (opens a browser) only if no
    valid/refreshable token exists yet. Persists the token back to
    disk after every refresh so the next run doesn't need to touch
    the network for auth at all until the refresh token itself is
    revoked.
    """
    # Guards the token file read/refresh/write so two threads don't
    # race to refresh and write credentials/token.json at the same
    # time (which could corrupt it or waste a refresh call). Building
    # the actual service object below stays per-thread — this lock
    # only covers the cheap, infrequent token bookkeeping.
    with _token_lock:
        token_path = _token_path()
        creds: Credentials | None = None

        if os.path.exists(token_path):
            creds = Credentials.from_authorized_user_file(token_path, SCOPES)

        if not creds or not creds.valid:
            if creds and creds.expired and creds.refresh_token:
                creds.refresh(Request())
            else:
                client_secret_path = _client_secret_path()
                if not os.path.exists(client_secret_path):
                    raise RuntimeError(
                        f"OAuth client secret not found at '{client_secret_path}'. "
                        "Create a Desktop-app OAuth Client ID in the Cloud Console "
                        "and save its JSON there (see app/oauth_drive.py docstring)."
                    )
                flow = InstalledAppFlow.from_client_secrets_file(client_secret_path, SCOPES)
                # access_type="offline" + prompt="consent" guarantees a
                # refresh_token is issued (not just a short-lived access
                # token), so later runs — including on a headless server —
                # never need the browser again.
                creds = flow.run_local_server(port=0, access_type="offline", prompt="consent")

            os.makedirs(os.path.dirname(token_path) or ".", exist_ok=True)
            with open(token_path, "w") as f:
                f.write(creds.to_json())

    return creds


def get_drive_service():
    """
    Returns a Drive service instance private to the calling thread.
    See the module-level note above on why this is thread-local
    rather than a single shared/global instance.
    """
    if not hasattr(_local, "drive_service") or _local.drive_service is None:
        _local.drive_service = build("drive", "v3", credentials=get_credentials())
    return _local.drive_service


def get_sheets_service():
    """
    Returns a Sheets service instance private to the calling thread.
    See the module-level note above on why this is thread-local
    rather than a single shared/global instance.
    """
    if not hasattr(_local, "sheets_service") or _local.sheets_service is None:
        _local.sheets_service = build("sheets", "v4", credentials=get_credentials())
    return _local.sheets_service