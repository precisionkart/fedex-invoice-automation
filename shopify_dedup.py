"""
Shopify order tag-based deduplication.
Uses a Shopify ORDER TAG as a shared, persistent dedup marker so duplicate
orders/paid webhooks don't create duplicate FedEx labels.
  - 'fedex-processing'  set the moment we start handling the order
  - 'fedex-shipped'     set once the label is successfully created
"""

import logging
from generate_invoice import get_access_token, GRAPHQL_URL
import requests

log = logging.getLogger(__name__)

PROCESSING_TAG = "fedex-processing"
SHIPPED_TAG = "fedex-shipped"

GET_ORDER_TAGS_QUERY = """
query getOrderTags($query: String!) {
  orders(first: 1, query: $query) {
    edges { node { id tags } }
  }
}
"""

ADD_TAGS_MUTATION = """
mutation tagsAdd($id: ID!, $tags: [String!]!) {
  tagsAdd(id: $id, tags: $tags) {
    node { id }
    userErrors { field message }
  }
}
"""

REMOVE_TAGS_MUTATION = """
mutation tagsRemove($id: ID!, $tags: [String!]!) {
  tagsRemove(id: $id, tags: $tags) {
    node { id }
    userErrors { field message }
  }
}
"""


def _shopify_request(query, variables=None):
    token = get_access_token()
    headers = {"X-Shopify-Access-Token": token, "Content-Type": "application/json"}
    payload = {"query": query, "variables": variables or {}}
    r = requests.post(GRAPHQL_URL, json=payload, headers=headers, timeout=20)
    r.raise_for_status()
    data = r.json()
    if "errors" in data:
        raise RuntimeError(f"Shopify GraphQL errors: {data['errors']}")
    return data["data"]


def _normalize(order_name):
    if not order_name.startswith("#"):
        order_name = f"#{order_name}"
    return order_name


def get_order_id_and_tags(order_name):
    order_name = _normalize(order_name)
    data = _shopify_request(GET_ORDER_TAGS_QUERY, {"query": f"name:{order_name}"})
    edges = data.get("orders", {}).get("edges", [])
    if not edges:
        return None, []
    node = edges[0]["node"]
    return node["id"], node.get("tags", []) or []


def claim_order(order_name):
    """Return True if WE claimed it (proceed to ship), False if already claimed (SKIP)."""
    order_id, tags = get_order_id_and_tags(order_name)
    if order_id is None:
        log.warning(f"DEDUP: order {order_name} not found when claiming")
        return True
    if PROCESSING_TAG in tags or SHIPPED_TAG in tags:
        log.info(f"DEDUP: {order_name} already tagged ({tags}) - SKIPPING duplicate")
        return False
    result = _shopify_request(ADD_TAGS_MUTATION, {"id": order_id, "tags": [PROCESSING_TAG]})
    errors = result.get("tagsAdd", {}).get("userErrors", [])
    if errors:
        log.error(f"DEDUP: failed to tag {order_name}: {errors}")
        return False
    log.info(f"DEDUP: claimed {order_name} with '{PROCESSING_TAG}'")
    return True


def mark_shipped(order_name):
    order_id, tags = get_order_id_and_tags(order_name)
    if order_id is None:
        log.warning(f"DEDUP: order {order_name} not found when marking shipped")
        return
    _shopify_request(ADD_TAGS_MUTATION, {"id": order_id, "tags": [SHIPPED_TAG]})
    if PROCESSING_TAG in tags:
        _shopify_request(REMOVE_TAGS_MUTATION, {"id": order_id, "tags": [PROCESSING_TAG]})
    log.info(f"DEDUP: {order_name} marked '{SHIPPED_TAG}'")


def release_claim(order_name):
    order_id, tags = get_order_id_and_tags(order_name)
    if order_id is None:
        return
    if PROCESSING_TAG in tags:
        _shopify_request(REMOVE_TAGS_MUTATION, {"id": order_id, "tags": [PROCESSING_TAG]})
        log.info(f"DEDUP: released claim on {order_name} after failure")


if __name__ == "__main__":
    import sys
    order = sys.argv[1] if len(sys.argv) > 1 else "04188-SHP"
    print(f"Order: {order}")
    oid, tags = get_order_id_and_tags(order)
    print(f"  id:   {oid}")
    print(f"  tags: {tags}")
