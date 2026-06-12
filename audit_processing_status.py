"""
One-off audit: find paid Shopify orders that did NOT fully process through
the FedEx shipping pipeline.

A non-GB EU/US/UK order is "fully processed" when ALL of these are true:
  1. It has a row in the FedEx Shipping Log sheet
  2. That row has a FedEx tracking number
  3. An invoice PDF exists in Google Drive (named Invoice_{order}.pdf)
  4. Shopify tag 'fedex-shipped' is applied (and not stuck on 'fedex-processing')
  5. It is fulfilled in Shopify

This is READ-ONLY: it posts no notes, re-ships nothing, regenerates nothing.
It just reports. Run manually:

    python audit_processing_status.py --limit 50
"""

import re
import argparse
import logging

import requests
from dotenv import load_dotenv

from generate_invoice import get_access_token, GRAPHQL_URL
from country_router import classify_destination
import shipping_log
import drive_upload

load_dotenv()

logging.basicConfig(level=logging.INFO, format="%(message)s")
log = logging.getLogger("audit_processing")

# We classify destinations with the SAME router the live pipeline uses
# (country_router.classify_destination), so "would this order have shipped?"
# matches reality exactly: action 'skip' = GB, 'manual_review' = CA/NO/IE/etc,
# 'ship' = EU/US/EFTA that we actually auto-ship (and therefore must process).

PROCESSING_TAG = "fedex-processing"
SHIPPED_TAG = "fedex-shipped"

ORDERS_QUERY = """
query recentOrders($first: Int!) {
  orders(first: $first, query: "financial_status:paid", reverse: true, sortKey: CREATED_AT) {
    edges {
      node {
        name
        tags
        displayFulfillmentStatus
        shippingAddress { countryCodeV2 }
      }
    }
  }
}
"""


def _normalize_order_name(name):
    return str(name or "").lstrip("#").strip().upper()


def _invoice_filename(order_name):
    """Match generate_invoice.py's naming: Invoice_{safe_name}.pdf."""
    safe = re.sub(r"[^A-Za-z0-9_-]", "_", str(order_name).lstrip("#"))
    return f"Invoice_{safe}.pdf"


def load_shipping_log():
    """Index the FedEx Shipping Log by normalized order name.
    Returns {name: {tracking, country}}. Same Sheet as audit_ddu_status.py."""
    if not shipping_log.SHEET_ID:
        raise RuntimeError("Missing SHIPPING_LOG_SHEET_ID in .env")
    service = shipping_log._get_service()
    result = service.spreadsheets().values().get(
        spreadsheetId=shipping_log.SHEET_ID,
        range=f"{shipping_log.SHEET_NAME}!A:C",
    ).execute()
    rows = result.get("values", [])
    index = {}
    for row in rows[1:]:  # skip header
        order = row[0] if len(row) > 0 else ""
        if not order:
            continue
        key = _normalize_order_name(order)
        tracking = (row[1] if len(row) > 1 else "").strip()
        # Keep first row that actually has a tracking number, else any row.
        if key not in index or (tracking and not index[key]["tracking"]):
            index[key] = {
                "tracking": tracking,
                "country": (row[2] if len(row) > 2 else "").strip(),
            }
    return index


def invoice_exists_on_drive(order_name, _cache={}):
    """True if Invoice_{order}.pdf exists in Drive (any year/month subfolder).
    Uses the same Drive helper/token as ship_real_order.py."""
    filename = _invoice_filename(order_name)
    if filename in _cache:
        return _cache[filename]
    service = drive_upload._get_service()
    # Escape single quotes for the Drive query.
    safe_q = filename.replace("'", "\\'")
    q = f"name = '{safe_q}' and trashed = false"
    results = service.files().list(q=q, fields="files(id,name)", pageSize=1).execute()
    found = bool(results.get("files"))
    _cache[filename] = found
    return found


def fetch_recent_orders(limit):
    token = get_access_token()
    r = requests.post(
        GRAPHQL_URL,
        headers={"X-Shopify-Access-Token": token, "Content-Type": "application/json"},
        json={"query": ORDERS_QUERY, "variables": {"first": limit}},
        timeout=30,
    )
    r.raise_for_status()
    data = r.json()
    if "errors" in data:
        raise RuntimeError(f"Shopify GraphQL errors: {data['errors']}")
    return [e["node"] for e in data["data"]["orders"]["edges"]]


def main():
    parser = argparse.ArgumentParser(description="Audit which paid orders didn't fully process.")
    parser.add_argument("--limit", type=int, default=50,
                        help="Number of recent paid orders to audit (default 50).")
    args = parser.parse_args()

    log.info("Loading shipping log...")
    ship_index = load_shipping_log()
    log.info("  %d shipments indexed.", len(ship_index))

    orders = fetch_recent_orders(args.limit)

    counts = {"ok": 0, "missing": 0, "skip_gb": 0, "skip_manual": 0, "error": 0}
    rows = []          # (order, country, status_string)
    need_invoice = []  # order names missing an invoice

    for o in orders:
        name = o.get("name", "?")
        try:
            country = (o.get("shippingAddress") or {}).get("countryCodeV2") or "?"
            tags = o.get("tags") or []
            ff_status = o.get("displayFulfillmentStatus", "?")

            action = classify_destination(country)["action"]

            if action == "skip":  # GB / UK domestic
                counts["skip_gb"] += 1
                rows.append((name, country, "skip (GB)"))
                continue

            if action == "manual_review":  # CA, NO, IE, anything off-pipeline
                counts["skip_manual"] += 1
                rows.append((name, country, "skip (manual review)"))
                continue

            # action == "ship" — an order the pipeline should have processed.
            flags = []
            ship = ship_index.get(_normalize_order_name(name))
            if not ship:
                flags.append("❌ no shipping log row")
            elif not ship.get("tracking"):
                flags.append("❌ shipping log row but no tracking")

            if not invoice_exists_on_drive(name):
                flags.append("❌ no invoice on Drive")

            if PROCESSING_TAG in tags and SHIPPED_TAG not in tags:
                flags.append("❌ stuck in fedex-processing tag")

            if ff_status not in ("FULFILLED", "PARTIALLY_FULFILLED"):
                flags.append("❌ unfulfilled in Shopify")

            if flags:
                counts["missing"] += 1
                rows.append((name, country, "  ".join(flags)))
                if "❌ no invoice on Drive" in flags:
                    need_invoice.append(name)
            else:
                counts["ok"] += 1
                rows.append((name, country, "✅ OK"))

        except Exception as e:  # noqa: BLE001 — isolate per-order failures
            counts["error"] += 1
            rows.append((name, "?", f"⚠️ error: {e}"))
            log.warning("  %s: error during audit — %s", name, e)
            continue

    # ---- Table ----
    log.info("\n" + "=" * 90)
    log.info("%-14s %-5s %s", "Order", "Ctry", "Status")
    log.info("-" * 90)
    for name, country, status in rows:
        log.info("%-14s %-5s %s", name, country, status)
    log.info("=" * 90)

    total_skipped = counts["skip_gb"] + counts["skip_manual"]
    log.info(
        "Processed %d orders: %d OK, %d missing-something, %d skipped "
        "(%d GB, %d manual review)%s",
        len(orders), counts["ok"], counts["missing"], total_skipped,
        counts["skip_gb"], counts["skip_manual"],
        f", {counts['error']} errors" if counts["error"] else "",
    )

    if need_invoice:
        log.info("\nTo backfill missing invoices, run:")
        for n in need_invoice:
            log.info("  python generate_invoice.py %s", n.lstrip("#"))


if __name__ == "__main__":
    main()
