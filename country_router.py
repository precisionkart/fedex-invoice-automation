"""
Country routing logic for the FedEx automation.

For each Shopify order, decides what we should do based on the destination:
  - UK       → skip (handled manually)
  - EU       → run automation, no extra customs declaration PDF
  - US       → run automation, generate customs declaration PDF
  - Other    → flag for manual review, don't ship

Importable function:
    from country_router import classify_destination
    decision = classify_destination("DE")
    # → {"action": "ship", "region": "EU", "needs_customs_pdf": False}
"""

# UK = skip entirely
UK_COUNTRIES = {"GB"}

# EU member states (27)
EU_COUNTRIES = {
    "AT",  # Austria
    "BE",  # Belgium
    "BG",  # Bulgaria
    "HR",  # Croatia
    "CY",  # Cyprus
    "CZ",  # Czech Republic
    "DK",  # Denmark
    "EE",  # Estonia
    "FI",  # Finland
    "FR",  # France
    "DE",  # Germany
    "GR",  # Greece
    "HU",  # Hungary
    "IE",  # Ireland
    "IT",  # Italy
    "LV",  # Latvia
    "LT",  # Lithuania
    "LU",  # Luxembourg
    "MT",  # Malta
    "NL",  # Netherlands
    "PL",  # Poland
    "PT",  # Portugal
    "RO",  # Romania
    "SK",  # Slovakia
    "SI",  # Slovenia
    "ES",  # Spain
    "SE",  # Sweden
}

# US — run automation + needs separate customs declaration PDF
US_COUNTRIES = {"US"}


def classify_destination(country_code):
    """
    Decide what to do with an order based on its destination country.

    Args:
        country_code: ISO 2-letter country code (e.g. 'GB', 'DE', 'US')

    Returns:
        dict with:
          - action: 'skip' | 'ship' | 'manual_review'
          - region: 'UK' | 'EU' | 'US' | 'OTHER'
          - needs_customs_pdf: True for US, False otherwise
          - reason: human-readable explanation
    """
    if not country_code:
        return {
            "action":            "manual_review",
            "region":            "OTHER",
            "needs_customs_pdf": False,
            "reason":            "Missing country code",
        }

    code = country_code.upper().strip()

    if code in UK_COUNTRIES:
        return {
            "action":            "skip",
            "region":            "UK",
            "needs_customs_pdf": False,
            "reason":            "UK domestic order — handled manually",
        }

    if code in EU_COUNTRIES:
        return {
            "action":            "ship",
            "region":            "EU",
            "needs_customs_pdf": False,
            "reason":            f"EU destination ({code}) — commercial invoice only",
        }

    if code in US_COUNTRIES:
        return {
            "action":            "ship",
            "region":            "US",
            "needs_customs_pdf": True,
            "reason":            "US destination — commercial invoice + customs declaration",
        }

    return {
        "action":            "manual_review",
        "region":            "OTHER",
        "needs_customs_pdf": False,
        "reason":            f"Destination {code} not in routing rules — manual review needed",
    }


if __name__ == "__main__":
    # Quick test — run against a few real countries
    print("🌍 Country routing test:")
    print()

    test_cases = [
        ("GB", "UK order (should skip)"),
        ("DE", "Germany order (EU)"),
        ("PT", "Portugal order #04065 (EU)"),
        ("US", "US order (needs customs PDF)"),
        ("CA", "Canada order (manual review)"),
        ("AU", "Australia order (manual review)"),
        ("AE", "UAE order (manual review)"),
        ("",   "Missing country code"),
        ("gb", "Lowercase code (should still work)"),
    ]

    for code, description in test_cases:
        result = classify_destination(code)
        action_emoji = {
            "skip":          "⏭️ ",
            "ship":          "🚚 ",
            "manual_review": "⚠️  ",
        }[result["action"]]
        print(f"   {action_emoji} {code or '(empty)':5s} → {result['action']:14s} ({result['region']:6s}) — {result['reason']}")
        if result["needs_customs_pdf"]:
            print(f"          📋 Needs customs declaration PDF")

    print()
    print("✅ Country router working.")
