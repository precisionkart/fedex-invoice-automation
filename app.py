# === BOOT: write credential files from env ===
import startup  # noqa: E402
# === END BOOT ===

"""
Webhook server.
Receives 'order paid' webhooks from Shopify, generates a commercial invoice,
and uploads it to Google Drive.

Endpoints:
  GET  /                — health check, returns 'ok'
  POST /webhook/orders  — Shopify webhook target

Shopify webhooks are verified using HMAC-SHA256 with the shared webhook
secret (different from the OAuth Client Secret). Requests without a valid
signature are rejected.
"""

import os
import hmac
import hashlib
import base64
import json
import logging
import traceback
from datetime import datetime, timezone
from flask import Flask, request, abort
from dotenv import load_dotenv

# Reuse the pieces we already wrote
from generate_invoice import (
    get_access_token,
    fetch_order,
    build_invoice_from_order,
    render_pdf,
)
from drive_upload import upload_invoice
from country_router import classify_destination
from shipping_log import find_shipment, update_status
from shopify_fulfill import fulfill_order

load_dotenv()

WEBHOOK_SECRET = os.getenv("SHOPIFY_WEBHOOK_SECRET")
DRIVE_FOLDER   = os.getenv("GOOGLE_DRIVE_FOLDER_ID")

logging.basicConfig(level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger(__name__)

app = Flask(__name__)


def verify_shopify_hmac(body_bytes, signature_header):
    """Verify the request really came from Shopify."""
    if not WEBHOOK_SECRET:
        log.warning("SHOPIFY_WEBHOOK_SECRET not set — refusing to verify.")
        return False
    if not signature_header:
        return False
    digest = hmac.new(WEBHOOK_SECRET.encode("utf-8"),
                      body_bytes,
                      hashlib.sha256).digest()
    expected = base64.b64encode(digest).decode("utf-8")
    return hmac.compare_digest(expected, signature_header)


@app.route("/", methods=["GET"])
def health():
    return "ok", 200


@app.route("/webhook/orders", methods=["POST"])
def orders_webhook():
    raw_body  = request.get_data()
    signature = request.headers.get("X-Shopify-Hmac-Sha256", "")
    topic     = request.headers.get("X-Shopify-Topic", "")
    shop      = request.headers.get("X-Shopify-Shop-Domain", "")

    log.info(f"Webhook received: topic={topic} shop={shop} bytes={len(raw_body)}")

    if os.getenv("SKIP_HMAC_CHECK") == "true":
        log.warning("⚠️  HMAC verification SKIPPED (SKIP_HMAC_CHECK=true). Set to 'false' or remove to re-enable.")
    elif not verify_shopify_hmac(raw_body, signature):
        log.warning("HMAC verification failed — request rejected.")
        abort(401)

    try:
        payload = json.loads(raw_body)
    except Exception as e:
        log.error(f"Bad JSON body: {e}")
        abort(400)

    order_name = payload.get("name") or payload.get("order_number")
    if not order_name:
        log.error("No order name/number in payload.")
        return ("missing order name", 400)

    log.info(f"Processing order {order_name}")

    try:
        token = get_access_token()
        order = fetch_order(token, str(order_name))

        # --- Country routing ---
        shipping_country = (order.get("shippingAddress") or {}).get("countryCodeV2", "")
        routing = classify_destination(shipping_country)
        log.info(f"Country routing: {shipping_country} → {routing['action']} ({routing['region']}) — {routing['reason']}")

        if routing["action"] == "skip":
            log.info(f"⏭️  Skipping order {order_name} — {routing['reason']}")
            return ("skipped:uk", 200)

        if routing["action"] == "manual_review":
            log.warning(f"⚠️  Order {order_name} needs manual review — {routing['reason']}")
            return ("manual_review", 200)

        # If we got here, action == "ship" (EU or US)
        if routing["needs_customs_pdf"]:
            log.info(f"📋 US order — customs declaration PDF will be required (not yet implemented)")

        invoice, warnings = build_invoice_from_order(order)

        for w in warnings:
            log.warning(f"DATA: {w}")

        safe_name = "".join(c if c.isalnum() or c in "-_" else "_"
                            for c in str(order_name).lstrip("#"))
        local_path = f"/tmp/Invoice_{safe_name}.pdf"
        render_pdf(invoice, local_path)
        log.info(f"PDF written to {local_path}")

        if DRIVE_FOLDER:
            order_dt = datetime.fromisoformat(order["createdAt"].replace("Z", "+00:00"))
            result = upload_invoice(local_path, DRIVE_FOLDER, order_date=order_dt)
            log.info(f"Uploaded to Drive: {result['link']}")
        else:
            log.warning("GOOGLE_DRIVE_FOLDER_ID not set — skipped Drive upload.")

        return ("ok", 200)

    except Exception as e:
        log.error(f"Failed to process {order_name}: {e}")
        log.error(traceback.format_exc())
        # Return 200 anyway so Shopify doesn't retry storms.
        # We log so we can investigate later.
        return ("logged", 200)

# ============================================================
# FedEx Tracking Webhook
# Receives push notifications from FedEx when shipment events
# happen (Picked Up, In Transit, Delivered, etc.)
# ============================================================
@app.route("/webhook/fedex/track", methods=["POST"])
def fedex_track_webhook():
    raw_body = request.get_data()
    log.info(f"FedEx tracking webhook received: bytes={len(raw_body)}")

    try:
        payload = json.loads(raw_body)
    except Exception as e:
        log.error(f"Bad JSON body from FedEx: {e}")
        return ("bad json", 400)

    tracking_number = (
        payload.get("trackingNumber")
        or payload.get("trackingNbr")
        or (payload.get("output", {}) or {}).get("trackingNumber")
    )
    events = (
        payload.get("events")
        or payload.get("notifications")
        or (payload.get("output", {}) or {}).get("events")
        or []
    )

    if not tracking_number:
        log.warning(f"FedEx webhook missing tracking number. Payload keys: {list(payload.keys())}")
        return ("missing tracking", 200)

    log.info(f"📍 FedEx event for tracking {tracking_number} — {len(events)} event(s)")

    for event in events:
        event_type = event.get("eventType") or event.get("type") or ""
        description = event.get("eventDescription") or event.get("description") or ""
        timestamp = event.get("eventTimestamp") or event.get("timestamp") or ""
        location = (event.get("scanLocation") or {}).get("city") or event.get("location") or ""

        log.info(f"   {event_type:6s} {description:30s} {timestamp} {location}")

        if event_type == "PU":
            log.info(f"🚚 Package PICKED UP — looking up shipment...")
            try:
                shipment = find_shipment(tracking_number)
                if not shipment:
                    log.warning(f"   No matching shipping log row for {tracking_number} — manual review")
                    continue
                order_name = shipment["order"]
                if shipment["status"] == "fulfilled":
                    log.info(f"   Order {order_name} already fulfilled, skipping")
                    continue
                log.info(f"   Fulfilling Shopify order {order_name}...")
                fulfill_order(order_name, tracking_number)
                update_status(shipment["row_index"], status="fulfilled",
                              last_event=f"Picked up — {location}")
                log.info(f"   ✅ Order {order_name} marked fulfilled in Shopify + Sheet updated")
            except Exception as e:
                log.error(f"   ❌ Failed to auto-fulfill: {e}")
        elif event_type in ("DL", "OD"):
            log.info(f"📦 Delivered or out-for-delivery — informational only")

    return ("ok", 200)


if __name__ == "__main__":
    # Local development only — production uses gunicorn (see Procfile)
    port = int(os.getenv("PORT", "5000"))
    app.run(host="0.0.0.0", port=port, debug=True)
