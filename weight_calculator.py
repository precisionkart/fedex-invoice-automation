"""
Calculate total weight (and dimensions) for a list of Shopify products + quantities.

Usage:
    Edit the ITEMS list below with SKU + quantity, then:
    python weight_calculator.py
"""

import os
from dotenv import load_dotenv
from generate_invoice import get_access_token, graphql

load_dotenv()


# ============================================================
# Edit this list with your SKUs + quantities
# ============================================================
ITEMS = [
    ("PK-CHA-THCK-104",       10),
    ("PK-CHA-THCK-106",       10),
    ("PK-CHA-THCK-108",        5),
    ("PK-CHA-THCK-110",        5),
    ("PK-BRA-SBP-F257",       10),
    ("PK-BRA-CFB-F257",        1),
    ("PK-SPR-7075T-755",      10),
    ("PK-SPR-7075T-765",      10),
    ("PK-SPR-7075T-775",      10),
    ("PK-SPR-7075T-815",      10),
    ("PK-SPR-7075T-825",      10),
    ("PK-SPR-7075T-77",       10),
    ("PK-SPR-7075T-82",       10),
    ("PK-A11",                 4),
    ("PK-A4",                200),
    ("PK-LIQUID-CHAIN",        4),
    ("PK-A18",                 8),
]


QUERY = """
query findVariant($sku: String!) {
  productVariants(first: 1, query: $sku) {
    edges {
      node {
        title
        sku
        product { title }
        inventoryItem {
          measurement { weight { value unit } }
        }
        metafields(first: 20, namespace: "custom") {
          edges { node { key value } }
        }
      }
    }
  }
}
"""


def kg(value, unit):
    """Convert weight to kg."""
    if not value:
        return 0
    v = float(value)
    if unit == "GRAMS":   return v / 1000.0
    if unit == "POUNDS":  return v * 0.453592
    if unit == "OUNCES":  return v * 0.0283495
    return v  # kg


def find_variant(token, sku):
    """Find a variant by SKU."""
    data = graphql(token, QUERY, {"sku": f"sku:{sku}"})
    edges = data["productVariants"]["edges"]
    if not edges:
        return None
    return edges[0]["node"]


def main():
    token = get_access_token()
    print(f"📦 Calculating weight for {len(ITEMS)} item(s)...\n")
    print(f"{'SKU':<24} {'Qty':>4}  {'Unit kg':>8}  {'Total kg':>9}  Title")
    print("-" * 90)

    total_kg = 0
    not_found = []

    for sku, qty in ITEMS:
        variant = find_variant(token, sku)
        if not variant:
            not_found.append(sku)
            print(f"{sku:<24} {qty:>4}  {'—':>8}  {'—':>9}  ❌ NOT FOUND")
            continue

        weight_info = (variant.get("inventoryItem") or {}).get("measurement", {}).get("weight") or {}
        unit_kg = kg(weight_info.get("value"), weight_info.get("unit"))
        line_kg = unit_kg * qty
        total_kg += line_kg

        title = (variant.get("product", {}).get("title") or "")[:35]
        print(f"{sku:<24} {qty:>4}  {unit_kg:>8.3f}  {line_kg:>9.3f}  {title}")

    print("-" * 90)
    print(f"{'TOTAL':<24} {sum(q for _, q in ITEMS):>4}  {' ':>8}  {total_kg:>9.3f} kg\n")
    print(f"Total weight: {total_kg:.2f} kg ({total_kg * 2.20462:.2f} lbs)")

    if not_found:
        print(f"\n⚠️  SKUs not found in Shopify:")
        for s in not_found:
            print(f"   - {s}")


if __name__ == "__main__":
    main()
