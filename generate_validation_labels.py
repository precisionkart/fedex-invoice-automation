"""
Generate test labels for FedEx Label Validation Group.

Creates one label per FedEx service we plan to use, against real-ish
EU/UK addresses, in sandbox. Saves PDFs to ./labels_for_validation/

Steps after running this:
  1. Print each PDF at 600 DPI minimum
  2. Scan each printed label back at 600 DPI
  3. Fill out FedEx Label Cover Sheet (linked from validation page)
  4. Email all scans + cover sheet to label@fedex.com

Usage: python generate_validation_labels.py
"""

import os
import sys
from datetime import datetime
from dotenv import load_dotenv

load_dotenv()

# Force sandbox for safety
os.environ["FEDEX_ENVIRONMENT"] = "sandbox"

from fedex_ship import create_shipment

OUTPUT_DIR = "labels_for_validation"
os.makedirs(OUTPUT_DIR, exist_ok=True)


# Shipper — Precision Kart
SHIPPER = {
    "contact": {
        "personName":  "Precision Kart",
        "phoneNumber": "+447000000000",
        "companyName": "Precision Kart",
        "emailAddress": "info@precisionkart.co.uk",
    },
    "address": {
        "streetLines":   ["Hendall Gate Farm"],
        "city":          "Uckfield",
        "stateOrProvinceCode": "GB",
        "postalCode":    "TN225LX",
        "countryCode":   "GB",
    },
}

# Realistic test destinations
DESTINATIONS = [
    {
        "name":    "Pierre Dubois",
        "phone":   "+33123456789",
        "street":  "10 Rue de Rivoli",
        "city":    "Paris",
        "zip":     "75001",
        "country": "FR",
    },
    {
        "name":    "Hans Müller",
        "phone":   "+4930123456",
        "street":  "Friedrichstraße 100",
        "city":    "Berlin",
        "zip":     "10117",
        "country": "DE",
    },
    {
        "name":    "Anna Bianchi",
        "phone":   "+390612345678",
        "street":  "Via Roma 50",
        "city":    "Rome",
        "zip":     "00184",
        "country": "IT",
    },
    {
        "name":    "Carlos Garcia",
        "phone":   "+34911234567",
        "street":  "Calle Mayor 25",
        "city":    "Madrid",
        "zip":     "28013",
        "country": "ES",
    },
    {
        "name":    "Jan de Vries",
        "phone":   "+31201234567",
        "street":  "Dam 1",
        "city":    "Amsterdam",
        "zip":     "1012JS",
        "country": "NL",
    },
]

# Services we want validated
SERVICES = [
    "FEDEX_REGIONAL_ECONOMY",
    "FEDEX_INTERNATIONAL_CONNECT_PLUS",
    "INTERNATIONAL_PRIORITY",
    "INTERNATIONAL_PRIORITY_EXPRESS",
    "INTERNATIONAL_ECONOMY",
]


def recipient_from(dest):
    return {
        "contact": {"personName": dest["name"], "phoneNumber": dest["phone"]},
        "address": {
            "streetLines":  [dest["street"]],
            "city":         dest["city"],
            "postalCode":   dest["zip"],
            "countryCode":  dest["country"],
        },
    }


def make_shipment(service, destination):
    """Build a representative shipment for label validation."""
    return {
        "shipper":     SHIPPER,
        "recipient":   recipient_from(destination),
        "service_type": service,
        "package": {
            "weight_kg":     0.8,
            "length_cm":     26,
            "width_cm":      24,
            "height_cm":     4,
            "currency":      "GBP",
            "declared_value": 12.0,
        },
        "line_items": [{
            "description":       "GO KART NYLON CHAIN GUARD PROTECTOR | MADE IN UK",
            "country_of_origin": "GB",
            "hs_code":           "870899",
            "quantity":          1,
            "weight_kg":         0.8,
            "unit_value":        12.0,
            "currency":          "GBP",
        }],
    }


def main():
    print(f"🛫 Generating {len(SERVICES)} test labels for FedEx validation\n")

    for i, service in enumerate(SERVICES, 1):
        # Pair each service with a different destination for variety
        destination = DESTINATIONS[(i - 1) % len(DESTINATIONS)]
        country     = destination["country"]
        print(f"{i}/{len(SERVICES)}  {service:35s} → {country}")

        try:
            shipment = make_shipment(service, destination)
            result   = create_shipment(shipment)

            tracking = result["tracking_number"]
            label    = result["label_pdf"]

            # Clean filename
            safe_service = service.replace("_", "-").lower()
            filename = f"{i:02d}_{safe_service}_{country}_{tracking}.pdf"
            path     = os.path.join(OUTPUT_DIR, filename)

            with open(path, "wb") as f:
                f.write(label)

            print(f"       ✅ {path}\n")

        except Exception as e:
            print(f"       ❌ {str(e)[:200]}\n")
            continue

    print("=" * 60)
    print(f"Done. Labels saved to: ./{OUTPUT_DIR}/")
    print()
    print("Next steps:")
    print("  1. Open each PDF and print at 600 DPI minimum")
    print("  2. Scan each printed label back at 600 DPI")
    print("  3. Fill out the FedEx Label Cover Sheet (from developer portal)")
    print("  4. Email everything to label@fedex.com:")
    print("       Subject: Label validation submission — Precision Kart")
    print("                Account 207751841")


if __name__ == "__main__":
    main()
