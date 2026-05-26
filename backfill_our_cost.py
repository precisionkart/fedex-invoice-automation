"""
Backfill 'Our Cost £' (column K) and 'Service' (column J)
using today's FedEx rate quotes for each historic shipment.

Caveat: Today's rates may differ from what we were actually
charged at ship time. Replace with actual invoiced cost when
FedEx invoice arrives.
"""
import sys
import time
import logging
from dotenv import load_dotenv

load_dotenv()
logging.basicConfig(level=logging.INFO, format='%(message)s')
log = logging.getLogger(__name__)

from shipping_log import _get_service, SHEET_ID, SHEET_NAME
from ship_real_order import dry_quote_only


def main(limit=None):
    svc = _get_service()
    sheet = svc.spreadsheets()

    result = sheet.values().get(
        spreadsheetId=SHEET_ID,
        range=f"{SHEET_NAME}!A:M",
    ).execute()
    rows = result.get("values", [])
    if not rows:
        log.info("Sheet empty.")
        return

    log.info(f"Found {len(rows)-1} data rows. Scanning for empty K cells...\n")

    updates = []
    processed = 0
    for i, row in enumerate(rows[1:], start=2):
        padded = row + [""] * (13 - len(row))
        order_name = padded[0].strip()
        our_cost = padded[10].strip() if len(padded) > 10 else ""

        if our_cost:
            continue
        if not order_name or "TEST" in order_name.upper():
            continue

        log.info(f"  Row {i}: {order_name} — quoting...")
        cost, service_name = dry_quote_only(order_name)
        if cost is None:
            log.info(f"     SKIP ({service_name})")
            continue

        # Write to J (service) and K (cost)
        updates.append({
            "range": f"{SHEET_NAME}!J{i}:K{i}",
            "values": [[service_name, cost]],
        })
        log.info(f"     £{cost} via {service_name}")
        processed += 1

        if limit and processed >= limit:
            break

        time.sleep(0.3)

    if not updates:
        log.info("\nNothing to update.")
        return

    log.info(f"\nWriting {len(updates)} updates...")
    svc.spreadsheets().values().batchUpdate(
        spreadsheetId=SHEET_ID,
        body={"valueInputOption": "USER_ENTERED", "data": updates},
    ).execute()
    log.info("✅ Done.")


if __name__ == "__main__":
    limit = int(sys.argv[1]) if len(sys.argv) > 1 else None
    main(limit=limit)
