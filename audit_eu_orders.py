"""
audit_eu_orders.py — focused audit of EU / non-UK shipments, going back
further than the other audits.

Question it answers: "are EU orders silently dropping?" For every paid order
in the recent window whose destination is NOT GB, it reports exactly what
happened — tags, shipping log row, tracking, invoice, fulfilment, routing —
and a one-word verdict (OK / in-flight / DROPPED / stuck / manual-review).

100% READ-ONLY: posts no notes, ships nothing.

    python audit_eu_orders.py                      # last 200 paid orders
    python audit_eu_orders.py --limit 500          # go back further
    python audit_eu_orders.py --since 2026-04-01   # everything since a date
"""

import re
import sys
import argparse
import logging
from datetime import datetime, timezone

import requests
from dotenv import load_dotenv

from generate_invoice import get_access_token, GRAPHQL_URL
from country_router import classify_destination, MANUAL_REVIEW_COUNTRIES
import shipping_log
import drive_upload

load_dotenv()
logging.basicConfig(level=logging.WARNING)
log = logging.getLogger("audit_eu")

PROCESSING_TAG = "fedex-processing"
SHIPPED_TAG = "fedex-shipped"

PAGE_QUERY = """
query($cursor: String) {
  orders(first: 50, after: $cursor, query: "financial_status:paid",
         reverse: true, sortKey: CREATED_AT) {
    pageInfo { hasNextPage endCursor }
    edges {
      node {
        name
        createdAt
        tags
        displayFulfillmentStatus
        shippingAddress { countryCodeV2 }
      }
    }
  }
}
"""


def _now():
    return datetime.now(timezone.utc)


def _parse_dt(iso):
    if not iso:
        return None
    try:
        return datetime.fromisoformat(iso.replace("Z", "+00:00")).astimezone(timezone.utc)
    except ValueError:
        return None


def _norm(name):
    return str(name or "").lstrip("#").strip().upper()


def fetch_orders(limit, since_dt):
    """Paginate paid orders newest-first.

    Stops when we hit `limit` orders (if no --since) or when orders are older
    than `since_dt` (if --since given). Prints progress every 25 orders.
    """
    token = get_access_token()
    headers = {"X-Shopify-Access-Token": token, "Content-Type": "application/json"}
    collected = []
    cursor = None
    while True:
        r = requests.post(GRAPHQL_URL, headers=headers,
                          json={"query": PAGE_QUERY, "variables": {"cursor": cursor}},
                          timeout=30)
        r.raise_for_status()
        data = r.json()
        if "errors" in data:
            raise RuntimeError(f"Shopify GraphQL errors: {data['errors']}")
        conn = data["data"]["orders"]
        for e in conn["edges"]:
            node = e["node"]
            if since_dt is not None:
                created = _parse_dt(node.get("createdAt"))
                if created is not None and created < since_dt:
                    return collected
            collected.append(node)
            if len(collected) % 25 == 0:
                print(f"  ...fetched {len(collected)} orders", file=sys.stderr)
            if since_dt is None and len(collected) >= limit:
                return collected
        if not conn["pageInfo"]["hasNextPage"]:
            return collected
        cursor = conn["pageInfo"]["endCursor"]


def load_shipping_log():
    """Index FedEx Shipping Log by order name -> tracking (same Sheet as audit_ddu_status.py)."""
    if not shipping_log.SHEET_ID:
        raise RuntimeError("Missing SHIPPING_LOG_SHEET_ID in .env")
    svc = shipping_log._get_service()
    res = svc.spreadsheets().values().get(
        spreadsheetId=shipping_log.SHEET_ID,
        range=f"{shipping_log.SHEET_NAME}!A:C",
    ).execute()
    index = {}
    for row in res.get("values", [])[1:]:
        if not row or not row[0]:
            continue
        key = _norm(row[0])
        tracking = (row[1] if len(row) > 1 else "").strip()
        if key not in index or (tracking and not index[key]):
            index[key] = tracking
    return index


_inv_cache = {}
def has_invoice(drive, name):
    """True if Invoice_{name}.pdf exists in Drive (any year/month subfolder)."""
    safe = re.sub(r"[^A-Za-z0-9_-]", "_", str(name).lstrip("#"))
    fn = f"Invoice_{safe}.pdf"
    if fn not in _inv_cache:
        rr = drive.files().list(q=f"name = '{fn}' and trashed = false",
                                fields="files(id)", pageSize=1).execute()
        _inv_cache[fn] = bool(rr.get("files"))
    return _inv_cache[fn]


def routing_label(country):
    """Map classify_destination to eu_us / manual_review / unknown."""
    action = classify_destination(country)["action"]
    if action == "ship":
        return "eu_us"
    if country in MANUAL_REVIEW_COUNTRIES:
        return "manual_review"
    return "unknown"  # routed to manual review but not in the known list


def tags_compact(tags):
    parts = []
    if PROCESSING_TAG in tags:
        parts.append("P")
    if SHIPPED_TAG in tags:
        parts.append("S")
    if any("manual" in str(t).lower() for t in tags):
        parts.append("M")
    return "".join(parts) if parts else "-"


def verdict_for(routing, tags, has_log, has_track, created):
    """Return (emoji_verdict, key) for one order."""
    has_proc = PROCESSING_TAG in tags
    age = _now() - created if created else None
    age_h = age.total_seconds() / 3600 if age else None
    age_d = age.days if age else None

    if routing in ("manual_review", "unknown"):
        return "🔧 manual review", "manual"

    # routable EU/US from here
    if has_track:
        return "✅ OK", "ok"
    if has_proc:
        if age_h is not None and age_h > 24:
            return "⚠️ stuck", "stuck"
        return "⏳ in flight", "inflight"
    # no tracking, no processing tag
    if not has_log and (age_d is not None and age_d > 2):
        return "❌ DROPPED", "dropped"
    return "⏳ in flight", "inflight"


def main():
    ap = argparse.ArgumentParser(description="Audit EU/non-UK orders for silent drops.")
    ap.add_argument("--limit", type=int, default=200, help="Recent paid orders to scan (default 200).")
    ap.add_argument("--since", type=str, default=None, help="Scan orders since YYYY-MM-DD (overrides --limit).")
    args = ap.parse_args()

    since_dt = None
    if args.since:
        try:
            since_dt = datetime.strptime(args.since, "%Y-%m-%d").replace(tzinfo=timezone.utc)
        except ValueError:
            print(f"Invalid --since date: {args.since!r} (expected YYYY-MM-DD)")
            sys.exit(2)

    print("Loading shipping log...", file=sys.stderr)
    ship_index = load_shipping_log()
    drive = drive_upload._get_service()

    scope = f"since {args.since}" if since_dt else f"last {args.limit}"
    print(f"Fetching paid orders ({scope})...", file=sys.stderr)
    orders = fetch_orders(args.limit, since_dt)

    rows = []
    counts = {"ok": 0, "inflight": 0, "dropped": 0, "stuck": 0, "manual": 0}
    backfill = []

    for o in orders:
        name = o.get("name", "?")
        try:
            country = (o.get("shippingAddress") or {}).get("countryCodeV2") or "?"
            if country == "GB":
                continue

            created = _parse_dt(o.get("createdAt"))
            created_str = created.strftime("%Y-%m-%d") if created else "?"
            tags = o.get("tags") or []
            ff = o.get("displayFulfillmentStatus", "?")
            ff_compact = {"FULFILLED": "Y", "UNFULFILLED": "N",
                          "PARTIALLY_FULFILLED": "PARTIAL"}.get(ff, ff)

            routing = routing_label(country)
            key = _norm(name)
            has_log = key in ship_index
            has_track = bool(ship_index.get(key))
            has_inv = has_invoice(drive, name)

            verdict, vkey = verdict_for(routing, tags, has_log, has_track, created)
            counts[vkey] += 1
            if vkey in ("dropped", "stuck"):
                backfill.append(name)

            rows.append({
                "name": name, "country": country, "created": created_str,
                "created_dt": created or datetime.min.replace(tzinfo=timezone.utc),
                "routing": routing, "tags": tags_compact(tags),
                "log": "✓" if has_log else "✗",
                "track": "✓" if has_track else "✗",
                "inv": "✓" if has_inv else "✗",
                "ff": ff_compact, "verdict": verdict,
            })
        except Exception as e:
            log.warning("error on %s: %s", name, e)
            rows.append({
                "name": name, "country": "?", "created": "?",
                "created_dt": datetime.min.replace(tzinfo=timezone.utc),
                "routing": "?", "tags": "?", "log": "?", "track": "?",
                "inv": "?", "ff": "?", "verdict": f"⚠️ error: {e}",
            })

    rows.sort(key=lambda r: r["created_dt"], reverse=True)

    # ---- table ----
    hdr = (f"{'Order':<13} {'Ctry':<4} {'Created':<11} {'Routing':<13} "
           f"{'Tags':<5} {'Log':<3} {'Trk':<3} {'Inv':<3} {'Ful':<7} Verdict")
    print()
    print(hdr)
    print("-" * len(hdr))
    for r in rows:
        print(f"{r['name']:<13} {r['country']:<4} {r['created']:<11} {r['routing']:<13} "
              f"{r['tags']:<5} {r['log']:<3} {r['track']:<3} {r['inv']:<3} "
              f"{r['ff']:<7} {r['verdict']}")

    # ---- summary ----
    print()
    print("=" * 70)
    print(f"Non-GB orders found: {len(rows)}   "
          f"({counts['ok']} OK, {counts['inflight']} in-flight, "
          f"{counts['dropped']} DROPPED, {counts['stuck']} stuck, "
          f"{counts['manual']} manual review)")
    print("=" * 70)

    if backfill:
        print()
        print("DROPPED / stuck orders — backfill with:")
        for n in backfill:
            print(f"  python ship_real_order.py {n.lstrip('#')}")


if __name__ == "__main__":
    main()
