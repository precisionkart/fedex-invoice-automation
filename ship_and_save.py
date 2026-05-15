"""
End-to-end test: create a FedEx shipment, save the label PDF to
Google Drive named by the order number.

This wires together:
  - fedex_ship.py (creates shipment, returns label PDF)
  - drive_upload.py (uploads PDF to Drive in /FedEx Shipments/YYYY/MM/)

Usage:
    python ship_and_save.py <order_number>

Example:
    python ship_and_save.py 04058-SHP

(Currently uses hardcoded test shipment data — wire to real
Shopify orders in next session.)
"""

import os
import sys
from datetime import datetime
from dotenv import load_dotenv

from fedex_ship import create_shipment, TEST_SHIPMENT
from drive_upload import upload_invoice
from shipping_log import log_shipment  # we'll reuse this — same logic works for labels

load_dotenv()
DRIVE_FOLDER = os.getenv("GOOGLE_DRIVE_FOLDER_ID")


def ship_and_save(order_number):
    print(f"📦 Order: {order_number}")
    print(f"🚚 Creating FedEx shipment...")
    print()

    # 1. Create the shipment with FedEx
    result = create_shipment(TEST_SHIPMENT)
    tracking  = result["tracking_number"]
    label_pdf = result["label_pdf"]

    print(f"   ✅ Tracking: {tracking}")
    print(f"   ✅ Service:  {result['service_used']}")

    # 2. Save the label to disk with the order number as the filename
    safe_name = "".join(c if c.isalnum() or c in "-_" else "_"
                        for c in str(order_number).lstrip("#"))
    local_path = f"/tmp/{safe_name}_label.pdf"
    with open(local_path, "wb") as f:
        f.write(label_pdf)
    print(f"   ✅ Label saved locally to: {local_path}")
    print()

    # 3. Upload to Google Drive
    if not DRIVE_FOLDER:
        print("⚠️  GOOGLE_DRIVE_FOLDER_ID not set in .env — skipped Drive upload.")
        return

    print(f"☁️  Uploading to Google Drive...")
    upload_result = upload_invoice(local_path, DRIVE_FOLDER, order_date=datetime.utcnow())
    print(f"   ✅ Uploaded to /FedEx Invoices/{upload_result['folder']}/{upload_result['name']}")
    print(f"   🔗 {upload_result['link']}")
    print()
    # Append to shipping log
    try:
        log_shipment(
            order_name=order_number,
            tracking_number=tracking,
            country="GB",  # TODO: pull real country from Shopify order later
            label_drive_link=upload_result.get("link", ""),
            invoice_drive_link="",
        )
        print(f"📋 Logged to shipping log Sheet")
    except Exception as e:
        print(f"⚠️  Shipping log write failed: {e}")

    print(f"🎉 End-to-end complete: Shipment → Label → Drive → Log")


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print("Usage: python ship_and_save.py <order_number>")
        print("Example: python ship_and_save.py 04058-SHP")
        sys.exit(1)

    ship_and_save(sys.argv[1])
