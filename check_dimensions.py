"""
Audit Shopify products for dimension metafields.

Lists every product and shows:
  - Whether length_cm, width_cm, height_cm are set
  - Whether variant weight is set
  - Any anomalies (zero values, weird formatting)

Run: python check_dimensions.py
"""

import os
from dotenv import load_dotenv
from generate_invoice import get_access_token, graphql

load_dotenv()


QUERY = """
{
  products(first: 250) {
    edges {
      node {
        title
        handle
        metafields(first: 20, namespace: "custom") {
          edges { node { key value } }
        }
        variants(first: 10) {
          edges {
            node {
              title
              sku
              inventoryItem {
                measurement { weight { value unit } }
              }
            }
          }
        }
      }
    }
    pageInfo { hasNextPage }
  }
}
"""


def main():
    token = get_access_token()
    data = graphql(token, QUERY)

    products = [e["node"] for e in data["products"]["edges"]]
    print(f"📦 Checking {len(products)} product(s)\n")

    ok_count, missing_count, suspicious_count = 0, 0, 0
    missing = []
    suspicious = []

    for p in products:
        mf = {edge["node"]["key"]: edge["node"]["value"]
              for edge in p.get("metafields", {}).get("edges", [])}
        L = mf.get("length_cm")
        W = mf.get("width_cm")
        H = mf.get("height_cm")

        # Check weight on first variant
        weight_val = None
        variants = p.get("variants", {}).get("edges", [])
        if variants:
            wi = (variants[0].get("node", {}).get("inventoryItem") or {}).get("measurement", {}).get("weight") or {}
            weight_val = wi.get("value")

        title = p.get("title", "")[:55]
        issues = []
        if not L: issues.append("no length")
        if not W: issues.append("no width")
        if not H: issues.append("no height")
        if not weight_val: issues.append("no weight")

        if issues:
            missing_count += 1
            missing.append(f"  ⚠️  {title:55s}  {', '.join(issues)}")
        else:
            # Check for suspicious 0 values
            try:
                lf, wf, hf = float(L), float(W), float(H)
                if lf <= 0 or wf <= 0 or hf <= 0:
                    suspicious_count += 1
                    suspicious.append(f"  🔍 {title:55s}  has zero dimension(s): {L}×{W}×{H}")
                else:
                    ok_count += 1
            except ValueError:
                suspicious_count += 1
                suspicious.append(f"  🔍 {title:55s}  unparseable: {L}, {W}, {H}")

    print(f"✅ {ok_count} products with full dimensions + weight")
    print(f"⚠️  {missing_count} products missing data")
    print(f"🔍 {suspicious_count} products with suspicious values\n")

    if missing:
        print("=" * 80)
        print("MISSING DATA — need to fix")
        print("=" * 80)
        for line in missing:
            print(line)
        print()

    if suspicious:
        print("=" * 80)
        print("SUSPICIOUS VALUES — likely typos")
        print("=" * 80)
        for line in suspicious:
            print(line)
        print()

    if not missing and not suspicious:
        print("🎉 All products have valid dimensions + weight!")


if __name__ == "__main__":
    main()
