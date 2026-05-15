"""
48h no-scan safety alert.

Scans the shipping log for rows that:
  - Still have status 'label_created'
  - Were created more than 48 hours ago

If any are found, sends ONE email listing them all.
If none, exits silently — no email.

Schedule: daily via GitHub Actions.
"""

import os
import sys
import base64
import logging
from datetime import datetime, timedelta, timezone
from email.mime.text import MIMEText
from dotenv import load_dotenv
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
)
log = logging.getLogger("alert_unscanned")

SHEET_ID    = os.getenv("SHIPPING_LOG_SHEET_ID")
ALERT_EMAIL = os.getenv("ALERT_EMAIL")   # where to send the alert
THRESHOLD_HOURS = 48

SCOPES = [
    "https://www.googleapis.com/auth/drive.file",
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/gmail.send",
]


def get_overdue_shipments():
    """Return list of dicts for rows still 'label_created' and older than THRESHOLD_HOURS."""
    creds = Credentials.from_authorized_user_file("google-token.json", SCOPES)
    service = build("sheets", "v4", credentials=creds, cache_discovery=False)
    result  = service.spreadsheets().values().get(
        spreadsheetId=SHEET_ID,
        range="Sheet1!A:I",
    ).execute()
    rows = result.get("values", [])

    cutoff = datetime.now(timezone.utc) - timedelta(hours=THRESHOLD_HOURS)
    overdue = []

    for idx, row in enumerate(rows[1:], start=2):
        if len(row) < 5:
            continue
        status     = row[4] if len(row) > 4 else ""
        created_at = row[3] if len(row) > 3 else ""

        if status != "label_created":
            continue

        # Sheet stores: "2026-05-15 17:17:51 UTC"
        try:
            dt = datetime.strptime(created_at.replace(" UTC", ""), "%Y-%m-%d %H:%M:%S")
            dt = dt.replace(tzinfo=timezone.utc)
        except ValueError:
            log.warning(f"   Can't parse date for row {idx}: '{created_at}'")
            continue

        if dt < cutoff:
            hours_old = int((datetime.now(timezone.utc) - dt).total_seconds() / 3600)
            overdue.append({
                "row":      idx,
                "order":    row[0],
                "tracking": row[1],
                "country":  row[2] if len(row) > 2 else "?",
                "age_h":    hours_old,
            })

    return overdue


def send_alert_email(overdue):
    """Send one email summarising all overdue shipments."""
    if not ALERT_EMAIL:
        log.error("Missing ALERT_EMAIL in env — can't send")
        return False

    creds   = Credentials.from_authorized_user_file("google-token.json", SCOPES)
    service = build("gmail", "v1", credentials=creds, cache_discovery=False)

    # Body
    lines = [
        f"You have {len(overdue)} shipment(s) where a label was created "
        f"more than {THRESHOLD_HOURS} hours ago but FedEx hasn't scanned them yet.",
        "",
        "Possible causes:",
        "  • Label printed but box not actually dropped off",
        "  • Pickup arranged but driver didn't scan",
        "  • Tracking number issue",
        "",
        "Overdue shipments:",
        "",
    ]
    for s in overdue:
        lines.append(
            f"  • {s['order']:15s} {s['tracking']:18s} {s['country']:4s} "
            f"({s['age_h']}h old)"
        )
    lines += [
        "",
        f"Open the shipping log to investigate:",
        f"  https://docs.google.com/spreadsheets/d/{SHEET_ID}/edit",
        "",
        "— Automated alert from your shipping system",
    ]
    body = "\n".join(lines)

    msg = MIMEText(body)
    msg["Subject"] = f"⚠️  {len(overdue)} unscanned shipment(s) — investigate"
    msg["To"]      = ALERT_EMAIL
    raw = base64.urlsafe_b64encode(msg.as_bytes()).decode()

    service.users().messages().send(
        userId="me",
        body={"raw": raw},
    ).execute()

    log.info(f"✅ Alert email sent to {ALERT_EMAIL}")
    return True


def main():
    log.info(f"🔍 Checking for shipments older than {THRESHOLD_HOURS}h still unscanned...")

    overdue = get_overdue_shipments()
    if not overdue:
        log.info("✅ All shipments scanned within threshold — no alert needed")
        return

    log.warning(f"⚠️  Found {len(overdue)} overdue shipment(s)")
    for s in overdue:
        log.warning(f"   {s['order']} / {s['tracking']} — {s['age_h']}h old")

    send_alert_email(overdue)


if __name__ == "__main__":
    main()
