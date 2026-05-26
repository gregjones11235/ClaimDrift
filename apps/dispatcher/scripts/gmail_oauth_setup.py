"""One-time OAuth setup for the dispatcher's Gmail send capability.

Run this on your local machine. It:
  1. Opens a browser asking you to consent (sign in as claimdriftnotifier@gmail.com).
  2. Captures the refresh token returned by Google.
  3. Writes refresh_token + client_id + client_secret to Secret Manager.

After this runs successfully, the Cloud Run dispatcher reads these three secrets
at startup and uses them to send mail via Gmail API. You should NOT need to
re-run this unless the refresh token expires (External + Testing OAuth apps:
7-day expiry; re-run before each demo / judging window).

Prereqs:
  - apps/dispatcher/scripts/client_secret.json downloaded from GCP Console
    (Desktop app OAuth client)
  - Gmail API enabled on the GCP project
  - You can authenticate to GCP (gcloud auth application-default login)

Usage:
  cd apps/dispatcher/scripts
  uv run python gmail_oauth_setup.py
"""

from __future__ import annotations

import sys
from pathlib import Path

from google.cloud import secretmanager
from google_auth_oauthlib.flow import InstalledAppFlow

PROJECT_ID = "tensile-topic-496519-i1"
EXPECTED_EMAIL = "claimdriftnotifier@gmail.com"

# Minimum scope to send mail. Do NOT request more than needed.
SCOPES = ["https://www.googleapis.com/auth/gmail.send"]

CLIENT_SECRET_PATH = Path(__file__).parent / "client_secret.json"

SECRETS_TO_WRITE = {
    "gmail-refresh-token": "refresh_token",
    "gmail-oauth-client-id": "client_id",
    "gmail-oauth-client-secret": "client_secret",
}


def run_oauth_flow() -> "google.oauth2.credentials.Credentials":
    if not CLIENT_SECRET_PATH.exists():
        sys.exit(
            f"ERROR: {CLIENT_SECRET_PATH} not found. "
            "Download the Desktop-app OAuth client JSON from GCP Console first."
        )

    flow = InstalledAppFlow.from_client_secrets_file(
        str(CLIENT_SECRET_PATH), SCOPES
    )
    # port=0 -> OS picks a free port; loopback redirect goes to http://localhost:<port>/
    print("Opening browser for OAuth consent...")
    print(f"Sign in as: {EXPECTED_EMAIL}")
    print()
    creds = flow.run_local_server(
        port=0,
        prompt="consent",  # force consent screen even if previously consented; ensures we get a refresh_token
        access_type="offline",  # required for refresh_token to be returned
    )

    if not creds.refresh_token:
        sys.exit(
            "ERROR: No refresh_token returned. This usually means you previously "
            "consented to this OAuth client; try revoking access at "
            "https://myaccount.google.com/permissions and re-running."
        )

    return creds


def upsert_secret(client: secretmanager.SecretManagerServiceClient, name: str, value: str) -> None:
    parent = f"projects/{PROJECT_ID}"
    secret_path = f"{parent}/secrets/{name}"

    # Create the secret container if it doesn't exist (idempotent).
    try:
        client.create_secret(
            request={
                "parent": parent,
                "secret_id": name,
                "secret": {"replication": {"automatic": {}}},
            }
        )
        print(f"  created secret container: {name}")
    except Exception as e:
        # google.api_core.exceptions.AlreadyExists is the expected case on re-run.
        if "AlreadyExists" not in type(e).__name__ and "already exists" not in str(e).lower():
            raise
        print(f"  secret container exists: {name}")

    # Always add a new version (old versions remain accessible by explicit version).
    response = client.add_secret_version(
        request={"parent": secret_path, "payload": {"data": value.encode("utf-8")}}
    )
    print(f"  added version: {response.name}")


def main() -> None:
    creds = run_oauth_flow()
    print()
    print("OAuth flow complete.")
    print(f"  client_id:     {creds.client_id[:40]}...")
    print(f"  refresh_token: {creds.refresh_token[:20]}... (len={len(creds.refresh_token)})")
    print()

    print(f"Writing 3 secrets to Secret Manager (project={PROJECT_ID})...")
    sm = secretmanager.SecretManagerServiceClient()
    values = {
        "refresh_token": creds.refresh_token,
        "client_id": creds.client_id,
        "client_secret": creds.client_secret,
    }
    for secret_name, value_key in SECRETS_TO_WRITE.items():
        upsert_secret(sm, secret_name, values[value_key])

    print()
    print("Done. Dispatcher (Step 6) will read these three secrets at startup.")
    print("You can delete apps/dispatcher/scripts/client_secret.json if you want;")
    print("Secret Manager now holds the durable credential (the refresh_token).")


if __name__ == "__main__":
    main()
