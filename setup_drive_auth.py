"""
ONE-TIME SETUP: authorise this app to upload PDFs to Drive AND
read/write the FedEx Shipping Log Sheet.

Scopes:
  drive.file       — only files this app creates (label + invoice PDFs)
  spreadsheets     — read/write Google Sheets shared with the user
"""

import os
import sys
from google_auth_oauthlib.flow import InstalledAppFlow

SCOPES = [
    "https://www.googleapis.com/auth/drive.file",
    "https://www.googleapis.com/auth/spreadsheets",
]

CLIENT_SECRETS_FILE = "google-oauth-client.json"
TOKEN_FILE = "google-token.json"


def main():
    if not os.path.exists(CLIENT_SECRETS_FILE):
        print(f"❌ Can't find {CLIENT_SECRETS_FILE}")
        sys.exit(1)

    print("🔐 Re-authorising with Drive + Sheets scopes...")
    print("   Sign in with the same Google account you used before.")
    print()

    flow = InstalledAppFlow.from_client_secrets_file(CLIENT_SECRETS_FILE, SCOPES)
    creds = flow.run_local_server(port=0)

    with open(TOKEN_FILE, "w") as f:
        f.write(creds.to_json())

    print(f"✅ Token saved to {TOKEN_FILE}")
    print("   Now has Drive + Sheets permissions.")


if __name__ == "__main__":
    main()
