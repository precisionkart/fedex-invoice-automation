"""
Shopify Fulfillment API helper.

Given an order name (e.g. '04065-SHP') + a tracking number,
marks the order as fulfilled in Shopify. This automatically
triggers the customer "Your order has shipped" email with
the tracking link.

We use the modern Fulfillment Orders API (2024+) because the
old Fulfillment API was deprecated.
"""

import os
import sys
import requests
from dotenv import load_dotenv
from generate_invoice import get_access_token, GRAPHQL_URL

load_dotenv()


def gql(token, query, variables=None):
    """Run a GraphQL query against Shopify Admin API."""
    response = requests.post(
        GRAPHQL_URL,
        headers={
            "X-Shopify-Access-Token": token,
            "Content-Type": "application/json",
        },
        json={"query": query, "variables": variables or {}},
        timeout=20,
    )
    response.raise_for_status()
    payload = response.json()
    if "errors" in payload:
        raise RuntimeError(f"GraphQL errors: {payload['errors']}")
    return payload["data"]


def find_order_by_name(token, order_name):
    """
    Find a Shopify order by its name (e.g. '04065-SHP'),
    return its ID and unfulfilled fulfillment orders.
    """
    # Shopify search syntax wants 'name:#04065-SHP'
    name_query = order_name if order_name.startswith("#") else f"#{order_name}"
    query = """
    query($q: String!) {
      orders(first: 1, query: $q) {
        edges {
          node {
            id
            name
            fulfillmentOrders(first: 10) {
              edges {
                node {
                  id
                  status
                  lineItems(first: 50) {
                    edges {
                      node {
                        id
                        remainingQuantity
                      }
                    }
                  }
                }
              }
            }
          }
        }
      }
    }
    """
    data = gql(token, query, {"q": f"name:{name_query}"})
    edges = data["orders"]["edges"]
    if not edges:
        raise RuntimeError(f"No order found matching name {order_name}")
    return edges[0]["node"]


def create_fulfillment(token, fulfillment_order_id, line_items, tracking_number, tracking_company="FedEx"):
    """
    Create a fulfillment against an open fulfillment order.
    This marks the order as shipped + triggers customer notification email.
    """
    mutation = """
    mutation fulfill($fulfillment: FulfillmentInput!) {
      fulfillmentCreate(fulfillment: $fulfillment) {
        fulfillment {
          id
          status
          trackingInfo { number company url }
        }
        userErrors { field message }
      }
    }
    """
    variables = {
        "fulfillment": {
            "lineItemsByFulfillmentOrder": [{
                "fulfillmentOrderId": fulfillment_order_id,
                "fulfillmentOrderLineItems": line_items,
            }],
            "trackingInfo": {
                "number":  tracking_number,
                "company": tracking_company,
            },
            "notifyCustomer": True,
        }
    }
    data = gql(token, mutation, variables)
    result = data["fulfillmentCreate"]
    if result["userErrors"]:
        raise RuntimeError(f"Fulfillment errors: {result['userErrors']}")
    return result["fulfillment"]


def fulfill_order(order_name, tracking_number):
    """
    High-level: given an order name and tracking number, mark it fulfilled.
    Returns the fulfillment details on success.
    """
    token = get_access_token()
    order = find_order_by_name(token, order_name)
    order_id = order["id"]
    name = order["name"]

    # Find the first OPEN fulfillment order with remaining line items
    fos = [edge["node"] for edge in order["fulfillmentOrders"]["edges"]]
    open_fos = [fo for fo in fos if fo["status"] == "OPEN"]
    if not open_fos:
        raise RuntimeError(f"Order {name} has no OPEN fulfillment orders (maybe already fulfilled?)")

    fo = open_fos[0]
    line_items_to_fulfill = [
        {"id": edge["node"]["id"], "quantity": edge["node"]["remainingQuantity"]}
        for edge in fo["lineItems"]["edges"]
        if edge["node"]["remainingQuantity"] > 0
    ]
    if not line_items_to_fulfill:
        raise RuntimeError(f"Order {name} has no remaining items to fulfill")

    fulfillment = create_fulfillment(
        token, fo["id"], line_items_to_fulfill, tracking_number
    )
    return {
        "order_name":   name,
        "order_id":     order_id,
        "fulfillment":  fulfillment,
    }


if __name__ == "__main__":
    if len(sys.argv) != 3:
        print("Usage: python shopify_fulfill.py <order_name> <tracking_number>")
        print("Example: python shopify_fulfill.py 04065-SHP 794817369207")
        sys.exit(1)

    order_name = sys.argv[1]
    tracking   = sys.argv[2]

    print(f"📦 Fulfilling Shopify order {order_name} with tracking {tracking}...")
    print()

    try:
        result = fulfill_order(order_name, tracking)
    except Exception as e:
        print(f"❌ Failed: {e}")
        sys.exit(1)

    print(f"✅ Order {result['order_name']} marked fulfilled")
    print(f"   Fulfillment ID: {result['fulfillment']['id']}")
    print(f"   Status:         {result['fulfillment']['status']}")
    print(f"   Customer will receive 'Your order has shipped' email with FedEx tracking link")
