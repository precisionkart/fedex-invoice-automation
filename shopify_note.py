"""
Shopify order note appender.

Adds a tracking note to a Shopify order without marking it fulfilled.
Pickers see the note in the order timeline, then manually mark fulfilled
after physically shipping.
"""

import logging
from generate_invoice import get_access_token, GRAPHQL_URL
import requests

log = logging.getLogger(__name__)

GET_ORDER_QUERY = """
query getOrder($query: String!) {
  orders(first: 1, query: $query) {
    edges {
      node {
        id
        note
      }
    }
  }
}
"""

UPDATE_NOTE_MUTATION = """
mutation orderUpdate($input: OrderInput!) {
  orderUpdate(input: $input) {
    order { id note }
    userErrors { field message }
  }
}
"""


def _shopify_request(query, variables=None):
    token = get_access_token()
    headers = {
        "X-Shopify-Access-Token": token,
        "Content-Type": "application/json",
    }
    payload = {"query": query, "variables": variables or {}}
    r = requests.post(GRAPHQL_URL, json=payload, headers=headers, timeout=20)
    r.raise_for_status()
    data = r.json()
    if "errors" in data:
        raise RuntimeError(f"Shopify GraphQL errors: {data['errors']}")
    return data["data"]


def add_order_note(order_name, note_text):
    """
    Append note_text to the order's note field.
    Preserves any existing note content.
    """
    if not order_name.startswith("#"):
        order_name = f"#{order_name}"

    # 1. Fetch order to get id + existing note
    data = _shopify_request(GET_ORDER_QUERY, {"query": f"name:{order_name}"})
    edges = data.get("orders", {}).get("edges", [])
    if not edges:
        raise RuntimeError(f"Order {order_name} not found in Shopify")
    node = edges[0]["node"]
    order_id = node["id"]
    existing_note = node.get("note") or ""

    # 2. Append new note (with separator if there's existing content)
    if existing_note:
        combined = f"{existing_note}\n\n{note_text}"
    else:
        combined = note_text

    # 3. Update order
    result = _shopify_request(
        UPDATE_NOTE_MUTATION,
        {"input": {"id": order_id, "note": combined}},
    )
    errors = result.get("orderUpdate", {}).get("userErrors", [])
    if errors:
        raise RuntimeError(f"orderUpdate errors: {errors}")

    return result["orderUpdate"]["order"]


if __name__ == "__main__":
    # Manual test
    import sys
    order = sys.argv[1] if len(sys.argv) > 1 else "04150-SHP"
    note = sys.argv[2] if len(sys.argv) > 2 else "Test note from shopify_note.py"
    print(f"Adding note to {order}...")
    result = add_order_note(order, note)
    print(f"OK. Updated note:\n{result['note']}")
