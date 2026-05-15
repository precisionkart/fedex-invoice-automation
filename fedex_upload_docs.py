"""
FedEx Trade Documents Upload — attach customs documents to an
existing shipment after it's been created.

Uses the post-shipment workflow (ETDPostshipment).

Endpoint:    POST /documents/v1/etds/upload
Schema ref:  Trade Documents Upload API v1.0.2

Usage:
    python fedex_upload_docs.py <tracking_number> <path_to_pdf>

Example:
    python fedex_upload_docs.py 794817353470 Invoice_04058-SHP.pdf
"""

import os
import sys
import json
import requests
from datetime import datetime
from dotenv import load_dotenv
from fedex_auth import get_fedex_token, BASE_URL

load_dotenv()

ACCOUNT_NUMBER = os.getenv("FEDEX_ACCOUNT_NUMBER")


def upload_trade_document(
    tracking_number,
    pdf_path,
    document_type="COMMERCIAL_INVOICE",
    origin_country="GB",
    destination_country="DE",
    ship_date=None,
):
    """
    Upload a single trade document and attach it to a shipment by tracking number.

    Args:
        tracking_number:     The FedEx tracking number from a previously created shipment.
        pdf_path:            Path to the PDF file on disk.
        document_type:       FedEx doc types per the schema:
                                 COMMERCIAL_INVOICE, PRO_FORMA_INVOICE,
                                 CERTIFICATE_OF_ORIGIN, ETD_LABEL, OTHER,
                                 USMCA_CERTIFICATION_OF_ORIGIN,
                                 USMCA_COMMERCIAL_INVOICE_CERTIFICATION_OF_ORIGIN
        origin_country:      ISO 2-char code of shipper country.
        destination_country: ISO 2-char code of recipient country.
        ship_date:           ISO date string. Defaults to today.

    Returns:
        dict with FedEx's response details.
    """
    if not ACCOUNT_NUMBER:
        raise RuntimeError("Missing FEDEX_ACCOUNT_NUMBER in .env")
    if not os.path.exists(pdf_path):
        raise FileNotFoundError(pdf_path)

    with open(pdf_path, "rb") as f:
        pdf_bytes = f.read()
    filename = os.path.basename(pdf_path)

    if ship_date is None:
        ship_date = datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%S")

    token = get_fedex_token()["token"]
    # Trade Documents Upload uses a separate "EDU Sandbox" URL, not the main sandbox.
    # Production is on the main apis.fedex.com host.
    DOC_SANDBOX = "https://documentapitest.prod.fedex.com/sandbox"
    DOC_BASE_URL = DOC_SANDBOX if "sandbox" in BASE_URL else "https://apis.fedex.com"
    url = f"{DOC_BASE_URL}/documents/v1/etds/upload"

    # Note the exact casing from the FedEx schema:
    #   "workflowName" (lowercase f)
    #   "ETDPostshipment" (lowercase s)
    document_metadata = {
        "workflowName":   "ETDPostshipment",
        "carrierCode":    "FDXE",
        "name":           filename,
        "contentType":    "application/pdf",
        "meta": {
            "shipDocumentType":       document_type,
            "trackingNumber":         tracking_number,
            "shipmentDate":           ship_date,
            "originCountryCode":      origin_country,
            "destinationCountryCode": destination_country,
        },
    }

    # FedEx expects multipart/form-data: JSON metadata + binary file
    files = {
        "document":   (None, json.dumps(document_metadata), "application/json"),
        "attachment": (filename, pdf_bytes, "application/pdf"),
    }

    headers = {
        "Authorization": f"Bearer {token}",
        "X-locale":      "en_GB",
    }

    response = requests.post(url, headers=headers, files=files, timeout=30)
    response.raise_for_status()
    return response.json()


if __name__ == "__main__":
    if len(sys.argv) != 3:
        print("Usage: python fedex_upload_docs.py <tracking_number> <path_to_pdf>")
        print()
        print("Example:")
        print("  python fedex_upload_docs.py 794817353470 Invoice_04058-SHP.pdf")
        sys.exit(1)

    tracking = sys.argv[1]
    pdf      = sys.argv[2]

    print(f"📎 Uploading trade document:")
    print(f"   Tracking:  {tracking}")
    print(f"   PDF:       {pdf}")
    print(f"   Type:      COMMERCIAL_INVOICE")
    print(f"   Workflow:  ETDPostshipment")
    print(f"   Endpoint:  {BASE_URL}/documents/v1/etds/upload")
    print()
    print(f"☁️  Calling FedEx Trade Documents Upload API...")
    print()

    try:
        result = upload_trade_document(tracking, pdf)
    except requests.exceptions.HTTPError as e:
        print(f"❌ HTTP error: {e}")
        print(f"   Status:   {e.response.status_code}")
        print(f"   Response: {e.response.text[:2500]}")
        sys.exit(1)
    except Exception as e:
        print(f"❌ Failed: {e}")
        sys.exit(1)

    print(f"✅ Document uploaded.")
    print()
    print("Response:")
    print(json.dumps(result, indent=2)[:1500])
