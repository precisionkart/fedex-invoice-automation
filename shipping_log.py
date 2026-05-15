"""
Google Sheets shipping log + lookup.

A single sheet 'FedEx Shipping Log' acts as our system of record:
  - One row per shipment
  - Tracks: order #, tracking number, country, status, drive links
  - Status transitions: label_created -> picked_up -> fulfilled
"""

import os
from datetime import datetime
from dotenv import load_dotenv
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build

load_dotenv()

SCOPES = [
    "https://www.googleapis.com/auth/drive.file",
    "https://www.googleapis.com/auth/spreadsheets",
]
TOKEN_FILE = "google-token.json"

SHEET_ID   = os.getenv("SHIPPING_LOG_SHEET_ID")
SHEET_NAME = "Sheet1"

COL_ORDER         = "A"
COL_TRACKING      = "B"
COL_COUNTRY       = "C"
COL_CREATED_AT    = "D"
COL_STATUS        = "E"
COL_LAST_EVENT    = "F"
COL_MARK_PACKED   = "G"
COL_LABEL_LINK    = "H"
COL_INVOICE_LINK  = "I"


def _get_service():
    creds = Credentials.from_authorized_user_file(TOKEN_FILE, SCOPES)
    return build("sheets", "v4", credentials=creds, cache_discovery=False)


def log_shipment(order_name, tracking_number, country,
                 label_drive_link="", invoice_drive_link=""):
    """Append a new row when a shipment is created."""
    if not SHEET_ID:
        raise RuntimeError("Missing SHIPPING_LOG_SHEET_ID in .env")

    service = _get_service()
    now = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S UTC")
    row = [[
        order_name,
        tracking_number,
        country,
        now,
        "label_created",
        "",
        False,
        label_drive_link,
        invoice_drive_link,
    ]]
    return service.spreadsheets().values().append(
        spreadsheetId=SHEET_ID,
        range=f"{SHEET_NAME}!A:I",
        valueInputOption="USER_ENTERED",
        insertDataOption="INSERT_ROWS",
        body={"values": row},
    ).execute()


def find_shipment(tracking_number):
    """Look up a row by tracking number. Returns dict or None."""
    if not SHEET_ID:
        raise RuntimeError("Missing SHIPPING_LOG_SHEET_ID in .env")

    service = _get_service()
    result = service.spreadsheets().values().get(
        spreadsheetId=SHEET_ID,
        range=f"{SHEET_NAME}!A:I",
    ).execute()
    rows = result.get("values", [])

    for idx, row in enumerate(rows[1:], start=2):
        if len(row) > 1 and row[1] == tracking_number:
            return {
                "row_index":  idx,
                "order":      row[0] if len(row) > 0 else "",
                "tracking":   row[1] if len(row) > 1 else "",
                "country":    row[2] if len(row) > 2 else "",
                "created_at": row[3] if len(row) > 3 else "",
                "status":     row[4] if len(row) > 4 else "",
                "last_event": row[5] if len(row) > 5 else "",
                "packed":     row[6] if len(row) > 6 else "",
                "label":      row[7] if len(row) > 7 else "",
                "invoice":    row[8] if len(row) > 8 else "",
            }
    return None


def update_status(row_index, status=None, last_event=None):
    """Update Status (col E) and/or Last event (col F) for a row."""
    if not SHEET_ID:
        raise RuntimeError("Missing SHIPPING_LOG_SHEET_ID in .env")

    service = _get_service()
    data = []
    if status is not None:
        data.append({"range": f"{SHEET_NAME}!{COL_STATUS}{row_index}",
                     "values": [[status]]})
    if last_event is not None:
        data.append({"range": f"{SHEET_NAME}!{COL_LAST_EVENT}{row_index}",
                     "values": [[last_event]]})
    if not data:
        return None

    return service.spreadsheets().values().batchUpdate(
        spreadsheetId=SHEET_ID,
        body={"valueInputOption": "USER_ENTERED", "data": data},
    ).execute()


def find_packed_unfulfilled():
    """Manual override: find rows where 'Mark packed?' is ticked but still label_created."""
    if not SHEET_ID:
        raise RuntimeError("Missing SHIPPING_LOG_SHEET_ID in .env")

    service = _get_service()
    result = service.spreadsheets().values().get(
        spreadsheetId=SHEET_ID,
        range=f"{SHEET_NAME}!A:I",
    ).execute()
    rows = result.get("values", [])

    waiting = []
    for idx, row in enumerate(rows[1:], start=2):
        if len(row) < 7:
            continue
        packed_str = str(row[6]).upper()
        status     = row[4] if len(row) > 4 else ""
        if packed_str in ("TRUE", "YES", "✓") and status == "label_created":
            waiting.append({
                "row_index": idx,
                "order":     row[0],
                "tracking":  row[1],
            })
    return waiting


if __name__ == "__main__":
    print("Testing shipping log helper...")
    print(f"Sheet ID: {SHEET_ID}")
    print()

    print("Logging a test shipment...")
    log_shipment(
        order_name="TEST-001",
        tracking_number="TEST_TRACKING_999",
        country="GB",
        label_drive_link="https://drive.google.com/file/d/test/view",
        invoice_drive_link="https://drive.google.com/file/d/test2/view",
    )
    print("   Row appended")
    print()

    print("Looking up by tracking number...")
    found = find_shipment("TEST_TRACKING_999")
    if found:
        print(f"   Found at row {found['row_index']}: {found['order']} ({found['country']}) status={found['status']}")
    else:
        print("   Not found")
    print()

    if found:
        print("Updating status to picked_up...")
        update_status(found["row_index"], status="picked_up",
                      last_event="Picked up — Uckfield (test)")
        print("   Updated")
    print()

    print("Shipping log helper working.")
