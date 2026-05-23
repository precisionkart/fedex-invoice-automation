"""
End-to-end real order shipper.

Given a Shopify order name (e.g. '04072-SHP'):
  1. Fetch from Shopify
  2. Read product dimensions (metafields) + variant weight
  3. Pick package via box_chooser
  4. Build shipment dict (FedEx-format)
  5. Get rates → pick cheapest
  6. Create shipment → label + tracking
  7. Save label + invoice to Drive
  8. Upload invoice to FedEx via Trade Documents
  9. Log to Sheet

Usage:
    python ship_real_order.py 04072-SHP
"""

import os
import sys
import logging
import base64
from dotenv import load_dotenv

from generate_invoice import (
    fetch_order, get_access_token,
    build_invoice_from_order, render_pdf,
    normalise_country_code, kg,
)
from drive_upload import upload_invoice
from box_chooser import choose_package
from fedex_rates import get_rates
from fedex_ship import create_shipment
from fedex_upload_docs import upload_trade_document
from shipping_log import log_shipment

load_dotenv()

logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("ship_real_order")


# Your shipper address (Precision Kart)
SHIPPER = {
    "contact": {
        "personName":  "Precision Kart",
        "phoneNumber": "447914567321",
        "companyName": "Precision Kart",
        "emailAddress": "info@precisionkart.co.uk",
    },
    "address": {
        "streetLines":   ["Unit 2 Pacemanor", "Bellbrook Industrial Estate"],
        "city":          "Uckfield",
        "stateOrProvinceCode": "GB",
        "postalCode":    "TN221YA",
        "countryCode":   "GB",
    },
}


def metafields_to_dict(metafields_block):
    """Flatten Shopify GraphQL metafields edges/node into a {key: value} dict."""
    out = {}
    if not metafields_block:
        return out
    for edge in metafields_block.get("edges", []):
        node = edge.get("node", {})
        out[node.get("key")] = node.get("value")
    return out


def extract_items(order):
    """
    Build line items for both box_chooser and FedEx customs commodities.
    Pulls dimensions from product-level metafields, weight from variant.
    """
    items = []
    for edge in order.get("lineItems", {}).get("edges", []):
        li = edge["node"]
        variant   = li.get("variant") or {}
        product   = variant.get("product") or {}
        v_meta    = metafields_to_dict(variant.get("metafields"))
        p_meta    = metafields_to_dict(product.get("metafields"))

        # Dimensions from product metafields
        try:
            L = float(p_meta.get("length_cm"))
            W = float(p_meta.get("width_cm"))
            H = float(p_meta.get("height_cm"))
        except (TypeError, ValueError):
            raise RuntimeError(
                f"'{li.get('title')}' missing product-level dimensions "
                f"(custom.length_cm/width_cm/height_cm). Found: {p_meta}"
            )

        # Weight from variant.inventoryItem.measurement.weight
        weight_info = (variant.get("inventoryItem") or {}).get("measurement", {}).get("weight") or {}
        weight_kg   = kg(weight_info.get("value"), weight_info.get("unit"))
        if not weight_kg:
            raise RuntimeError(f"'{li.get('title')}' has no weight set in Shopify.")

        # Customs data from variant metafields (your existing pattern)
        desc  = v_meta.get("fedex_product_title") or li.get("title")
        hs    = (v_meta.get("fedex_hs_code") or "").replace(".", "")
        coo_raw = v_meta.get("fedex_country_of_origin") or "GB"
        coo_code, _status = normalise_country_code(coo_raw)
        coo = coo_code
        value = float(v_meta.get("fedex_shipping_cost") or 0)

        items.append({
            "length_cm":         L,
            "width_cm":          W,
            "height_cm":         H,
            "weight_kg":         weight_kg,
            "quantity":          li.get("quantity", 1),
            "title":             li.get("title", ""),
            # For FedEx customs
            "description":       desc,
            "hs_code":           hs,
            "country_of_origin": coo,
            "unit_value":        value,
            "currency":          "GBP",
        })
    return items


def build_fedex_recipient(order):
    """Translate Shopify shipping address → FedEx recipient block."""
    addr = order.get("shippingAddress") or {}
    country = addr.get("countryCodeV2") or addr.get("country") or ""
    
    # Province code: only required for US/CA; for others, omit if missing or invalid (>2 chars)
    province = addr.get("provinceCode") or ""
    if country in ("US", "CA") and len(province) != 2:
        province = ""  # let FedEx complain rather than ship with bad value
    elif len(province) > 2:
        province = ""  # FedEx max is 2 chars; for non-US/CA, just omit
    
    address_block = {
        "streetLines":  [s for s in [addr.get("address1"), addr.get("address2")] if s],
        "city":         addr.get("city") or "",
        "postalCode":   addr.get("zip") or "",
        "countryCode":  country,
    }
    if province:
        address_block["stateOrProvinceCode"] = province
    
    # Phone: FedEx accepts blank for many EU destinations, but safer to fallback to Precision Kart's
    phone = (addr.get("phone") or "").strip()
    if not phone:
        phone = "+447000000000"  # Precision Kart fallback so FedEx can call us on issues
    
    return {
        "contact": {
            "personName":  addr.get("name") or "Customer",
            "phoneNumber": phone[:15],
        },
        "address": address_block,
    }


def ship_order(order_name):
    """End-to-end pipeline for one order."""
    log.info(f"🚀 Shipping order {order_name}")

    # 1. Fetch from Shopify
    log.info("   1/9 Fetching order...")
    token = get_access_token()
    order = fetch_order(token, order_name)
    country = (order.get("shippingAddress") or {}).get("countryCodeV2") or "?"
    log.info(f"      Country: {country}")

    # 2. Extract items + dimensions
    log.info("   2/9 Reading items + dimensions...")
    items = extract_items(order)
    log.info(f"      {len(items)} item(s)")

    # 3. Pick package
    log.info("   3/9 Choosing package...")
    pkg = choose_package(items)
    if pkg.get("manual_review"):
        log.warning(f"      ⚠️  Manual review: {pkg.get('reason')}")
        return {"error": "manual_review", "details": pkg}
    log.info(f"      Selected: {pkg['package_name']} "
             f"({pkg['length_cm']}×{pkg['width_cm']}×{pkg['height_cm']}cm, "
             f"{pkg['weight_kg']}kg)")

    # 4. Build shipment dict for FedEx
    declared_value = sum(it["unit_value"] * it["quantity"] for it in items)
    shipment = {
        "shipper":   SHIPPER,
        "recipient": build_fedex_recipient(order),
        "package": {
            "weight_kg":      pkg["weight_kg"],
            "length_cm":      pkg["length_cm"],
            "width_cm":       pkg["width_cm"],
            "height_cm":      pkg["height_cm"],
            "currency":       "GBP",
            "declared_value": declared_value,
        },
        "line_items": items,
    }

    # 5. Get rates → pick cheapest
    log.info("   4/9 Fetching FedEx rates...")
    rates = get_rates(shipment)
    if not rates:
        return {"error": "No rates returned"}
    cheapest = rates[0]   # already sorted by price ascending
    log.info(f"      Cheapest: {cheapest['service_name']} "
             f"@ {cheapest['price']} {cheapest['currency']}")
    shipment["service_type"] = cheapest["service_type"]

    # 6. Create shipment (skip if dry-run)
    dry_run = os.getenv("FEDEX_DRY_RUN", "false").lower() == "true"
    if dry_run:
        log.info("   5/9 [DRY RUN] Skipping label creation")
        log.info(f"      Would ship: {pkg['package_name']} via {cheapest['service_name']}")
        log.info(f"      Would cost: {cheapest['price']} {cheapest['currency']}")
        return {
            "order":    order_name,
            "tracking": "DRY-RUN-NO-LABEL",
            "package":  pkg["package_name"],
            "service":  cheapest["service_name"],
            "cost":     f"{cheapest['price']} {cheapest['currency']}",
            "label":    "(dry-run, no label created)",
            "invoice":  "(dry-run, not generated)",
            "dry_run":  True,
        }
    log.info("   5/9 Creating FedEx shipment...")
    ship_result = create_shipment(shipment)
    tracking  = ship_result["tracking_number"]
    label_pdf = ship_result["label_pdf"]   # raw bytes, already decoded
    log.info(f"      Tracking: {tracking}")

    # 7. Save label PDF to Drive
    log.info("   6/9 Saving label to Drive...")
    label_path = f"{order_name}_label.pdf"
    with open(label_path, "wb") as f:
        f.write(label_pdf)
    label_drive = upload_invoice(label_path, os.getenv("DRIVE_FOLDER_LABELS") or os.getenv("GOOGLE_DRIVE_FOLDER_ID"))
    log.info(f"      Label: {label_drive.get('link', 'uploaded')}")

    # 8. Generate + save invoice
    log.info("   7/9 Generating invoice...")
    invoice_path = f"Invoice_{order_name}.pdf"
    invoice, _warnings = build_invoice_from_order(order)
    render_pdf(invoice, invoice_path)
    invoice_drive = upload_invoice(invoice_path, os.getenv("DRIVE_FOLDER_INVOICES") or os.getenv("GOOGLE_DRIVE_FOLDER_ID"))
    log.info(f"      Invoice: {invoice_drive.get('link', 'uploaded')}")

    # 9. Upload invoice as ETD
    log.info("   8/9 Uploading invoice via ETD...")
    etd_result = upload_trade_document(
        tracking_number=tracking,
        pdf_path=invoice_path,
        origin_country="GB",
        destination_country=country,
    )
    doc_id = etd_result.get('output', {}).get('meta', {}).get('docId', 'n/a')
    log.info(f"      ETD docId: {doc_id}")

    # 10. Log to Sheet
    log.info("   9/9 Logging to Sheet...")
    log_shipment(
        order_name=order_name,
        tracking_number=tracking,
        country=country,
        label_drive_link=label_drive.get("link", ""),
        invoice_drive_link=invoice_drive.get("link", ""),
    )
    log.info(f"      ✅ Logged")

    log.info(f"🎉 Done! {order_name} → {tracking}")
    return {
        "order":    order_name,
        "tracking": tracking,
        "package":  pkg["package_name"],
        "service":  cheapest["service_name"],
        "cost":     f"{cheapest['price']} {cheapest['currency']}",
        "label":    label_drive.get("link"),
        "invoice":  invoice_drive.get("link"),
    }


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print("Usage: python ship_real_order.py <order_name>")
        sys.exit(1)

    order_name = sys.argv[1].lstrip("#")
    try:
        result = ship_order(order_name)
    except Exception as e:
        print(f"❌ {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)

    print()
    print("=" * 60)
    print("Summary")
    print("=" * 60)
    for k, v in result.items():
        print(f"  {k:10s} {v}")
