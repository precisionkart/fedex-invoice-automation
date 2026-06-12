"""
One-off audit: mark each recent international order as DDU or DDP in its
Shopify order timeline (note field).

Background
----------
Commit 58cbdda switched FedEx duties payment from SENDER (we paid — DDP) to
RECIPIENT (customer pays on delivery — DDU). Shipments created BEFORE that
code went live on Railway were sent DDP; shipments AT/AFTER it are DDU.

For each of the last N paid Shopify orders:
  - GB (domestic) -> skip
  - No shipment in the FedEx shipping log -> skip
  - Shipped before DDU_LIVE_FROM -> DDP (we paid duties, recoverable)
  - Shipped at/after DDU_LIVE_FROM -> DDU (customer pays)

Read-only on Shopify orders; the ONLY thing written is a timeline note.
Idempotent (won't double-post), per-order error isolation, dry-run aware.

This is a one-off audit tool. It is intentionally NOT wired into app.py or
cron — run it manually:

    python audit_ddu_status.py --limit 3 --dry-run   # preview
    python audit_ddu_status.py --limit 90            # real run
"""

import os
import sys
import argparse
import logging
from datetime import datetime, timezone

import requests
from dotenv import load_dotenv

from generate_invoice import get_access_token, GRAPHQL_URL
import shopify_note
import shipping_log

load_dotenv()

logging.basicConfig(level=logging.INFO, format="%(message)s")
log = logging.getLogger("audit_ddu")

# ---------------------------------------------------------------------------
# IMPORTANT: replace this with the actual Railway-confirmed UTC timestamp at
# which commit 58cbdda (SENDER -> RECIPIENT / DDP -> DDU) finished deploying.
# Shipments strictly before this moment were DDP; at/after are DDU.
DDU_LIVE_FROM = "2026-06-10T22:00:00Z"
# ---------------------------------------------------------------------------

DDP_NOTE = (
    "⚠️ Shipped DDP — Precision paid duties & taxes (pre-fix shipment). "
    "Tracking: {tracking}. This is recoverable — claim ancillary clearance "
    "fees back from FedEx if charged."
)
DDU_NOTE = (
    "✅ Shipped DDU — Customer pays duties & taxes on delivery. "
    "Tracking: {tracking}."
)

# Substrings used for idempotency — if either already appears on the order
# note we treat the order as already audited and skip it.
ALREADY_NOTED_MARKERS = ("Shipped DDU", "Shipped DDP")

ORDERS_QUERY = """
query recentOrders($first: Int!) {
  orders(first: $first, query: "financial_status:paid", reverse: true, sortKey: CREATED_AT) {
    edges {
      node {
        name
        note
        shippingAddress { countryCodeV2 }
      }
    }
  }
}
"""


def _parse_live_from():
    """Parse DDU_LIVE_FROM (ISO 8601, Z suffix) to an aware UTC datetime."""
    iso = DDU_LIVE_FROM.replace("Z", "+00:00")
    dt = datetime.fromisoformat(iso)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _parse_shipped_at(raw):
    """
    Parse a shipping-log timestamp into an aware UTC datetime.

    The log writes col D (Created at) as e.g. "2026-06-10 21:34:05 UTC"
    (see shipping_log.log_shipment). Be lenient about formats / trailing
    'UTC' and fall back to ISO parsing.
    """
    if not raw:
        return None
    s = str(raw).strip()
    s = s.replace(" UTC", "").replace("Z", "").strip()
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%dT%H:%M:%S"):
        try:
            return datetime.strptime(s, fmt).replace(tzinfo=timezone.utc)
        except ValueError:
            continue
    try:
        dt = datetime.fromisoformat(s)
        return dt.replace(tzinfo=timezone.utc) if dt.tzinfo is None else dt.astimezone(timezone.utc)
    except ValueError:
        return None


def _normalize_order_name(name):
    """Normalize for cross-referencing: strip '#', whitespace, uppercase."""
    return str(name or "").lstrip("#").strip().upper()


def load_shipping_log():
    """
    Read the whole FedEx Shipping Log sheet once and index by normalized
    order name. Returns {normalized_name: {tracking, country, shipped_at}}.
    Reads directly from the same Sheet shipping_log.py writes to.
    """
    if not shipping_log.SHEET_ID:
        raise RuntimeError("Missing SHIPPING_LOG_SHEET_ID in .env")

    service = shipping_log._get_service()
    result = service.spreadsheets().values().get(
        spreadsheetId=shipping_log.SHEET_ID,
        range=f"{shipping_log.SHEET_NAME}!A:D",
    ).execute()
    rows = result.get("values", [])

    index = {}
    for row in rows[1:]:  # skip header
        order = row[0] if len(row) > 0 else ""
        if not order:
            continue
        key = _normalize_order_name(order)
        # If duplicate rows exist for one order, keep the earliest shipment
        # (that determines DDU vs DDP — first time we actually shipped it).
        shipped_at = _parse_shipped_at(row[3] if len(row) > 3 else "")
        entry = {
            "tracking":   row[1] if len(row) > 1 else "",
            "country":    row[2] if len(row) > 2 else "",
            "shipped_at": shipped_at,
        }
        existing = index.get(key)
        if existing is None:
            index[key] = entry
        elif shipped_at and existing["shipped_at"] and shipped_at < existing["shipped_at"]:
            index[key] = entry
        elif existing["shipped_at"] is None and shipped_at:
            index[key] = entry
    return index


def fetch_recent_orders(limit):
    """Pull the last `limit` paid orders from Shopify (read-only)."""
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


def already_noted(order_node):
    """True if this order's note already contains a DDU/DDP audit marker."""
    note = order_node.get("note") or ""
    return any(marker in note for marker in ALREADY_NOTED_MARKERS)


def main():
    parser = argparse.ArgumentParser(description="Audit DDU/DDP status of recent international orders.")
    parser.add_argument("--limit", type=int, default=90,
                        help="Number of recent paid orders to audit (default 90).")
    parser.add_argument("--dry-run", action="store_true",
                        help="Log what WOULD be posted but don't post. Overrides FEDEX_DRY_RUN for this run.")
    args = parser.parse_args()

    env_dry = os.getenv("FEDEX_DRY_RUN", "false").lower() == "true"
    dry_run = args.dry_run or env_dry

    live_from = _parse_live_from()

    log.info("=" * 78)
    log.info("DDU/DDP audit")
    log.info("  DDU_LIVE_FROM : %s", live_from.isoformat())
    log.info("  limit         : %s", args.limit)
    log.info("  dry run       : %s%s", dry_run,
             "  (FEDEX_DRY_RUN)" if env_dry and not args.dry_run else "")
    log.info("=" * 78)

    ship_index = load_shipping_log()
    log.info("Loaded %d shipments from the shipping log.\n", len(ship_index))

    orders = fetch_recent_orders(args.limit)

    counts = {"ddp": 0, "ddu": 0, "skip_gb": 0, "skip_no_ship": 0, "skip_noted": 0, "error": 0}
    rows = []  # (order, country, classification, tracking)

    for o in orders:
        name = o.get("name", "?")
        try:
            country = (o.get("shippingAddress") or {}).get("countryCodeV2") or "?"

            if country == "GB":
                counts["skip_gb"] += 1
                rows.append((name, country, "skip (GB)", ""))
                continue

            ship = ship_index.get(_normalize_order_name(name))
            if not ship:
                counts["skip_no_ship"] += 1
                rows.append((name, country, "skip (no shipment)", ""))
                continue

            if already_noted(o):
                counts["skip_noted"] += 1
                rows.append((name, country, "skip (already noted)", ship.get("tracking", "")))
                continue

            shipped_at = ship.get("shipped_at")
            tracking = ship.get("tracking") or "(unknown)"

            if shipped_at is None:
                # Shipment row exists but timestamp unparseable — don't guess.
                counts["skip_no_ship"] += 1
                rows.append((name, country, "skip (no ship date)", tracking))
                log.warning("  %s: shipment row found but shipped_at unparseable — skipping.", name)
                continue

            if shipped_at < live_from:
                classification = "DDP"
                note_text = DDP_NOTE.format(tracking=tracking)
                counts["ddp"] += 1
            else:
                classification = "DDU"
                note_text = DDU_NOTE.format(tracking=tracking)
                counts["ddu"] += 1

            rows.append((name, country, classification, tracking))

            if dry_run:
                log.info("  [DRY] %s (%s) -> %s  | would post: %s", name, country, classification, note_text)
            else:
                shopify_note.add_order_note(name, note_text)
                log.info("  POSTED %s (%s) -> %s", name, country, classification)

        except Exception as e:  # noqa: BLE001 — isolate per-order failures
            counts["error"] += 1
            rows.append((name, "?", "ERROR", ""))
            log.warning("  %s: error during audit — %s", name, e)
            continue

    # ---- Summary table ----
    log.info("\n" + "=" * 78)
    log.info("%-14s %-5s %-22s %s", "Order", "Ctry", "Result", "Tracking")
    log.info("-" * 78)
    for name, country, result, tracking in rows:
        log.info("%-14s %-5s %-22s %s", name, country, result, tracking)
    log.info("=" * 78)

    total_skipped = counts["skip_gb"] + counts["skip_no_ship"] + counts["skip_noted"]
    log.info(
        "Processed %d orders: %d DDP, %d DDU, %d skipped "
        "(%d GB, %d no shipment, %d already noted), %d errors.",
        len(orders), counts["ddp"], counts["ddu"], total_skipped,
        counts["skip_gb"], counts["skip_no_ship"], counts["skip_noted"], counts["error"],
    )
    if dry_run:
        log.info("DRY RUN — nothing was posted to Shopify.")


if __name__ == "__main__":
    main()
