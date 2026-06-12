"""
FedEx Ship API — create a shipment, get a label + tracking number.

Sandbox usage doesn't create real shipments or charge anything.
The tracking number returned will be a test number that doesn't
actually track.

Run directly to test with a hardcoded UK→Germany shipment:
    python fedex_ship.py
"""

import os
import sys
import base64
import json
import requests
from datetime import datetime, timedelta
from dotenv import load_dotenv
from fedex_auth import get_fedex_token, BASE_URL

load_dotenv()

ACCOUNT_NUMBER = os.getenv("FEDEX_ACCOUNT_NUMBER")


# FedEx uses non-standard currency codes in the Ship API.
# NOTE: GBP is just "GBP" — "UKL" is NOT a valid code and FedEx rejects it.
FEDEX_CURRENCY = {
    "CAD": "CDN",
}

def fedex_currency(iso_code):
    return FEDEX_CURRENCY.get(iso_code, iso_code)


# Test shipment — UK to Germany, one kart part
TEST_SHIPMENT = {
    "service_type": "FEDEX_REGIONAL_ECONOMY",
    "shipper": {
        "contact": {
            "personName":   "Precision Kart",
            "phoneNumber":  "01825761111",
            "companyName":  "Precision Kart",
        },
        "address": {
            "streetLines":  ["Unit 2 Pacemanor, Bellbrook Ind Estate"],
            "city":         "Uckfield",
            "stateOrProvinceCode": "",
            "postalCode":   "TN221YA",
            "countryCode":  "GB",
        },
    },
    "recipient": {
        "contact": {
            "personName":   "Test Customer",
            "phoneNumber":  "+4930123456",
            "companyName":  "",
        },
        "address": {
            "streetLines":  ["Musterstraße 1"],
            "city":         "Berlin",
            "stateOrProvinceCode": "",
            "postalCode":   "10115",
            "countryCode":  "DE",
        },
    },
    "package": {
        "weight_kg":      1.0,
        "length_cm":      20,
        "width_cm":       15,
        "height_cm":      10,
    },
    "line_items": [
        {
            "description":      "GO KART ROLLER CHAIN",
            "quantity":         1,
            "hs_code":          "7315.11",
            "country_of_origin": "GB",
            "weight_kg":        1.0,
            "unit_value":       50.00,
            "currency":         "GBP",
        },
    ],
}


def build_ship_request(shipment, account_number):
    """Construct the JSON body FedEx expects to create a shipment."""
    pkg = shipment["package"]
    # Round each commodity's customs value to 2dp first, then sum — so
    # totalDeclaredValue exactly equals the sum of the per-commodity
    # customsValue amounts (FedEx rejects TOTALCARRIAGEVALUE.EXCEEDS.CUSTOMSVALUE
    # on float artifacts like 14.100000000000001 vs 14.1).
    total_declared = round(
        sum(round(li["unit_value"] * li["quantity"], 2) for li in shipment["line_items"]),
        2,
    )
    primary_currency = shipment["line_items"][0]["currency"]

    return {
        "labelResponseOptions": "LABEL",
        "accountNumber": {"value": account_number},
        "requestedShipment": {
            "shipper":   shipment["shipper"],
            "recipients": [shipment["recipient"]],
            "shipDatestamp": (datetime.utcnow() + timedelta(days=1)).strftime("%Y-%m-%d"),
            "serviceType":         shipment["service_type"],
            "packagingType":       "YOUR_PACKAGING",
            "pickupType":          "DROPOFF_AT_FEDEX_LOCATION",
            "totalDeclaredValue": {
                "amount":   total_declared,
                "currency": fedex_currency(primary_currency),
            },
            "shippingChargesPayment": {
                "paymentType": "SENDER",
                "payor": {
                    "responsibleParty": {
                        "accountNumber": {"value": account_number},
                    },
                },
            },
            "shipmentSpecialServices": {
                "specialServiceTypes": ["ELECTRONIC_TRADE_DOCUMENTS"],
                "etdDetail": {
                    "attributes": ["POST_SHIPMENT_UPLOAD_REQUESTED"],
                },
            },
            "customsClearanceDetail": {
                "dutiesPayment": {
                    "paymentType": "RECIPIENT",
                },
                "commodities": [
                    {
                        "description":     li["description"],
                        "countryOfManufacture": li["country_of_origin"],
                        "harmonizedCode":  li["hs_code"],
                        "quantity":        li["quantity"],
                        "quantityUnits":   "PCS",
                        "weight": {
                            "units": "KG",
                            "value": round(li["weight_kg"], 3),
                        },
                        "unitPrice": {
                            "amount":   li["unit_value"],
                            "currency": fedex_currency(li["currency"]),
                        },
                        "customsValue": {
                            "amount":   round(li["unit_value"] * li["quantity"], 2),
                            "currency": fedex_currency(li["currency"]),
                        },
                    }
                    for li in shipment["line_items"]
                ],
            },
            "labelSpecification": {
                "imageType":      "PDF",
                "labelStockType": "STOCK_4X6",
            },
            "shippingDocumentSpecification": {
                "shippingDocumentTypes": []
            },
            "requestedPackageLineItems": [{
                "weight": {
                    "units": "KG",
                    "value": round(pkg["weight_kg"], 3),
                },
                "dimensions": {
                    "length": pkg["length_cm"],
                    "width":  pkg["width_cm"],
                    "height": pkg["height_cm"],
                    "units":  "CM",
                },
                "customerReferences": [
                    {
                        "customerReferenceType": "CUSTOMER_REFERENCE",
                        "value": str(shipment.get("order_name", ""))[:40],
                    }
                ] if shipment.get("order_name") else [],
            }],
        },
    }


def create_shipment(shipment):
    """Call FedEx Ship API, return tracking number + label PDF bytes."""
    if not ACCOUNT_NUMBER:
        raise RuntimeError("Missing FEDEX_ACCOUNT_NUMBER in .env")

    token = get_fedex_token()["token"]
    url = f"{BASE_URL}/ship/v1/shipments"

    body = build_ship_request(shipment, ACCOUNT_NUMBER)
    headers = {
        "Authorization":  f"Bearer {token}",
        "Content-Type":   "application/json",
        "X-locale":       "en_GB",
    }

    import json as _json
    response = requests.post(url, headers=headers, json=body, timeout=30)
    if response.status_code >= 400:
        print(f"⚠️  FedEx Ship API returned {response.status_code}")
        print(f"   Response: {response.text[:3000]}")
        print(f"   Request body sent:")
        print(_json.dumps(body, indent=2)[:5000])
    response.raise_for_status()
    data = response.json()

    output = data.get("output", {})
    transactions = output.get("transactionShipments", [])
    if not transactions:
        raise RuntimeError(f"No transactionShipments in response: {data}")

    tx = transactions[0]
    pieces = tx.get("pieceResponses", [])
    if not pieces:
        raise RuntimeError(f"No pieceResponses in shipment: {tx}")

    piece = pieces[0]
    tracking_number = piece.get("trackingNumber")

    docs = piece.get("packageDocuments", [])
    if not docs:
        raise RuntimeError(f"No packageDocuments returned: {piece}")
    label_b64 = docs[0].get("encodedLabel")
    if not label_b64:
        raise RuntimeError(f"No encodedLabel in document: {docs[0]}")
    label_pdf = base64.b64decode(label_b64)

    return {
        "tracking_number": tracking_number,
        "label_pdf":       label_pdf,
        "service_used":    tx.get("serviceType"),
        "ship_date":       body["requestedShipment"]["shipDatestamp"],
        # The actual duties paymentType sent to FedEx (SENDER=DDP, RECIPIENT=DDU).
        # Read from the request body so it stays accurate if the code changes.
        "duties_payment_type": body["requestedShipment"]
            .get("customsClearanceDetail", {})
            .get("dutiesPayment", {})
            .get("paymentType"),
    }


if __name__ == "__main__":
    s = TEST_SHIPMENT
    print(f"📦 Creating test shipment:")
    print(f"   Service: {s['service_type']}")
    print(f"   From:    {s['shipper']['address']['postalCode']} {s['shipper']['address']['countryCode']}")
    print(f"   To:      {s['recipient']['address']['postalCode']} {s['recipient']['address']['countryCode']}")
    print(f"   Items:   {len(s['line_items'])} line item(s)")
    print()
    print(f"🚚 Calling FedEx Ship API...")
    print()

    try:
        result = create_shipment(TEST_SHIPMENT)
    except requests.exceptions.HTTPError as e:
        print(f"❌ HTTP error: {e}")
        print(f"   Status:   {e.response.status_code}")
        print(f"   Response: {e.response.text[:2500]}")
        sys.exit(1)
    except Exception as e:
        print(f"❌ Failed: {e}")
        sys.exit(1)

    out_path = f"/tmp/test_label_{result['tracking_number']}.pdf"
    with open(out_path, "wb") as f:
        f.write(result["label_pdf"])

    print(f"✅ Shipment created!")
    print(f"   Tracking number: {result['tracking_number']}")
    print(f"   Service used:    {result['service_used']}")
    print(f"   Ship date:       {result['ship_date']}")
    print(f"   Label saved to:  {out_path}")
    print()
    print(f"💡 Open the PDF to verify:")
    print(f"   open {out_path}")
