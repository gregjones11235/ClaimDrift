"""One-time Gmail OAuth for local sending (replaces apps/dispatcher/scripts/gmail_oauth_setup.py, whose OAuth client
belonged to the deleted GCP project). Sign in as the sender account in a browser; no password is stored anywhere.

Prereqs (GCP Console, project gen-lang-client-0220563082 or any project you own):
  1. APIs & Services -> Library -> Gmail API -> Enable.
  2. OAuth consent screen: External; add claimdriftnotifier@gmail.com as a test user. Publishing the app ("In production",
     unverified is fine for your own account) avoids the 7-day refresh-token expiry of Testing apps.
  3. Credentials -> Create credentials -> OAuth client ID -> Desktop app -> download JSON to
     apps/dispatcher/scripts/client_secret.json (gitignored).

Usage (WSL, repo root):
  uv run python claimdrift/scripts/gmail_oauth_local.py
  -> prints a URL; open it in the Windows browser, sign in as claimdriftnotifier@gmail.com, allow "send email".
  -> writes .gmail_token.json (gitignored) and appends GMAIL_TOKEN_FILE=... to .env.
"""
from __future__ import annotations

import sys
from pathlib import Path

from google_auth_oauthlib.flow import InstalledAppFlow

ROOT = Path(__file__).resolve().parents[2]
CLIENT_SECRET = ROOT / "apps" / "dispatcher" / "scripts" / "client_secret.json"
TOKEN = ROOT / "agents" / ".gmail_token.json"
ENV = ROOT / ".env"
SCOPES = ["https://www.googleapis.com/auth/gmail.send"]


def main() -> None:
    if not CLIENT_SECRET.exists():
        sys.exit(f"{CLIENT_SECRET} not found: download the Desktop-app OAuth client JSON first (see the docstring).")
    flow = InstalledAppFlow.from_client_secrets_file(str(CLIENT_SECRET), SCOPES)
    print("Open the URL below in your browser and sign in as claimdriftnotifier@gmail.com.\n")
    creds = flow.run_local_server(port=8765, open_browser=False, prompt="consent", access_type="offline")
    if not creds.refresh_token:
        sys.exit("No refresh_token returned: revoke the app at https://myaccount.google.com/permissions and re-run.")
    TOKEN.write_text(creds.to_json(), encoding="utf-8")
    env = ENV.read_text(encoding="utf-8") if ENV.exists() else ""
    if "GMAIL_TOKEN_FILE=" not in env:
        with ENV.open("a", encoding="utf-8") as f:
            f.write(("" if env.endswith("\n") or not env else "\n") + f"GMAIL_TOKEN_FILE={TOKEN}\n")
    print(f"\nSaved {TOKEN}; GMAIL_TOKEN_FILE is set in {ENV}.")


if __name__ == "__main__":
    main()
