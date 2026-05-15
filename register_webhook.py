"""
One-time setup: registers a webhook in Shopify that sends paid orders
to our Railway server. Run this once. Re-running will not create duplicates
— Shopify deduplicates by topic + callback URL.
"""

import os
import sys
import json
import requests
from dotenv import load_dotenv

load_dotenv()

STORE_DOMAIN  = os.getenv("SHOPIFY_STORE_DOMAIN")
CLIENT_ID     = os.getenv("SHOPIFY_CLIENT_ID")
CLIENT_SECRET = os.getenv("SHOPIFY_CLIENT_SECRET")

if not (STORE_DOMAIN and CLIENT_ID and CLIENT_SECRET):
    print("❌ Missing Shopify credentials in .env")
    sys.exit(1)

WEBHOOK_URL = "https://web-production-bc61b.up.railway.app/webhook/orders"
TOPIC       = "ORDERS_PAID"

API_VERSION = "2026-01"
GRAPHQL_URL = f"https://{STORE_DOMAIN}/admin/api/{API_VERSION}/graphql.json"


def get_token():
    url = f"https://{STORE_DOMAIN}/admin/oauth/access_token"
    r = requests.post(url,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        data={"client_id": CLIENT_ID, "client_secret": CLIENT_SECRET,
              "grant_type": "client_credentials"}, timeout=15)
    r.raise_for_status()
    return r.json()["access_token"]


def gql(token, query, variables=None):
    r = requests.post(GRAPHQL_URL,
        headers={"X-Shopify-Access-Token": token,
                 "Content-Type": "application/json"},
        json={"query": query, "variables": variables or {}}, timeout=20)
    r.raise_for_status()
    payload = r.json()
    if "errors" in payload:
        raise RuntimeError(json.dumps(payload["errors"], indent=2))
    return payload["data"]


# 1. List existing webhooks
LIST_Q = """
{
  webhookSubscriptions(first: 50) {
    edges { node { id topic endpoint { __typename ... on WebhookHttpEndpoint { callbackUrl } } } }
  }
}
"""

# 2. Create
CREATE_Q = """
mutation create($topic: WebhookSubscriptionTopic!, $sub: WebhookSubscriptionInput!) {
  webhookSubscriptionCreate(topic: $topic, webhookSubscription: $sub) {
    userErrors { field message }
    webhookSubscription { id topic endpoint { __typename ... on WebhookHttpEndpoint { callbackUrl } } }
  }
}
"""


def main():
    print("🔑 Authenticating...")
    token = get_token()

    print("📋 Checking existing webhooks...")
    data = gql(token, LIST_Q)
    existing = data["webhookSubscriptions"]["edges"]

    for edge in existing:
        node = edge["node"]
        ep = node.get("endpoint", {})
        url = ep.get("callbackUrl") if ep else None
        print(f"   - {node['topic']:25s}  {url}")
        if node["topic"] == TOPIC and url == WEBHOOK_URL:
            print()
            print("ℹ️  Webhook already registered. Nothing to do.")
            return

    print()
    print(f"📡 Creating webhook:")
    print(f"   Topic: {TOPIC}")
    print(f"   URL:   {WEBHOOK_URL}")
    result = gql(token, CREATE_Q, {
        "topic": TOPIC,
        "sub": {
            "callbackUrl": WEBHOOK_URL,
            "format": "JSON",
        },
    })
    errs = result["webhookSubscriptionCreate"]["userErrors"]
    if errs:
        print("❌ Errors:")
        for e in errs:
            print(f"   - {e['field']}: {e['message']}")
        sys.exit(1)

    sub = result["webhookSubscriptionCreate"]["webhookSubscription"]
    print()
    print("✅ Webhook registered.")
    print(f"   ID:    {sub['id']}")
    print(f"   Topic: {sub['topic']}")
    print()
    print("⚠️  Next: find this webhook's signing secret in the Shopify admin")
    print("   and add it to Railway as SHOPIFY_WEBHOOK_SECRET.")


if __name__ == "__main__":
    main()
