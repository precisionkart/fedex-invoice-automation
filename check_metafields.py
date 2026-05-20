"""Quick metafield check for a single SKU - checks both variant and product level."""
import os
from dotenv import load_dotenv
from generate_invoice import get_access_token, graphql

load_dotenv()

SKU = "PK-MXC-RIMS"

QUERY = """
query findVariant($sku: String!) {
  productVariants(first: 1, query: $sku) {
    edges {
      node {
        title
        sku
        product {
          title
          metafields(first: 30, namespace: "custom") {
            edges { node { key value type } }
          }
        }
        inventoryItem {
          measurement { weight { value unit } }
        }
        metafields(first: 30, namespace: "custom") {
          edges { node { key value type } }
        }
      }
    }
  }
}
"""

token = get_access_token()
data = graphql(token, QUERY, {"sku": f"sku:{SKU}"})
edges = data["productVariants"]["edges"]

if not edges:
    print(f"SKU not found: {SKU}")
    exit(1)

v = edges[0]["node"]
print(f"Product: {v['product']['title']}")
print(f"Variant: {v['title']}")
print(f"SKU:     {v['sku']}")

weight = (v.get("inventoryItem") or {}).get("measurement", {}).get("weight") or {}
if weight.get("value"):
    print(f"Weight:  {weight['value']} {weight['unit']}")

print()
print("VARIANT metafields:")
print("-" * 60)
variant_meta = {}
for edge in v["metafields"]["edges"]:
    n = edge["node"]
    variant_meta[n["key"]] = n["value"]
    print(f"   {n['key']:30s} = {n['value']}")

print()
print("PRODUCT metafields:")
print("-" * 60)
product_meta = {}
for edge in v["product"]["metafields"]["edges"]:
    n = edge["node"]
    product_meta[n["key"]] = n["value"]
    print(f"   {n['key']:30s} = {n['value']}")

print()
print("Customs declaration readiness check:")
print("-" * 60)
expected = {
    "fedex_product_title":     "variant",
    "fedex_hs_code":           "variant",
    "fedex_country_of_origin": "variant",
    "fedex_material_type":     "product",
    "fedex_intended_use":      "product",
}
for key, expected_level in expected.items():
    if key in variant_meta:
        print(f"   OK  {key:30s} = {variant_meta[key]:30s} (found in variant)")
    elif key in product_meta:
        print(f"   OK  {key:30s} = {product_meta[key]:30s} (found in product)")
    else:
        print(f"   --  {key:30s} = MISSING")
