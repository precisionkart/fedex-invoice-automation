"""
FedEx OAuth — get an access token.

FedEx uses OAuth 2.0 client credentials grant. We swap our
API Key + Secret for a short-lived bearer token (valid ~1 hour),
which we then send with every request to other FedEx APIs.

Sandbox base URL:    https://apis-sandbox.fedex.com
Production base URL: https://apis.fedex.com

Run directly to test:
    python fedex_auth.py
"""

import os
import sys
import requests
from dotenv import load_dotenv

load_dotenv()

CLIENT_ID     = os.getenv("FEDEX_CLIENT_ID")
CLIENT_SECRET = os.getenv("FEDEX_CLIENT_SECRET")
ENV           = os.getenv("FEDEX_ENVIRONMENT", "sandbox").lower()

BASE_URL = {
    "sandbox":    "https://apis-sandbox.fedex.com",
    "production": "https://apis.fedex.com",
}.get(ENV, "https://apis-sandbox.fedex.com")


def get_fedex_token():
    """
    Exchange API Key + Secret for an access token.
    FedEx returns a JSON blob with 'access_token' and 'expires_in' (seconds).
    """
    if not (CLIENT_ID and CLIENT_SECRET):
        raise RuntimeError(
            "Missing FedEx credentials. "
            "Need FEDEX_CLIENT_ID and FEDEX_CLIENT_SECRET in .env"
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
    print(f"🔐 Authenticating with FedEx ({ENV})...")
    print(f"   Endpoint: {BASE_URL}/oauth/token")
    print()

    try:
        result = get_fedex_token()
    except requests.exceptions.HTTPError as e:
        print(f"❌ HTTP error: {e}")
        print(f"   Status:   {e.response.status_code}")
        print(f"   Response: {e.response.text[:500]}")
        sys.exit(1)
    except Exception as e:
        print(f"❌ Failed: {e}")
        sys.exit(1)

    print(f"✅ Token acquired.")
    print(f"   Type:       {result['type']}")
    print(f"   Scope:      {result['scope']}")
    print(f"   Expires in: {result['expires_in']} seconds")
    print(f"   Token:      {result['token'][:20]}...{result['token'][-10:]}")
    print()
    print("FedEx auth works. Ready for the next step.")
