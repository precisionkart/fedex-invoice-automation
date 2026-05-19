"""
Comprehensive pre-flight check before first real shipment.
Tests every API, integration, and file path the pipeline depends on.
NO real shipment is created. NO real charges.

Usage: python preflight_check.py
"""

import os
import sys
import io
import time
import tempfile
from dotenv import load_dotenv

load_dotenv()

# Color codes for terminal output
GREEN  = "\033[92m"
YELLOW = "\033[93m"
RED    = "\033[91m"
RESET  = "\033[0m"
BOLD   = "\033[1m"

failures = []
warnings = []


def check(name):
    """Decorator to print check name + capture result."""
    def wrapper(func):
        def inner():
            print(f"  Checking {name}...", end=" ", flush=True)
            try:
                result = func()
                print(f"{GREEN}✅{RESET} {result}")
                return True
            except Exception as e:
                print(f"{RED}❌{RESET}\n     {str(e)[:200]}")
                failures.append((name, str(e)))
                return False
        return inner
    return wrapper


@check("1. .env file present + all required vars")
def check_env():
    required = [
        "SHOPIFY_STORE_DOMAIN", "SHOPIFY_CLIENT_ID", "SHOPIFY_CLIENT_SECRET",
        "FEDEX_CLIENT_ID", "FEDEX_CLIENT_SECRET", "FEDEX_ACCOUNT_NUMBER",
        "FEDEX_ENVIRONMENT", "GOOGLE_DRIVE_FOLDER_ID", "SHIPPING_LOG_SHEET_ID",
        "FEDEX_TRACK_CLIENT_ID", "FEDEX_TRACK_CLIENT_SECRET", "FEDEX_TRACK_ACCOUNT_NUMBER",
    ]
    missing = [v for v in required if not os.getenv(v)]
    if missing:
        raise RuntimeError(f"Missing: {', '.join(missing)}")
    env = os.getenv("FEDEX_ENVIRONMENT")
    return f"All {len(required)} vars set. FedEx env: {env}"


@check("2. FedEx Ship/Rates auth (production)")
def check_fedex_auth():
    from fedex_auth import get_fedex_token, BASE_URL
    token = get_fedex_token()["token"]
    env = "PRODUCTION" if "sandbox" not in BASE_URL else "SANDBOX"
    return f"Token acquired ({env})"


@check("3. FedEx Track auth (production)")
def check_track_auth():
    from fedex_track_auth import get_track_token, BASE_URL
    token = get_track_token()["token"]
    env = "PRODUCTION" if "sandbox" not in BASE_URL else "SANDBOX"
    return f"Token acquired ({env})"


@check("4. Shopify GraphQL connection")
def check_shopify():
    from generate_invoice import get_access_token, graphql
    token = get_access_token()
    data = graphql(token, "{ shop { name } }")
    return f"Connected to '{data['shop']['name']}'"


@check("5. Google Drive write access")
def check_drive():
    from drive_upload import upload_invoice
    # Make a tiny test PDF
    import io
    from reportlab.pdfgen import canvas
    fd, path = tempfile.mkstemp(suffix=".pdf")
    c = canvas.Canvas(path)
    c.drawString(100, 750, "Preflight test — safe to delete")
    c.save()
    os.close(fd)
    result = upload_invoice(path, os.getenv("GOOGLE_DRIVE_FOLDER_ID"))
    os.unlink(path)
    return f"Uploaded test PDF: id={result.get('id', 'unknown')[:10]}..."


@check("6. Google Sheet read access")
def check_sheet_read():
    from shipping_log import find_packed_unfulfilled
    rows = find_packed_unfulfilled()
    return f"Sheet readable, {len(rows)} row(s) in 'label_created' state"


@check("7. FedEx Rates API call (real shipment data)")
def check_rates():
    from fedex_rates import get_rates
    test = {
        "shipper": {
            "contact": {"personName": "Precision Kart", "phoneNumber": "+447000000000"},
            "address": {"streetLines": ["Hendall Gate Farm"], "city": "Uckfield",
                        "stateOrProvinceCode": "GB", "postalCode": "TN225LX", "countryCode": "GB"},
        },
        "recipient": {
            "contact": {"personName": "Test", "phoneNumber": "+33000000000"},
            "address": {"streetLines": ["1 rue de test"], "city": "Paris",
                        "postalCode": "75001", "countryCode": "FR"},
        },
        "package": {"weight_kg": 0.5, "length_cm": 20, "width_cm": 15, "height_cm": 5,
                    "currency": "GBP", "declared_value": 10.0},
    }
    rates = get_rates(test)
    if not rates:
        raise RuntimeError("No rates returned")
    return f"{len(rates)} services, cheapest {rates[0]['currency']} {rates[0]['price']:.2f}"


@check("8. Box chooser logic (basic sanity)")
def check_box_chooser():
    from box_chooser import choose_package
    result = choose_package([{
        "title": "Test", "length_cm": 10, "width_cm": 10, "height_cm": 5,
        "weight_kg": 0.2, "quantity": 1,
    }])
    if result.get("manual_review"):
        raise RuntimeError(f"Box logic rejected basic 10×10×5 item: {result.get('reason')}")
    return f"Picks {result['package_name']} for 10×10×5cm item"


@check("9. Track API for a known sandbox tracking number")
def check_track_call():
    """Confirms Track API endpoint is reachable. 404 is expected (sandbox number)."""
    from fedex_track_auth import get_track_token, BASE_URL
    import requests
    token = get_track_token()["token"]
    response = requests.post(
        f"{BASE_URL}/track/v1/trackingnumbers",
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json",
                 "X-locale": "en_GB"},
        json={"trackingInfo": [{"trackingNumberInfo": {"trackingNumber": "794817397480"}}],
              "includeDetailedScans": True},
        timeout=20,
    )
    # 200 or 404 both confirm endpoint is reachable — production won't find sandbox numbers
    if response.status_code not in (200, 404):
        raise RuntimeError(f"Unexpected status {response.status_code}: {response.text[:200]}")
    return f"Endpoint reachable (status {response.status_code})"


def main():
    print()
    print(f"{BOLD}🛫 PRE-FLIGHT CHECK — Precision Kart Shipping Pipeline{RESET}")
    print("=" * 60)
    print()

    checks = [check_env, check_fedex_auth, check_track_auth, check_shopify,
              check_drive, check_sheet_read, check_rates, check_box_chooser, check_track_call]
    results = [c() for c in checks]
    passed = sum(1 for r in results if r)

    print()
    print("=" * 60)
    if passed == len(checks):
        print(f"{GREEN}{BOLD}🎉 ALL {passed}/{len(checks)} CHECKS PASSED — Ready for real shipment{RESET}")
        print()
        print("To create the real shipment now, run:")
        print(f"  python ship_real_order.py 04065-SHP")
    else:
        print(f"{RED}{BOLD}⚠️  {passed}/{len(checks)} passed — fix these before going live:{RESET}")
        for name, err in failures:
            print(f"  • {name}: {err[:150]}")


if __name__ == "__main__":
    main()
