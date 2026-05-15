"""
Polling-based FedEx tracking.

Every 15 minutes, this script:
  1. Reads shipping log Sheet for rows with status 'label_created'
  2. Calls FedEx Track API for each tracking number
  3. If 'PU' event seen → fulfill Shopify order + update Sheet status to 'fulfilled'
  4. If 'DL' event seen → update Sheet status to 'delivered'

Reuses every other module:
  - shipping_log.py for Sheet read/write
  - shopify_fulfill.py for fulfillment
  - fedex_track_auth.py for Track API token

Run manually:    python poll_tracking.py
Schedule:        Railway cron / cron / GitHub Actions every 15 min
"""

import os
import sys
import logging
import requests
from dotenv import load_dotenv
from fedex_track_auth import get_track_token, BASE_URL
from shipping_log import find_packed_unfulfilled, update_status
from shopify_fulfill import fulfill_order
from googleapiclient.discovery import build
from google.oauth2.credentials import Credentials

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
)
log = logging.getLogger("poll_tracking")

SHEET_ID = os.getenv("SHIPPING_LOG_SHEET_ID")
SCOPES = [
    "https://www.googleapis.com/auth/drive.file",
    "https://www.googleapis.com/auth/spreadsheets",
]


def get_open_shipments():
    """Return list of dicts for rows where status is still 'label_created'."""
    creds   = Credentials.from_authorized_user_file("google-token.json", SCOPES)
    service = build("sheets", "v4", credentials=creds, cache_discovery=False)
    result  = service.spreadsheets().values().get(
        spreadsheetId=SHEET_ID,
        range="Sheet1!A:I",
    ).execute()
    rows = result.get("values", [])

    open_rows = []
    for idx, row in enumerate(rows[1:], start=2):
        if len(row) < 5:
            continue
        status = row[4]
        if status == "label_created":
            open_rows.append({
                "row_index": idx,
                "order":     row[0],
                "tracking":  row[1],
                "country":   row[2] if len(row) > 2 else "",
            })
    return open_rows


def fetch_tracking_events(tracking_number):
    """
    Call FedEx Track API for one tracking number.
    Returns a list of event dicts (or [] if none / lookup failed).
    """
    token = get_track_token()["token"]
    url = f"{BASE_URL}/track/v1/trackingnumbers"

    payload = {
        "trackingInfo": [{
            "trackingNumberInfo": {"trackingNumber": tracking_number},
        }],
        "includeDetailedScans": True,
    }
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type":  "application/json",
        "X-locale":      "en_GB",
    }

    response = requests.post(url, headers=headers, json=payload, timeout=20)
    if response.status_code != 200:
        log.warning(f"   Track API non-200 ({response.status_code}): {response.text[:200]}")
        return []

    data = response.json()
    # Drill into the nested structure: output → completeTrackResults → trackResults → scanEvents
    try:
        results = data["output"]["completeTrackResults"][0]["trackResults"][0]
        return results.get("scanEvents", []) or []
    except (KeyError, IndexError):
        return []


def process_one(shipment):
    """For one open shipment, fetch events + act on them."""
    tracking = shipment["tracking"]
    order    = shipment["order"]

    if tracking.startswith("TEST_"):
        log.info(f"   Skipping test tracking {tracking}")
        return

    log.info(f"📦 Checking {order} / {tracking}...")
    events = fetch_tracking_events(tracking)
    if not events:
        log.info(f"   No events yet")
        return

    # Look for PU or DL in the event list (most recent first usually)
    has_pu = any(e.get("eventType") == "PU" for e in events)
    has_dl = any(e.get("eventType") == "DL" for e in events)
    latest = events[0] if events else {}
    latest_desc = latest.get("eventDescription", "")
    latest_loc  = (latest.get("scanLocation") or {}).get("city", "")

    if has_dl:
        log.info(f"   �� Delivered — updating status")
        update_status(shipment["row_index"], status="delivered",
                      last_event=f"Delivered — {latest_loc}")
    elif has_pu:
        log.info(f"   🚚 Picked up — auto-fulfilling Shopify")
        try:
            fulfill_order(order, tracking)
            update_status(shipment["row_index"], status="fulfilled",
                          last_event=f"Picked up — {latest_loc}")
            log.info(f"   ✅ Fulfilled + Sheet updated")
        except Exception as e:
            msg = str(e)
            if "no OPEN fulfillment" in msg or "already" in msg.lower():
                # Order was fulfilled outside our system (manually or by an earlier run).
                # Still update Sheet so we don't keep retrying every 15 min.
                update_status(shipment["row_index"], status="fulfilled",
                              last_event=f"Picked up — {latest_loc} (was already fulfilled)")
                log.info(f"   ℹ️  Already fulfilled elsewhere — Sheet synced")
            else:
                log.error(f"   ❌ Fulfill failed: {e}")
    else:
        # Still in transit / pre-pickup — just update the last_event field
        log.info(f"   ⏳ Latest: {latest_desc} ({latest_loc})")
        update_status(shipment["row_index"],
                      last_event=f"{latest_desc} — {latest_loc}")


def main():
    log.info("🔍 Polling FedEx for tracking updates...")
    log.info(f"   Sheet: {SHEET_ID}")
    print()

    shipments = get_open_shipments()
    log.info(f"   Found {len(shipments)} open shipment(s) to check")

    if not shipments:
        log.info("Nothing to poll. Done.")
        return

    for s in shipments:
        try:
            process_one(s)
        except Exception as e:
            log.error(f"   Error on {s['tracking']}: {e}")

    print()
    log.info("✅ Poll complete.")


if __name__ == "__main__":
    main()
