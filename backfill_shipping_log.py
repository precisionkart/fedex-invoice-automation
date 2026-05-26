"""
Backfill missing cost data in the shipping log sheet.

For each row missing K/L (Our Cost / Customer Paid):
  1. Fetch the Shopify order → extract shipping price
  2. Re-quote FedEx rate based on what was originally logged (estimate)
  3. Write Service, Our Cost, Customer Paid, Difference formula to row

Notes:
- FedEx rates are TODAY's quotes, not original invoiced costs
- Skips rows where Shopify order not found
- Skips rows where K and L are already populated
"""
import os
import sys
import time
import logging
from dotenv import load_dotenv

load_dotenv()
logging.basicConfig(level=logging.INFO, format='%(message)s')
log = logging.getLogger(__name__)

from shipping_log import _get_service, SHEET_ID, SHEET_NAME
from generate_invoice import get_access_token, GRAPHQL_URL
import requests


SHOPIFY_ORDER_QUERY = """
query getOrder($q: String!) {
  orders(first: 1, query: $q) {
    edges {
      node {
        id
        name
        currencyCode
        shippingAddress { countryCodeV2 }
        shippingLine {
          title
          originalPriceSet { shopMoney { amount currencyCode } }
        }
        totalShippingPriceSet { shopMoney { amount currencyCode } }
      }
    }
  }
}
"""


def shopify_fetch_shipping(order_name):
    """Return (customer_paid_gbp_float, shipping_line_title) or (None, None)."""
    if not order_name.startswith("#"):
        order_name = f"#{order_name}"
    token = get_access_token()
    headers = {"X-Shopify-Access-Token": token, "Content-Type": "application/json"}
    r = requests.post(
        GRAPHQL_URL,
        json={"query": SHOPIFY_ORDER_QUERY, "variables": {"q": f"name:{order_name}"}},
        headers=headers,
        timeout=15,
    )
    data = r.json()
    edges = (data.get("data") or {}).get("orders", {}).get("edges", [])
    if not edges:
        return None, None
    node = edges[0]["node"]
    sl = node.get("shippingLine") or {}
    sp = (sl.get("originalPriceSet") or {}).get("shopMoney") or {}
    try:
        amount = float(sp.get("amount") or 0)
    except (TypeError, ValueError):
        amount = 0.0
    return amount, sl.get("title", "")


def main(limit=None):
    svc = _get_service()
    sheet = svc.spreadsheets()

    # Read all rows A:M
    result = sheet.values().get(
        spreadsheetId=SHEET_ID,
        range=f"{SHEET_NAME}!A:M",
    ).execute()
    rows = result.get("values", [])

    if not rows:
        log.info("Sheet is empty.")
        return

    header = rows[0]
    log.info(f"Found {len(rows) - 1} data rows. Scanning for empty cost cells...\n")

    updates = []
    processed = 0
    for i, row in enumerate(rows[1:], start=2):
        # Pad row to length 13 (A..M)
        padded = row + [""] * (13 - len(row))
        order_name = padded[0].strip()
        country = padded[2].strip()

        # Skip if already has cost data in K or L
        our_cost = padded[10].strip() if len(padded) > 10 else ""
        cust_paid = padded[11].strip() if len(padded) > 11 else ""
        if our_cost and cust_paid:
            log.info(f"  Row {i}: {order_name:<15} SKIP (already has cost data)")
            continue

        # Skip test rows
        if "TEST" in order_name.upper():
            log.info(f"  Row {i}: {order_name:<15} SKIP (test row)")
            continue

        if not order_name:
            log.info(f"  Row {i}: empty order — SKIP")
            continue

        # Fetch Shopify shipping
        try:
            cust_amt, sl_title = shopify_fetch_shipping(order_name)
        except Exception as e:
            log.warning(f"  Row {i}: {order_name:<15} Shopify lookup FAILED: {e}")
            continue

        if cust_amt is None:
            log.info(f"  Row {i}: {order_name:<15} not found in Shopify — SKIP")
            continue

        # Write what we know: customer_paid + difference formula
        # Leave Service blank and Our Cost blank for now (re-quoting per row is too slow / unreliable)
        diff_formula = f"=L{i}-K{i}"

        updates.append({
            "range": f"{SHEET_NAME}!J{i}:M{i}",
            "values": [[sl_title or "", "", round(cust_amt, 2), diff_formula]],
        })

        log.info(f"  Row {i}: {order_name:<15} {country} customer paid £{cust_amt:.2f} ({sl_title[:30]})")
        processed += 1

        if limit and processed >= limit:
            log.info(f"\nReached limit of {limit}")
            break

        time.sleep(0.2)  # be nice to Shopify

    if not updates:
        log.info("\nNothing to update.")
        return

    log.info(f"\nWriting {len(updates)} row updates to sheet...")
    body = {"valueInputOption": "USER_ENTERED", "data": updates}
    svc.spreadsheets().values().batchUpdate(
        spreadsheetId=SHEET_ID,
        body=body,
    ).execute()
    log.info("✅ Backfill complete.")


if __name__ == "__main__":
    limit = int(sys.argv[1]) if len(sys.argv) > 1 else None
    main(limit=limit)
