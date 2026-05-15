"""
Audit recent paid Shopify orders to find any that came through
during the credentials downtime and missed processing.
"""

import os
import requests
from dotenv import load_dotenv
from generate_invoice import get_access_token, GRAPHQL_URL

load_dotenv()

QUERY = """
{
  orders(first: 25, query: "financial_status:paid", reverse: true, sortKey: CREATED_AT) {
    edges {
      node {
        name
        createdAt
        displayFulfillmentStatus
        shippingAddress { countryCodeV2 }
        totalPriceSet { shopMoney { amount currencyCode } }
      }
    }
  }
}
"""

UK     = {"GB"}
EU     = {"AT","BE","BG","HR","CY","CZ","DK","EE","FI","FR","DE","GR","HU",
          "IE","IT","LV","LT","LU","MT","NL","PL","PT","RO","SK","SI","ES","SE"}
US     = {"US"}


def main():
    token = get_access_token()
    r = requests.post(
        GRAPHQL_URL,
        headers={"X-Shopify-Access-Token": token, "Content-Type": "application/json"},
        json={"query": QUERY},
        timeout=20,
    )
    r.raise_for_status()
    orders = [e["node"] for e in r.json()["data"]["orders"]["edges"]]

    print(f"\n{'Order':<14} {'Created':<19} {'Ctry':<5} {'Total':<14} {'Fulfilled?':<14} Action")
    print("─" * 88)

    needs_invoice = []
    for o in orders:
        name      = o["name"]
        created   = o["createdAt"][:19].replace("T", " ")
        country   = (o.get("shippingAddress") or {}).get("countryCodeV2", "?")
        total_obj = (o.get("totalPriceSet") or {}).get("shopMoney") or {}
        total     = f"{total_obj.get('amount', '?')} {total_obj.get('currencyCode', '')}"
        ff_status = o.get("displayFulfillmentStatus", "?")

        if country in UK:
            note = "skip (UK)"
        elif country in EU:
            note = "👀 EU — invoice needed"
            needs_invoice.append(name)
        elif country in US:
            note = "👀 US — invoice + customs needed"
            needs_invoice.append(name)
        else:
            note = f"manual review ({country})"

        print(f"{name:<14} {created:<19} {country:<5} {total:<14} {ff_status:<14} {note}")

    print()
    print(f"Total: {len(orders)}, needing invoice generation: {len(needs_invoice)}")
    if needs_invoice:
        print()
        print("To backfill invoices, run:")
        for n in needs_invoice:
            clean = n.lstrip("#")
            print(f"  python generate_invoice.py {clean}")


if __name__ == "__main__":
    main()
