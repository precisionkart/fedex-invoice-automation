"""
Dry-run version of ship_real_order — runs the full pipeline up to,
but NOT including, the FedEx Ship API call. Shows what would happen
without spending money.

Usage: python ship_dry_run.py <order_name>
"""

import os, sys, logging
from dotenv import load_dotenv

from generate_invoice import fetch_order, get_access_token
from box_chooser import choose_package
from fedex_rates import get_rates
from ship_real_order import extract_items, build_fedex_recipient, SHIPPER

load_dotenv()
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("dry_run")


def main():
    if len(sys.argv) != 2:
        print("Usage: python ship_dry_run.py <order_name>")
        sys.exit(1)
    order_name = sys.argv[1].lstrip("#")

    log.info(f"🔍 DRY RUN for order {order_name}")
    log.info("   No FedEx shipment will be created. No charges incurred.")
    print()

    log.info("1. Fetching order...")
    token = get_access_token()
    order = fetch_order(token, order_name)
    country = (order.get("shippingAddress") or {}).get("countryCodeV2") or "?"
    addr = order.get("shippingAddress") or {}
    log.info(f"   Country: {country}")
    log.info(f"   Recipient: {addr.get('name')}")
    log.info(f"   Address: {addr.get('address1')}, {addr.get('city')}, {addr.get('zip')}")
    log.info(f"   Phone: {addr.get('phone') or '⚠️  MISSING'}")

    log.info("2. Reading items + dimensions...")
    items = extract_items(order)
    log.info(f"   {len(items)} item(s):")
    for it in items:
        log.info(
            f"     • {it['title'][:50]:50s} qty={it['quantity']} "
            f"{it['length_cm']}×{it['width_cm']}×{it['height_cm']}cm "
            f"{it['weight_kg']}kg  HS={it['hs_code'] or '⚠️ MISSING'} "
            f"COO={it['country_of_origin']} value={it['unit_value']}"
        )

    log.info("3. Choosing package...")
    pkg = choose_package(items)
    if pkg.get("manual_review"):
        log.warning(f"   ⚠️  Manual review: {pkg.get('reason')}")
        return
    log.info(f"   Selected: {pkg['package_name']} "
             f"({pkg['length_cm']}×{pkg['width_cm']}×{pkg['height_cm']}cm, {pkg['weight_kg']}kg)")

    log.info("4. Fetching real production FedEx rates...")
    declared = sum(it["unit_value"] * it["quantity"] for it in items)
    shipment = {
        "shipper":   SHIPPER,
        "recipient": build_fedex_recipient(order),
        "package": {
            "weight_kg": pkg["weight_kg"], "length_cm": pkg["length_cm"],
            "width_cm":  pkg["width_cm"],  "height_cm": pkg["height_cm"],
            "currency":  "GBP", "declared_value": declared,
        },
        "line_items": items,
    }
    rates = get_rates(shipment)
    if not rates:
        log.error("   ❌ No rates returned")
        return

    log.info(f"   Got {len(rates)} service(s):")
    for r in rates:
        marker = "  🏆 CHEAPEST" if r == rates[0] else ""
        log.info(f"     {r['service_name']:50s} {r['currency']} {r['price']:.2f}{marker}")

    print()
    print("=" * 60)
    print("DRY RUN COMPLETE — no shipment created, no charges")
    print("=" * 60)
    print(f"  Would ship in:   {pkg['package_name']}")
    print(f"  Cheapest:        {rates[0]['service_name']}")
    print(f"  Cost:            {rates[0]['price']} {rates[0]['currency']}")
    print(f"  Declared value:  £{declared:.2f}")
    print()
    print("If everything above looks correct, run:")
    print(f"  python ship_real_order.py {order_name}")
    print()
    print("That will create a REAL FedEx label and charge your account.")


if __name__ == "__main__":
    main()
