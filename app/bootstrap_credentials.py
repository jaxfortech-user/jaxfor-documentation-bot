"""
bootstrap_credentials.py
--------------------------
Writes secret JSON files (the service account key, the OAuth token)
to disk from environment variables, on platforms like Railway where
the filesystem is ephemeral and .gitignored files never make it into
the deploy.

Why this exists: app.drive and app.oauth_drive both read credentials
from files on disk (GOOGLE_SERVICE_ACCOUNT_JSON and OAUTH_TOKEN_JSON
paths). Locally that's fine — the files just sit in credentials/.
On Railway there's no way to upload a file directly, only environment
variables, so the actual JSON *content* is pasted into a Railway env
var and this module writes it out to the expected path at startup,
before anything else runs.

Locally, none of the *_CONTENT env vars are set, so this is a no-op —
existing local dev is completely unaffected.

Env vars this reads (all optional — each is only used if set):
  GOOGLE_SERVICE_ACCOUNT_JSON_CONTENT   raw JSON content of the
                                         service account key
  OAUTH_TOKEN_JSON_CONTENT              raw JSON content of the
                                         cached OAuth token
                                         (credentials/token.json) —
                                         see note below on how to
                                         get this value

Writes to whatever path GOOGLE_SERVICE_ACCOUNT_JSON / OAUTH_TOKEN_JSON
point at (same defaults as app.drive / app.oauth_drive use).

Note on OAUTH_TOKEN_JSON_CONTENT: generate credentials/token.json
locally first (run any script that calls app.oauth_drive.get_credentials()
and complete the one-time browser consent), then copy that file's
entire contents into this Railway env var. The client_secret.json
itself is NOT needed on Railway — an authorized-user token file
carries its own client_id/client_secret/refresh_token, enough to
self-refresh indefinitely without the browser flow ever running again
(until the refresh token itself is revoked).
"""

from __future__ import annotations

import logging
import os

logger = logging.getLogger("jaxfor.bootstrap_credentials")


def _write_if_content_env_set(content_env_var: str, target_path_env_var: str, default_path: str) -> None:
    content = os.environ.get(content_env_var)
    if not content:
        return  # not set — local dev, or file already provisioned some other way

    target_path = os.environ.get(target_path_env_var, default_path)
    os.makedirs(os.path.dirname(target_path) or ".", exist_ok=True)

    with open(target_path, "w") as f:
        f.write(content)

    logger.info("Wrote %s from %s (%d bytes)", target_path, content_env_var, len(content))


def bootstrap_credentials() -> None:
    """
    Call this once, as early as possible at process startup — before
    any code that reads GOOGLE_SERVICE_ACCOUNT_JSON or OAUTH_TOKEN_JSON
    from disk (i.e. before importing app.pipeline).
    """
    _write_if_content_env_set(
        "GOOGLE_SERVICE_ACCOUNT_JSON_CONTENT",
        "GOOGLE_SERVICE_ACCOUNT_JSON",
        "credentials/service_account.json",
    )
    _write_if_content_env_set(
        "OAUTH_TOKEN_JSON_CONTENT",
        "OAUTH_TOKEN_JSON",
        "credentials/token.json",
    )