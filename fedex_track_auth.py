"""
FedEx OAuth for the TRACK API project (separate from the Ship project).

The Track API has its own client_id / secret because FedEx splits
"Ships with FedEx" projects from "Basic Integrated Visibility" projects.

Run directly to test:
    python fedex_track_auth.py
"""

import os
import sys
import requests
from dotenv import load_dotenv

load_dotenv()

CLIENT_ID     = os.getenv("FEDEX_TRACK_CLIENT_ID")
CLIENT_SECRET = os.getenv("FEDEX_TRACK_CLIENT_SECRET")
ENV           = os.getenv("FEDEX_ENVIRONMENT", "sandbox").lower()

BASE_URL = {
    "sandbox":    "https://apis-sandbox.fedex.com",
    "production": "https://apis.fedex.com",
}.get(ENV, "https://apis-sandbox.fedex.com")


def get_track_token():
    """
    Exchange Track project API Key + Secret for an access token.
    Returns dict with token, expires_in, scope, type.
    """
    if not (CLIENT_ID and CLIENT_SECRET):
        raise RuntimeError(
            "Missing FedEx Track credentials. "
            "Need FEDEX_TRACK_CLIENT_ID and FEDEX_TRACK_CLIENT_SECRET in .env"
        )

    url = f"{BASE_URL}/oauth/token"
    headers = {"Content-Type": "application/x-www-form-urlencoded"}
    body = {
        "grant_type":    "client_credentials",
        "client_id":     CLIENT_ID,
        "client_secret": CLIENT_SECRET,
    }

    response = requests.post(url, headers=headers, data=body, timeout=15)
    response.raise_for_status()

    data = response.json()
    token = data.get("access_token")
    if not token:
        raise RuntimeError(f"No access_token in response: {data}")

    return {
        "token":      token,
        "expires_in": data.get("expires_in"),
        "scope":      data.get("scope"),
        "type":       data.get("token_type"),
    }


if __name__ == "__main__":
    print(f"🔐 Authenticating with FedEx TRACK API ({ENV})...")
    print(f"   Endpoint: {BASE_URL}/oauth/token")
    print()

    try:
        result = get_track_token()
    except requests.exceptions.HTTPError as e:
        print(f"❌ HTTP error: {e}")
        print(f"   Status:   {e.response.status_code}")
        print(f"   Response: {e.response.text[:500]}")
        sys.exit(1)
    except Exception as e:
        print(f"❌ Failed: {e}")
        sys.exit(1)

    print(f"✅ Track API token acquired.")
    print(f"   Type:       {result['type']}")
    print(f"   Scope:      {result['scope']}")
    print(f"   Expires in: {result['expires_in']} seconds")
    print(f"   Token:      {result['token'][:20]}...{result['token'][-10:]}")
    print()
    print("Track API auth works. Ready to build the tracking webhook.")
