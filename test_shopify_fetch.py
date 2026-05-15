"""
Diagnostic — Dev Dashboard custom app, client credentials grant, GraphQL Admin API.
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
    print("❌ Missing credentials. Need SHOPIFY_STORE_DOMAIN, SHOPIFY_CLIENT_ID, SHOPIFY_CLIENT_SECRET in .env")
    sys.exit(1)

API_VERSION = "2026-01"
GRAPHQL_URL = f"https://{STORE_DOMAIN}/admin/api/{API_VERSION}/graphql.json"


def get_access_token():
    """
    Exchange Client ID + Secret for an access token.
    Shopify requires form-encoded (NOT JSON) for this endpoint.
    """
    url = f"https://{STORE_DOMAIN}/admin/oauth/access_token"
    headers = {"Content-Type": "application/x-www-form-urlencoded"}
    body = {
        "client_id":     CLIENT_ID,
        "client_secret": CLIENT_SECRET,
        "grant_type":    "client_credentials",
    }
    response = requests.post(url, headers=headers, data=body, timeout=15)
    response.raise_for_status()
    data = response.json()
    token = data.get("access_token")
    if not token:
        raise RuntimeError(f"No access_token in response: {data}")
    return token


def graphql(token, query):
    """Run a GraphQL query against the Admin API."""
    headers = {
        "X-Shopify-Access-Token": token,
        "Content-Type": "application/json",
    }
    response = requests.post(GRAPHQL_URL, headers=headers,
                             json={"query": query}, timeout=15)
    response.raise_for_status()
    payload = response.json()
    if "errors" in payload:
        raise RuntimeError(f"GraphQL errors: {payload['errors']}")
    return payload["data"]


def get_recent_orders(token, limit=5):
    query = f"""
    {{
      orders(first: {limit}, query: "financial_status:paid", reverse: true, sortKey: CREATED_AT) {{
        edges {{
          node {{
            name
            id
            createdAt
            currencyCode
            totalPriceSet {{ shopMoney {{ amount currencyCode }} }}
            shippingLine {{ title originalPriceSet {{ shopMoney {{ amount currencyCode }} }} }}
            shippingAddress {{
              name address1 address2 city zip country countryCodeV2 phone
            }}
            lineItems(first: 20) {{
              edges {{
                node {{
                  title quantity sku variantTitle
                  variant {{
                    id
                    metafields(first: 20) {{
                      edges {{ node {{ namespace key value }} }}
                    }}
                  }}
                }}
              }}
            }}
          }}
        }}
      }}
    }}
    """
    return graphql(token, query)


def main():
    print(f"🔌 Connecting to {STORE_DOMAIN}...")
    print()

    print("🔑 Fetching access token (Client Credentials Grant)...")
    try:
        token = get_access_token()
        print(f"   ✅ Token acquired (starts with: {token[:10]}...)")
    except requests.exceptions.HTTPError as e:
        print(f"❌ Token fetch failed: {e}")
        print(f"   Status: {e.response.status_code}")
        body = e.response.text
        # try to extract JSON error if present
        try:
            err = e.response.json()
            print(f"   Error: {json.dumps(err, indent=2)[:500]}")
        except Exception:
            print(f"   Body (first 300 chars): {body[:300]}")
        sys.exit(1)
    except Exception as e:
        print(f"❌ Unexpected error: {e}")
        sys.exit(1)
    print()

    print("📥 Fetching recent paid orders via GraphQL...")
    try:
        data = get_recent_orders(token, limit=5)
    except requests.exceptions.HTTPError as e:
        print(f"❌ Order fetch failed: {e}")
        print(f"   Status: {e.response.status_code}")
        print(f"   Body: {e.response.text[:500]}")
        sys.exit(1)
    except Exception as e:
        print(f"❌ Unexpected error: {e}")
        sys.exit(1)

    orders = [edge["node"] for edge in data["orders"]["edges"]]
    if not orders:
        print("⚠️  No paid orders found in this store yet.")
        sys.exit(0)

    print(f"✅ Found {len(orders)} recent paid order(s).")
    print()
    print("=" * 70)

    for o in orders:
        print(f"\n📦 Order {o['name']}")
        print(f"   Created:   {o['createdAt'][:10]}")
        print(f"   Currency:  {o['currencyCode']}")
        tot = o.get("totalPriceSet", {}).get("shopMoney", {})
        print(f"   Total:     {tot.get('amount')} {tot.get('currencyCode')}")

        sl = o.get("shippingLine")
        if sl:
            sp = sl.get("originalPriceSet", {}).get("shopMoney", {})
            print(f"   Shipping:  {sp.get('amount')} {sp.get('currencyCode')}  ({sl.get('title')})")

        addr = o.get("shippingAddress") or {}
        if addr:
            print(f"   Ship to:   {addr.get('name', '')}")
            print(f"              {addr.get('address1', '')}")
            if addr.get('address2'):
                print(f"              {addr.get('address2', '')}")
            print(f"              {addr.get('zip', '')} {addr.get('city', '')}")
            print(f"              {addr.get('country', '')} ({addr.get('countryCodeV2', '')})")
            if addr.get('phone'):
                print(f"              ☎ {addr.get('phone', '')}")

        line_edges = o.get("lineItems", {}).get("edges", [])
        print(f"   Line items ({len(line_edges)}):")
        for le in line_edges:
            li = le["node"]
            print(f"      • {li['quantity']}× {li['title']}")
            if li.get('variantTitle'):
                print(f"        Variant: {li['variantTitle']}")
            print(f"        SKU: {li.get('sku') or '(none)'}")
            variant = li.get("variant") or {}
            mfs = variant.get("metafields", {}).get("edges", [])
            if mfs:
                print(f"        Metafields ({len(mfs)}):")
                for mfe in mfs:
                    mf = mfe["node"]
                    print(f"          - {mf['namespace']}.{mf['key']} = {mf['value']}")
            else:
                print(f"        ⚠️  No metafields set on this variant.")
        print("-" * 70)

    print()
    print("✅ Test complete.")


if __name__ == "__main__":
    main()
EOFcat > test_shopify_fetch.py << 'EOF'
"""
Diagnostic — Dev Dashboard custom app, client credentials grant, GraphQL Admin API.
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
    print("❌ Missing credentials. Need SHOPIFY_STORE_DOMAIN, SHOPIFY_CLIENT_ID, SHOPIFY_CLIENT_SECRET in .env")
    sys.exit(1)

API_VERSION = "2026-01"
GRAPHQL_URL = f"https://{STORE_DOMAIN}/admin/api/{API_VERSION}/graphql.json"


def get_access_token():
    """
    Exchange Client ID + Secret for an access token.
    Shopify requires form-encoded (NOT JSON) for this endpoint.
    """
    url = f"https://{STORE_DOMAIN}/admin/oauth/access_token"
    headers = {"Content-Type": "application/x-www-form-urlencoded"}
    body = {
        "client_id":     CLIENT_ID,
        "client_secret": CLIENT_SECRET,
        "grant_type":    "client_credentials",
    }
    response = requests.post(url, headers=headers, data=body, timeout=15)
    response.raise_for_status()
    data = response.json()
    token = data.get("access_token")
    if not token:
        raise RuntimeError(f"No access_token in response: {data}")
    return token


def graphql(token, query):
    """Run a GraphQL query against the Admin API."""
    headers = {
        "X-Shopify-Access-Token": token,
        "Content-Type": "application/json",
    }
    response = requests.post(GRAPHQL_URL, headers=headers,
                             json={"query": query}, timeout=15)
    response.raise_for_status()
    payload = response.json()
    if "errors" in payload:
        raise RuntimeError(f"GraphQL errors: {payload['errors']}")
    return payload["data"]


def get_recent_orders(token, limit=5):
    query = f"""
    {{
      orders(first: {limit}, query: "financial_status:paid", reverse: true, sortKey: CREATED_AT) {{
        edges {{
          node {{
            name
            id
            createdAt
            currencyCode
            totalPriceSet {{ shopMoney {{ amount currencyCode }} }}
            shippingLine {{ title originalPriceSet {{ shopMoney {{ amount currencyCode }} }} }}
            shippingAddress {{
              name address1 address2 city zip country countryCodeV2 phone
            }}
            lineItems(first: 20) {{
              edges {{
                node {{
                  title quantity sku variantTitle
                  variant {{
                    id
                    metafields(first: 20) {{
                      edges {{ node {{ namespace key value }} }}
                    }}
                  }}
                }}
              }}
            }}
          }}
        }}
      }}
    }}
    """
    return graphql(token, query)


def main():
    print(f"🔌 Connecting to {STORE_DOMAIN}...")
    print()

    print("🔑 Fetching access token (Client Credentials Grant)...")
    try:
        token = get_access_token()
        print(f"   ✅ Token acquired (starts with: {token[:10]}...)")
    except requests.exceptions.HTTPError as e:
        print(f"❌ Token fetch failed: {e}")
        print(f"   Status: {e.response.status_code}")
        body = e.response.text
        # try to extract JSON error if present
        try:
            err = e.response.json()
            print(f"   Error: {json.dumps(err, indent=2)[:500]}")
        except Exception:
            print(f"   Body (first 300 chars): {body[:300]}")
        sys.exit(1)
    except Exception as e:
        print(f"❌ Unexpected error: {e}")
        sys.exit(1)
    print()

    print("📥 Fetching recent paid orders via GraphQL...")
    try:
        data = get_recent_orders(token, limit=5)
    except requests.exceptions.HTTPError as e:
        print(f"❌ Order fetch failed: {e}")
        print(f"   Status: {e.response.status_code}")
        print(f"   Body: {e.response.text[:500]}")
        sys.exit(1)
    except Exception as e:
        print(f"❌ Unexpected error: {e}")
        sys.exit(1)

    orders = [edge["node"] for edge in data["orders"]["edges"]]
    if not orders:
        print("⚠️  No paid orders found in this store yet.")
        sys.exit(0)

    print(f"✅ Found {len(orders)} recent paid order(s).")
    print()
    print("=" * 70)

    for o in orders:
        print(f"\n📦 Order {o['name']}")
        print(f"   Created:   {o['createdAt'][:10]}")
        print(f"   Currency:  {o['currencyCode']}")
        tot = o.get("totalPriceSet", {}).get("shopMoney", {})
        print(f"   Total:     {tot.get('amount')} {tot.get('currencyCode')}")

        sl = o.get("shippingLine")
        if sl:
            sp = sl.get("originalPriceSet", {}).get("shopMoney", {})
            print(f"   Shipping:  {sp.get('amount')} {sp.get('currencyCode')}  ({sl.get('title')})")

        addr = o.get("shippingAddress") or {}
        if addr:
            print(f"   Ship to:   {addr.get('name', '')}")
            print(f"              {addr.get('address1', '')}")
            if addr.get('address2'):
                print(f"              {addr.get('address2', '')}")
            print(f"              {addr.get('zip', '')} {addr.get('city', '')}")
            print(f"              {addr.get('country', '')} ({addr.get('countryCodeV2', '')})")
            if addr.get('phone'):
                print(f"              ☎ {addr.get('phone', '')}")

        line_edges = o.get("lineItems", {}).get("edges", [])
        print(f"   Line items ({len(line_edges)}):")
        for le in line_edges:
            li = le["node"]
            print(f"      • {li['quantity']}× {li['title']}")
            if li.get('variantTitle'):
                print(f"        Variant: {li['variantTitle']}")
            print(f"        SKU: {li.get('sku') or '(none)'}")
            variant = li.get("variant") or {}
            mfs = variant.get("metafields", {}).get("edges", [])
            if mfs:
                print(f"        Metafields ({len(mfs)}):")
                for mfe in mfs:
                    mf = mfe["node"]
                    print(f"          - {mf['namespace']}.{mf['key']} = {mf['value']}")
            else:
                print(f"        ⚠️  No metafields set on this variant.")
        print("-" * 70)

    print()
    print("✅ Test complete.")


if __name__ == "__main__":
    main()
