"""Google OAuth for a personal account (installed-app flow).

First run opens a browser to grant access; the refresh token is cached in a
0600 file and reused after that. Scopes are requested per integration and kept
minimal (see each integration's SCOPES).
"""
from __future__ import annotations

import os
from pathlib import Path

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials


class GoogleAuthError(RuntimeError):
    pass


def get_credentials(scopes: list[str], client_secret: Path, token_path: Path,
                    interactive: bool = True) -> Credentials:
    creds = None
    if token_path.exists():
        creds = Credentials.from_authorized_user_file(str(token_path), scopes)
        # A token granted for fewer scopes can't be widened by refreshing.
        if creds and not set(scopes) <= set(creds.scopes or []):
            creds = None
    if creds and creds.valid:
        return creds
    if creds and creds.expired and creds.refresh_token:
        creds.refresh(Request())
    else:
        if not interactive:
            raise GoogleAuthError("Google isn't connected yet. Run `python -m quasi connect google` once.")
        if not client_secret.exists():
            raise GoogleAuthError(
                f"OAuth client file not found at {client_secret}. "
                "Create a Desktop OAuth client in Google Cloud and save its JSON there (see quasi/README.md).")
        from google_auth_oauthlib.flow import InstalledAppFlow
        flow = InstalledAppFlow.from_client_secrets_file(str(client_secret), scopes)
        creds = flow.run_local_server(port=0, open_browser=True)
    token_path.parent.mkdir(parents=True, exist_ok=True)
    token_path.write_text(creds.to_json())
    os.chmod(token_path, 0o600)
    return creds
