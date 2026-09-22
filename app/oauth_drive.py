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

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build

SCOPES = [
    "https://www.googleapis.com/auth/drive",
    "https://www.googleapis.com/auth/spreadsheets",
]

_drive_service = None
_sheets_service = None


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
    global _drive_service
    if _drive_service is None:
        _drive_service = build("drive", "v3", credentials=get_credentials())
    return _drive_service


def get_sheets_service():
    global _sheets_service
    if _sheets_service is None:
        _sheets_service = build("sheets", "v4", credentials=get_credentials())
    return _sheets_service