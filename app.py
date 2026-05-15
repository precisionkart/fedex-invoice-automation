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


if __name__ == "__main__":
    # Local development only — production uses gunicorn (see Procfile)
    port = int(os.getenv("PORT", "5000"))
    app.run(host="0.0.0.0", port=port, debug=True)
