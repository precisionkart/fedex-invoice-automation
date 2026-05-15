"""
ONE-TIME SETUP: Authorise this app to upload PDFs to your Google Drive.

Run this script once. A browser window will open asking you to sign in
and grant Drive access. After you approve, a token file is saved
(google-token.json) and you never need to run this again.

After running successfully, all future invoice generations can upload
to Drive silently.
"""

import os
import sys
from google_auth_oauthlib.flow import InstalledAppFlow

SCOPES = ["https://www.googleapis.com/auth/drive.file"]
# 'drive.file' = the app can only see/modify files it creates itself.
# This is the minimum scope needed — it CANNOT read your other Drive files.

CLIENT_SECRETS_FILE = "google-oauth-client.json"
TOKEN_FILE = "google-token.json"


def main():
    if not os.path.exists(CLIENT_SECRETS_FILE):
        print(f"❌ Can't find {CLIENT_SECRETS_FILE} in this folder.")
        print("   Did you download the OAuth client JSON and rename it?")
        sys.exit(1)

    print("🔐 Starting one-time authorisation flow...")
    print("   A browser window will open shortly.")
    print("   Sign in with the Google account that owns the Drive folder.")
    print("   Then click 'Allow' to grant access.")
    print()

    flow = InstalledAppFlow.from_client_secrets_file(CLIENT_SECRETS_FILE, SCOPES)
    creds = flow.run_local_server(port=0)

    with open(TOKEN_FILE, "w") as f:
        f.write(creds.to_json())

    print()
    print(f"✅ Done! Refresh token saved to {TOKEN_FILE}")
    print("   You can now run generate_invoice.py and PDFs will auto-upload to Drive.")
    print("   This setup script does not need to be run again.")


if __name__ == "__main__":
    main()
