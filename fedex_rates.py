"""
FedEx Rates & Transit Times — get shipping prices for a package.

Takes a from/to address and package details, returns all
available services with their prices. We pick the cheapest.

Run directly to test with hardcoded UK→Germany shipment:
    python fedex_rates.py
"""

import os
import sys
import json
import requests
from dotenv import load_dotenv
from fedex_auth import get_fedex_token, BASE_URL

load_dotenv()

ACCOUNT_NUMBER = os.getenv("FEDEX_ACCOUNT_NUMBER")


# Test shipment — UK to Germany, ~1kg parcel
# We'll replace this with real Shopify order data later
TEST_SHIPMENT = {
    "shipper": {
        "address": {
            "streetLines":  ["Unit 2 Pacemanor, Bellbrook Ind Estate"],
            "city":         "Uckfield",
            "stateOrProvinceCode": "",
            "postalCode":   "TN22 1YA",
            "countryCode":  "GB",
        },
    },
    "recipient": {
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
        "declared_value": 50.00,
        "currency":       "GBP",
    },
}


def build_rate_request(shipment, account_number):
    """Construct the JSON body FedEx expects for a rate quote."""
    pkg = shipment["package"]
    return {
        "accountNumber": {"value": account_number},
        "requestedShipment": {
            "shipper":   shipment["shipper"],
            "recipient": shipment["recipient"],
            "preferredCurrency": pkg["currency"],
            "rateRequestType": ["ACCOUNT", "LIST"],
            "pickupType": "DROPOFF_AT_FEDEX_LOCATION",
            "requestedPackageLineItems": [{
                "weight": {
                    "units": "KG",
                    "value": pkg["weight_kg"],
                },
                "dimensions": {
                    "length": pkg["length_cm"],
                    "width":  pkg["width_cm"],
                    "height": pkg["height_cm"],
                    "units":  "CM",
                },
                "declaredValue": {
                    "amount":   pkg["declared_value"],
                    "currency": pkg["currency"],
                },
            }],
        },
    }


def get_rates(shipment):
    """Call FedEx Rates API, return a list of available services + prices."""
    if not ACCOUNT_NUMBER:
        raise RuntimeError("Missing FEDEX_ACCOUNT_NUMBER in .env")

    token = get_fedex_token()["token"]
    url = f"{BASE_URL}/rate/v1/rates/quotes"

    body = build_rate_request(shipment, ACCOUNT_NUMBER)
    headers = {
        "Authorization":  f"Bearer {token}",
        "Content-Type":   "application/json",
        "X-locale":       "en_GB",
    }

    response = requests.post(url, headers=headers, json=body, timeout=20)
    response.raise_for_status()
    data = response.json()

    services = []
    rate_details = data.get("output", {}).get("rateReplyDetails", [])
    for r in rate_details:
        service_type = r.get("serviceType")
        service_name = r.get("serviceName") or service_type
        commit       = r.get("commit", {})
        transit_days = commit.get("transitDays", {}).get("description", "")
        rated_shipments = r.get("ratedShipmentDetails", [])
        if not rated_shipments:
            continue
        rs = rated_shipments[0]
        total = rs.get("totalNetCharge")
        currency = rs.get("currency", "GBP")
        services.append({
            "service_type": service_type,
            "service_name": service_name,
            "price":        float(total) if total else None,
            "currency":     currency,
            "transit_days": transit_days,
        })

    services.sort(key=lambda s: (s["price"] is None, s["price"] or 0))
    return services


if __name__ == "__main__":
    print(f"📦 Test shipment:")
    s = TEST_SHIPMENT
    print(f"   From: {s['shipper']['address']['postalCode']} {s['shipper']['address']['countryCode']}")
    print(f"   To:   {s['recipient']['address']['postalCode']} {s['recipient']['address']['countryCode']}")
    print(f"   Weight: {s['package']['weight_kg']} kg")
    print(f"   Dims:   {s['package']['length_cm']}×{s['package']['width_cm']}×{s['package']['height_cm']} cm")
    print(f"   Value:  {s['package']['declared_value']} {s['package']['currency']}")
    print()
    print(f"🔍 Asking FedEx for rates...")
    print()

    try:
        services = get_rates(TEST_SHIPMENT)
    except requests.exceptions.HTTPError as e:
        print(f"❌ HTTP error: {e}")
        print(f"   Status:   {e.response.status_code}")
        print(f"   Response: {e.response.text[:1500]}")
        sys.exit(1)
    except Exception as e:
        print(f"❌ Failed: {e}")
        sys.exit(1)

    if not services:
        print("⚠️  No services returned. Either no service available for this route, or sandbox data is sparse.")
        sys.exit(0)

    print(f"✅ Found {len(services)} service(s):")
    print()
    for i, svc in enumerate(services, 1):
        price = f"{svc['currency']} {svc['price']:.2f}" if svc['price'] else "—"
        marker = " 🏆 CHEAPEST" if i == 1 else ""
        print(f"   {i}. {svc['service_name']:<45s} {price:>15s}  {svc['transit_days']}{marker}")
    print()
    print(f"Pick: {services[0]['service_name']} at {services[0]['currency']} {services[0]['price']:.2f}")
