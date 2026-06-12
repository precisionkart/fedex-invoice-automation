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
import requests
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


def _post_order_note(order_name, note_text):
    """Post an arbitrary note (info or failure) to the Shopify order timeline.

    Best-effort: never raises (logs a warning instead) and is skipped during
    dry-run. Posts note_text verbatim so callers control the exact wording.
    """
    if os.getenv("FEDEX_DRY_RUN", "false").lower() == "true":
        return
    try:
        from shopify_note import add_order_note
        add_order_note(order_name, note_text)
        log.info("      Shopify note added")
    except Exception as note_err:
        log.warning(f"      Failed to add Shopify note: {note_err}")


def _post_failure_note(order_name, reason):
    """Post a manual-review/failure note to the Shopify order timeline.

    Best-effort: never raises and is skipped during dry-run. For the existing
    manual-review exits (package picker, no rates, cost guard, country routing).
    """
    _post_order_note(
        order_name,
        f"❌ Auto-ship failed — manual review needed. Reason: {reason}",
    )


def _plain_english_fedex_failure(status_code, codes, messages, tx_id):
    """Map a FedEx Ship API error to a plain-English ops-facing note.

    Best-effort detection from the error codes/messages; falls back to a
    generic message. Always starts with "⚠️ Manual review needed" so the
    notes are visually distinct and grep-able.
    """
    codes_upper = (codes or "").upper()
    blob = f"{codes_upper} {(messages or '').upper()}"

    # 5xx / unreachable — no status_code or server-side error
    if status_code is None or status_code >= 500:
        return (
            "⚠️ Manual review needed — FedEx servers unreachable. "
            "Please ship manually or retry later."
        )

    # Customs value below shipping cost
    if "TOTALCARRIAGEVALUE.EXCEEDS.CUSTOMSVALUE" in codes_upper:
        return (
            "⚠️ Manual review needed — FedEx rejected this shipment because the "
            "order value was below the shipping cost. Please ship manually."
        )

    # Product/commodity data issue: HS code, or currency error tied to a commodity
    if "HARMONIZED.CODE.INVALID" in codes_upper or (
        "CURRENCY.TYPE.INVALID" in codes_upper and "COMMODITY_INDEX" in blob
    ):
        return (
            "⚠️ Manual review needed — FedEx rejected this shipment due to a "
            "product information issue. Please ship manually."
        )

    # Generic 4xx fallback
    return (
        "⚠️ Manual review needed — FedEx rejected this shipment. "
        "Please ship manually."
    )


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


def dry_quote_only(order_name):
    """
    Quick FedEx rate lookup for an order — no label created.
    Used by backfill script to fill in 'Our Cost' for historic shipments.
    Returns (cheapest_price_gbp, service_name) or (None, "REASON").
    """
    try:
        token = get_access_token()
        order = fetch_order(token, order_name)
        if not order:
            return None, "ORDER_NOT_FOUND"

        items = extract_items(order)
        if not items:
            return None, "NO_ITEMS"

        pkg = choose_package(items)
        if pkg.get("manual_review"):
            return None, "MANUAL_REVIEW"

        shipment = {
            "shipper":   SHIPPER,
            "recipient": build_fedex_recipient(order),
            "package": {
                "weight_kg":      pkg["weight_kg"],
                "length_cm":      pkg["length_cm"],
                "width_cm":       pkg["width_cm"],
                "height_cm":      pkg["height_cm"],
                "currency":       "GBP",
                "declared_value": 50.0,
            },
        }
        rates = get_rates(shipment)
        if not rates:
            return None, "NO_RATES"

        cheapest = min(rates, key=lambda r: r["price"])
        if cheapest.get("currency") != "GBP":
            return None, "NON_GBP"

        return round(float(cheapest["price"]), 2), cheapest.get("service_name", "")
    except Exception as e:
        return None, f"ERROR: {e}"


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
        _post_failure_note(order_name, pkg["reason"])
        return {"error": "manual_review", "details": pkg}
    log.info(f"      Selected: {pkg['package_name']} "
             f"({pkg['length_cm']}×{pkg['width_cm']}×{pkg['height_cm']}cm, "
             f"{pkg['weight_kg']}kg)")

    # 4. Build shipment dict for FedEx
    # Group identical line items (same SKU + variant) and round to 2dp
    grouped = {}
    for it in items:
        key = (it.get("sku", ""), it.get("description", ""))
        if key in grouped:
            grouped[key]["quantity"] += it["quantity"]
            grouped[key]["weight_kg"] += it.get("weight_kg", 0) * it["quantity"]
        else:
            grouped[key] = dict(it)
            grouped[key]["weight_kg"] = it.get("weight_kg", 0) * it["quantity"]
    
    items = list(grouped.values())
    
    # Round unit_value to 2dp to avoid float artifacts
    for it in items:
        it["unit_value"] = round(float(it["unit_value"]), 2)
    
    declared_value = round(sum(it["unit_value"] * it["quantity"] for it in items), 2)
    shipment = {
        "shipper":   SHIPPER,
        "recipient": build_fedex_recipient(order),
        "order_name": order_name,
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
        _post_failure_note(order_name, "No rates returned")
        return {"error": "No rates returned"}
    cheapest = rates[0]   # already sorted by price ascending
    log.info(f"      Cheapest: {cheapest['service_name']} "
             f"@ {cheapest['price']} {cheapest['currency']}")
    shipment["service_type"] = cheapest["service_type"]

    # COST DISPARITY GUARD: skip auto-shipping if FedEx cost is way over
    # what the customer paid. Two triggers (either bails to manual review):
    #   1. FedEx cost > customer_paid * 1.15 (15% over)
    #   2. FedEx cost > customer_paid + 5.00 (fixed margin)
    customer_paid_gbp = None
    try:
        sl = order.get("shippingLine") or {}
        sp_money = (sl.get("originalPriceSet") or {}).get("shopMoney") or {}
        if sp_money.get("amount"):
            customer_paid_gbp = float(sp_money["amount"])
    except Exception:
        customer_paid_gbp = None

    fedex_cost = float(cheapest.get("price") or 0)
    if customer_paid_gbp is not None and customer_paid_gbp > 0:
        threshold_pct = customer_paid_gbp * 1.15
        threshold_abs = customer_paid_gbp + 5.0
        if fedex_cost > threshold_pct or fedex_cost > threshold_abs:
            pct_over = (fedex_cost / customer_paid_gbp - 1) * 100
            reason = (
                f"Manual Review: Shipping Discrepancy too large "
                f"(customer paid GBP{customer_paid_gbp:.2f}, "
                f"FedEx wants GBP{fedex_cost:.2f}, "
                f"+{pct_over:.1f}%)"
            )
            log.warning(f"      {reason}")
            try:
                from shopify_note import add_order_note
                add_order_note(order_name, reason)
                log.info(f"      Shopify note added for manual review")
            except Exception as note_err:
                log.error(f"      Failed to add Shopify note: {note_err}")
            return {"error": "manual_review_cost", "details": reason}

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
    # DECLARED VALUE: send the TRUE declared customs value as-is. We previously
    # scaled it up to clear the shipping cost, but FedEx validates declared
    # customs value against actual goods value and rejected the scaled "fake"
    # value as a misleading CURRENCY.TYPE.INVALID. totalDeclaredValue is for
    # declared customs value, not freight — a value below the shipping cost is
    # fine; FedEx ships low-value items every day. unitPrice / customsValue /
    # totalDeclaredValue are all derived from the actual order values in
    # build_ship_request (customsValue = round(unitPrice * quantity, 2)).
    try:
        line_items = shipment.get("line_items") or []
        if line_items:
            shipping_cost = float(cheapest.get("price") or 0)
            declared_total = round(
                sum(round((li.get("unit_value") or 0) * (li.get("quantity") or 0), 2)
                    for li in line_items),
                2,
            )
            if declared_total > 0 and declared_total < shipping_cost:
                log.info(f"      Declared value GBP{declared_total:.2f} is below shipping "
                         f"GBP{shipping_cost:.2f} — sending true declared value (no scaling)")
                _post_order_note(
                    order_name,
                    f"ℹ️ Note: Order value £{declared_total:.2f} is below shipping cost "
                    f"£{shipping_cost:.2f}. Sending true declared value."
                )
    except Exception as declared_err:
        log.error(f"      Declared-value check error: {declared_err}")

    try:
        ship_result = create_shipment(shipment)
    except requests.exceptions.HTTPError as http_err:
        # FedEx Ship API returned an HTTP error. Record a failure note on the
        # Shopify order (so there's a paper trail) BEFORE re-raising — we want
        # the crash + stack trace to stay visible, just not silent.
        resp = http_err.response
        status_code = getattr(resp, "status_code", None)
        codes, messages, tx_id = "", "", ""
        try:
            body = resp.json() if resp is not None else {}
            errors = body.get("errors") or []
            codes = ", ".join(e.get("code", "") for e in errors if e.get("code"))
            messages = " ".join(e.get("message", "") for e in errors if e.get("message"))
            tx_id = body.get("transactionId", "")
        except Exception:
            pass  # non-JSON body (e.g. 503 HTML) — fall through to generic message

        note = _plain_english_fedex_failure(status_code, codes, messages, tx_id)
        _post_order_note(order_name, note)
        raise  # re-raise the original HTTPError — keep the crash + stack trace
    except requests.exceptions.RequestException as conn_err:
        # Connection-level failure (DNS, timeout, refused) — no HTTP response.
        note = _plain_english_fedex_failure(None, "", "", "")
        _post_order_note(order_name, note)
        raise

    tracking  = ship_result["tracking_number"]
    label_pdf = ship_result["label_pdf"]   # raw bytes, already decoded
    duties_payment_type = ship_result.get("duties_payment_type")  # SENDER=DDP, RECIPIENT=DDU
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

    # 9b. For US orders, also upload customs declaration via ETD
    if country == "US":
        log.info("   8b/9 Uploading customs declaration via ETD...")
        try:
            from generate_customs_declaration import (
                build_line_items as cd_build,
                group_line_items as cd_group,
                render_customs_pdf as cd_render,
            )
            customs_raw = cd_build(order)
            customs_grouped = cd_group(customs_raw)
            customs_path = f"/tmp/Customs_{order_name.lstrip(chr(35))}.pdf"
            cd_render(order_name.lstrip("#"), customs_grouped, customs_path)

            customs_etd = upload_trade_document(
                tracking_number=tracking,
                pdf_path=customs_path,
                document_type="OTHER",
                origin_country="GB",
                destination_country=country,
            )
            customs_doc_id = customs_etd.get('output', {}).get('meta', {}).get('docId', 'n/a')
            log.info(f"      Customs ETD docId: {customs_doc_id}")

            customs_drive = upload_invoice(
                customs_path,
                os.getenv("DRIVE_FOLDER_DECLARATIONS") or os.getenv("GOOGLE_DRIVE_FOLDER_ID"),
            )
            log.info(f"      Customs Drive: {customs_drive.get('link', 'uploaded')}")
        except Exception as e:
            log.error(f"      Customs declaration upload failed (not blocking): {e}")

    # 10. Log to Sheet
    log.info("   9/9 Logging to Sheet...")

    # Pull what the customer paid for shipping (Shopify GBP)
    customer_paid_gbp = None
    try:
        sl = order.get("shippingLine") or {}
        sp_set = sl.get("originalPriceSet") or {}
        sp_money = sp_set.get("shopMoney") or {}
        if sp_money.get("amount"):
            customer_paid_gbp = float(sp_money["amount"])
    except Exception:
        customer_paid_gbp = None

    # Our cost in GBP (from cheapest FedEx rate quote)
    our_cost_gbp = None
    try:
        if cheapest.get("currency") == "GBP":
            our_cost_gbp = float(cheapest.get("price", 0))
    except Exception:
        our_cost_gbp = None

    log_shipment(
        order_name=order_name,
        tracking_number=tracking,
        country=country,
        label_drive_link=label_drive.get("link", ""),
        invoice_drive_link=invoice_drive.get("link", ""),
        service_name=cheapest.get("service_name", ""),
        our_cost_gbp=our_cost_gbp,
        customer_paid_gbp=customer_paid_gbp,
    )
    log.info(f"      ✅ Logged (cost £{our_cost_gbp}, customer paid £{customer_paid_gbp})")

    log.info(f"🎉 Done! {order_name} → {tracking}")
    # 10. Add tracking note to Shopify order (DOES NOT fulfil)
    # Pick/packers see this in the order timeline, then manually mark
    # fulfilled in Shopify after physically shipping.
    log.info("   10/10 Adding tracking note to Shopify order...")
    try:
        from shopify_note import add_order_note
        label_link = label_drive.get("link", "") if label_drive else ""
        # Duty status — derived from the actual dutiesPayment.paymentType sent
        # to FedEx, so it stays accurate even if the duty logic changes again.
        if country == "GB":
            duty_line = "Duty: N/A (UK domestic)"
        elif duties_payment_type == "RECIPIENT":
            duty_line = "Duty: DDU — customer pays duties & taxes on delivery"
        elif duties_payment_type == "SENDER":
            duty_line = "Duty: DDP — Precision paid duties & taxes"
        else:
            duty_line = f"Duty: unknown (FedEx paymentType={duties_payment_type})"
        note_lines = [
            f"FedEx label created — {cheapest['service_name']}, {cheapest['price']} {cheapest['currency']}",
            "",
            f"Tracking: {tracking}",
            duty_line,
        ]
        if label_link:
            note_lines.append(f"Label: {label_link}")
        note_text = "\n".join(note_lines)
        add_order_note(order_name, note_text)
        log.info(f"      Note added to Shopify timeline")
    except Exception as e:
        log.warning(f"      Shopify note failed (not blocking): {e}")

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
