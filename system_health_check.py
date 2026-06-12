"""
system_health_check.py — comprehensive READ-ONLY diagnostic for the FedEx
automation pipeline.

Runs eight groups of checks and prints a clean report:

  1. Environment & credentials      5. Recent order processing health
  2. External service auth          6. Webhook registration
  3. Production deployment          7. Box config sanity
  4. DDU/DDP verification           8. Recent failures

100% read-only: no labels, no notes, no FedEx ship calls, no writes to
Shopify / Drive / Sheets / webhooks. It only reads. Secrets and tokens are
never printed — only present/missing/valid/invalid.

    python system_health_check.py            # normal report
    python system_health_check.py --verbose  # show tracebacks / raw errors
"""

import os
import sys
import argparse
import logging
import warnings
import traceback

# Silence library noise (incl. python-dotenv's own "could not parse line N"
# warning — we detect and report that ourselves in check_env()).
logging.basicConfig(level=logging.ERROR)
logging.getLogger("dotenv").setLevel(logging.CRITICAL)
logging.getLogger("dotenv.main").setLevel(logging.CRITICAL)
warnings.filterwarnings("ignore")

import requests
from dotenv import load_dotenv

load_dotenv()

RAILWAY_BASE = "https://web-production-bc61b.up.railway.app"
WEBHOOK_TOPIC = "ORDERS_PAID"

OK, WARN, FAIL, INFO = "PASS", "WARN", "FAIL", "INFO"
_ICON = {OK: "✅", WARN: "⚠️ ", FAIL: "❌", INFO: "ℹ️ "}

VERBOSE = False

# Collected results for the final summary.
_results = []   # status in {PASS, WARN, FAIL}
_actions = []   # human-readable "ACTION REQUIRED" lines


def line(status, text, detail="", action=None):
    """Print one check line and record it for the summary."""
    if status in (OK, WARN, FAIL):
        _results.append(status)
    msg = f"  {_ICON[status]} {text}"
    if detail:
        msg += f" — {detail}"
    print(msg)
    if action:
        _actions.append(action)


def info(text):
    print(f"  {_ICON[INFO]} {text}")


def header(title):
    print()
    print("=" * 80)
    print(title.upper())
    print("=" * 80)


def err_detail(e):
    """Short error string normally; full traceback in verbose mode."""
    if VERBOSE:
        return "\n" + "".join(traceback.format_exception(type(e), e, e.__traceback__))
    # include response body for HTTP errors if available
    body = ""
    resp = getattr(e, "response", None)
    if resp is not None:
        try:
            body = f" | {resp.status_code}: {resp.text[:200]}"
        except Exception:
            pass
    return f"{e}{body}"


# ─────────────────────────────────────────────────────────────────────────
# 1. Environment & credentials
# ─────────────────────────────────────────────────────────────────────────
def check_env():
    header("1. Environment & credentials")

    env_path = ".env"
    if not os.path.exists(env_path):
        line(FAIL, ".env file readable", "no .env file found",
             action="Create a .env file with the required credentials")
    else:
        line(OK, ".env file readable")
        # Detect lines python-dotenv can't parse (the recurring
        # "could not parse statement starting at line N" warning).
        bad = []
        try:
            with open(env_path, "r") as f:
                for i, raw in enumerate(f, start=1):
                    s = raw.strip()
                    if not s or s.startswith("#") or s.startswith("export "):
                        continue
                    key = s.split("=", 1)[0].strip() if "=" in s else s
                    if "=" not in s or " " in key or not key.replace("_", "").isalnum():
                        bad.append((i, s[:60]))
        except Exception as e:
            line(FAIL, ".env parse scan", err_detail(e))
            bad = None
        if bad:
            for n, txt in bad:
                line(WARN, f".env line {n} unparseable",
                     f"{txt!r} — not a KEY=VALUE assignment (stray line / missing quotes)",
                     action=f"Remove or fix .env line {n}: {txt!r}")
        elif bad is not None:
            line(OK, ".env parses cleanly")

    required = [
        "SHOPIFY_STORE_DOMAIN", "SHOPIFY_CLIENT_ID", "SHOPIFY_CLIENT_SECRET",
        "FEDEX_ACCOUNT_NUMBER", "FEDEX_CLIENT_ID", "FEDEX_CLIENT_SECRET",
    ]
    for k in required:
        if (os.getenv(k) or "").strip():
            line(OK, f"{k} present")
        else:
            line(FAIL, f"{k} MISSING", action=f"Set {k} in .env")

    # Track creds are optional-but-expected; surface presence (don't fail hard here)
    for k in ("FEDEX_TRACK_CLIENT_ID", "FEDEX_TRACK_CLIENT_SECRET"):
        if (os.getenv(k) or "").strip():
            line(OK, f"{k} present")
        else:
            line(WARN, f"{k} not set", "Track API auth will be skipped")

    dry = os.getenv("FEDEX_DRY_RUN")
    if dry is None:
        info("FEDEX_DRY_RUN = unset (treated as false — automation WILL create labels)")
    elif dry.lower() == "true":
        info("FEDEX_DRY_RUN = true (production currently PAUSED — no labels created)")
    else:
        info(f"FEDEX_DRY_RUN = {dry} (automation LIVE — labels will be created)")


# ─────────────────────────────────────────────────────────────────────────
# 2. External service auth
# ─────────────────────────────────────────────────────────────────────────
def check_auth():
    header("2. External service auth")

    # Shopify token + trivial query
    try:
        from generate_invoice import get_access_token, GRAPHQL_URL
        token = get_access_token()
        r = requests.post(
            GRAPHQL_URL,
            headers={"X-Shopify-Access-Token": token, "Content-Type": "application/json"},
            json={"query": "{ shop { name } }"}, timeout=20,
        )
        r.raise_for_status()
        shop = (((r.json() or {}).get("data") or {}).get("shop") or {}).get("name")
        if shop:
            line(OK, "Shopify auth + GraphQL", f"shop: {shop}")
        else:
            line(FAIL, "Shopify auth + GraphQL", f"no shop name returned: {r.text[:120]}",
                 action="Check SHOPIFY_CLIENT_ID/SECRET and app scopes")
    except Exception as e:
        line(FAIL, "Shopify auth + GraphQL", err_detail(e),
             action="Fix Shopify credentials / token exchange")

    # FedEx Ship OAuth
    try:
        from fedex_auth import get_fedex_token
        tok = get_fedex_token()
        if tok.get("token"):
            line(OK, "FedEx Ship OAuth", f"valid token (scope: {tok.get('scope', '?')})")
        else:
            line(FAIL, "FedEx Ship OAuth", "no token in response",
                 action="Check FEDEX_CLIENT_ID/SECRET")
    except Exception as e:
        line(FAIL, "FedEx Ship OAuth", err_detail(e),
             action="Fix FedEx Ship API credentials")

    # FedEx Track OAuth (separate creds — known to be 403ing)
    if (os.getenv("FEDEX_TRACK_CLIENT_ID") or "").strip():
        try:
            from fedex_track_auth import get_track_token
            tok = get_track_token()
            if tok.get("token"):
                line(OK, "FedEx Track OAuth", f"valid token (scope: {tok.get('scope', '?')})")
            else:
                line(FAIL, "FedEx Track OAuth", "no token in response")
        except Exception as e:
            line(FAIL, "FedEx Track OAuth", err_detail(e),
                 action="Fix FedEx Tracking OAuth 403 (regenerate Track project creds at developer.fedex.com)")
    else:
        line(WARN, "FedEx Track OAuth", "skipped — FEDEX_TRACK_CLIENT_ID not set")

    # Google Drive — list 1 file from invoices folder
    try:
        import drive_upload
        service = drive_upload._get_service()
        folder = os.getenv("DRIVE_FOLDER_INVOICES") or os.getenv("GOOGLE_DRIVE_FOLDER_ID")
        q = f"'{folder}' in parents and trashed = false" if folder else "trashed = false"
        res = service.files().list(q=q, fields="files(id,name)", pageSize=1).execute()
        n = len(res.get("files", []))
        line(OK, "Google Drive access", f"listed {n} file(s) from {'invoices folder' if folder else 'Drive'}")
    except Exception as e:
        line(FAIL, "Google Drive access", err_detail(e),
             action="Re-auth Google Drive (refresh google-token.json)")

    # Google Sheets — read shipping log row count
    try:
        import shipping_log
        if not shipping_log.SHEET_ID:
            line(FAIL, "Google Sheets (shipping log)", "SHIPPING_LOG_SHEET_ID not set",
                 action="Set SHIPPING_LOG_SHEET_ID in .env")
        else:
            svc = shipping_log._get_service()
            res = svc.spreadsheets().values().get(
                spreadsheetId=shipping_log.SHEET_ID,
                range=f"{shipping_log.SHEET_NAME}!A:A",
            ).execute()
            rows = res.get("values", [])
            line(OK, "Google Sheets (shipping log)", f"{max(len(rows) - 1, 0)} shipment rows")
    except Exception as e:
        line(FAIL, "Google Sheets (shipping log)", err_detail(e),
             action="Re-auth Google Sheets / check SHIPPING_LOG_SHEET_ID")


# ─────────────────────────────────────────────────────────────────────────
# 3. Production deployment
# ─────────────────────────────────────────────────────────────────────────
def check_deployment():
    header("3. Production deployment")
    info(f"Railway URL: {RAILWAY_BASE}")
    last = None
    for path in ("/health", "/"):
        try:
            r = requests.get(RAILWAY_BASE + path, timeout=15)
            line(OK, f"server reachable ({path})", f"HTTP {r.status_code} — server is up")
            return
        except requests.exceptions.RequestException as e:
            last = e
    line(FAIL, "server reachable", err_detail(last),
         action="Check Railway deployment is running")


# ─────────────────────────────────────────────────────────────────────────
# 4. DDU/DDP verification (build body only — no FedEx call)
# ─────────────────────────────────────────────────────────────────────────
def check_ddu(order_name="04353-SHP"):
    header("4. DDU/DDP verification")
    info(f"Building FedEx ship body for {order_name} (no FedEx call)")
    try:
        from generate_invoice import fetch_order, get_access_token
        from ship_real_order import extract_items, build_fedex_recipient, SHIPPER
        from box_chooser import choose_package
        from fedex_ship import build_ship_request

        token = get_access_token()
        order = fetch_order(token, order_name)
        if not order:
            line(FAIL, f"build ship body for {order_name}", "order not found in Shopify")
            return
        items = extract_items(order)
        pkg = choose_package(items)
        if pkg.get("manual_review"):
            line(WARN, f"build ship body for {order_name}",
                 f"order routes to manual review ({pkg.get('reason')}) — can't build body")
            return

        shipment = {
            "shipper": SHIPPER,
            "recipient": build_fedex_recipient(order),
            "order_name": order_name,
            "service_type": "FEDEX_REGIONAL_ECONOMY",  # placeholder; not sent anywhere
            "package": {
                "weight_kg": pkg["weight_kg"], "length_cm": pkg["length_cm"],
                "width_cm": pkg["width_cm"], "height_cm": pkg["height_cm"],
                "currency": "GBP", "declared_value": 50.0,
            },
            "line_items": items,
        }
        body = build_ship_request(shipment, os.getenv("FEDEX_ACCOUNT_NUMBER") or "TEST")
        rs = body["requestedShipment"]

        duties = (rs.get("customsClearanceDetail", {}).get("dutiesPayment", {})
                  .get("paymentType"))
        if duties == "RECIPIENT":
            line(OK, "dutiesPayment = RECIPIENT (DDU)", "customer pays duties & taxes ✓")
        else:
            line(FAIL, "dutiesPayment", f"🚩 = {duties!r} — expected RECIPIENT (DDU)!",
                 action=f"🚩 DDP REGRESSION: dutiesPayment is {duties!r}, we'd be paying duties again — fix fedex_ship.py")

        shipping = rs.get("shippingChargesPayment", {}).get("paymentType")
        if shipping == "SENDER":
            line(OK, "shippingChargesPayment = SENDER", "we pay shipping ✓")
        else:
            line(FAIL, "shippingChargesPayment", f"= {shipping!r} — expected SENDER",
                 action="shippingChargesPayment should be SENDER — check fedex_ship.py")
    except Exception as e:
        line(FAIL, "DDU/DDP body verification", err_detail(e))


# ─────────────────────────────────────────────────────────────────────────
# 5. Recent order processing health  (+ collects data for 8)
# ─────────────────────────────────────────────────────────────────────────
def check_processing(limit=30):
    header("5. Recent order processing health")
    manual_or_stuck = []  # for section 8: (name, country, tags, reason)
    try:
        from generate_invoice import get_access_token, GRAPHQL_URL
        from country_router import classify_destination
        import shipping_log, drive_upload, re

        # shipping log index
        svc = shipping_log._get_service()
        res = svc.spreadsheets().values().get(
            spreadsheetId=shipping_log.SHEET_ID,
            range=f"{shipping_log.SHEET_NAME}!A:C",
        ).execute()
        ship_index = {}
        for row in res.get("values", [])[1:]:
            if not row or not row[0]:
                continue
            key = row[0].lstrip("#").strip().upper()
            tracking = (row[1] if len(row) > 1 else "").strip()
            if key not in ship_index or (tracking and not ship_index[key]):
                ship_index[key] = tracking

        drive = drive_upload._get_service()
        _cache = {}
        def has_invoice(name):
            safe = re.sub(r"[^A-Za-z0-9_-]", "_", name.lstrip("#"))
            fn = f"Invoice_{safe}.pdf"
            if fn not in _cache:
                rr = drive.files().list(q=f"name = '{fn}' and trashed = false",
                                        fields="files(id)", pageSize=1).execute()
                _cache[fn] = bool(rr.get("files"))
            return _cache[fn]

        token = get_access_token()
        query = """
        query($first:Int!){ orders(first:$first, query:"financial_status:paid", reverse:true, sortKey:CREATED_AT){
          edges{ node{ name tags displayFulfillmentStatus shippingAddress{ countryCodeV2 } } } } }
        """
        r = requests.post(GRAPHQL_URL,
            headers={"X-Shopify-Access-Token": token, "Content-Type": "application/json"},
            json={"query": query, "variables": {"first": limit}}, timeout=30)
        r.raise_for_status()
        orders = [e["node"] for e in r.json()["data"]["orders"]["edges"]]

        ok = stuck = not_processed = skipped = 0
        problems = []
        for o in orders:
            name = o.get("name", "?")
            try:
                country = (o.get("shippingAddress") or {}).get("countryCodeV2") or "?"
                tags = o.get("tags") or []
                ff = o.get("displayFulfillmentStatus", "?")
                action = classify_destination(country)["action"]
                stuck_tag = "fedex-processing" in tags and "fedex-shipped" not in tags

                if action in ("skip", "manual_review"):
                    skipped += 1
                    if stuck_tag:
                        manual_or_stuck.append((name, country, tags, "stuck in fedex-processing"))
                    elif action == "manual_review":
                        manual_or_stuck.append((name, country, tags, "manual-review country"))
                    continue

                missing = []
                key = name.lstrip("#").strip().upper()
                if key not in ship_index:
                    missing.append("no shipping log row")
                elif not ship_index[key]:
                    missing.append("no tracking")
                if not has_invoice(name):
                    missing.append("no invoice on Drive")
                if stuck_tag:
                    missing.append("stuck in fedex-processing")
                if ff not in ("FULFILLED", "PARTIALLY_FULFILLED"):
                    missing.append("unfulfilled")

                if not missing:
                    ok += 1
                elif stuck_tag:
                    stuck += 1
                    problems.append((name, country, missing))
                    manual_or_stuck.append((name, country, tags, "stuck in fedex-processing"))
                else:
                    not_processed += 1
                    problems.append((name, country, missing))
            except Exception as e:
                not_processed += 1
                problems.append((name, "?", [f"error: {e}"]))

        status = OK if not problems else WARN
        line(status, f"last {len(orders)} paid orders",
             f"{ok} OK, {stuck} stuck, {not_processed} not processed, {skipped} skipped (GB/manual)")
        for name, country, missing in problems:
            print(f"      ❌ {name} ({country}): {', '.join(missing)}")
            if "no invoice on Drive" in missing or "no shipping log row" in missing:
                _actions.append(f"Process missed order {name} ({country}): {', '.join(missing)}")
    except Exception as e:
        line(FAIL, "recent order processing health", err_detail(e))

    # 8. Recent failures
    header("8. Recent failures (manual review / stuck)")
    if manual_or_stuck:
        line(WARN, "manual-review / stuck orders", f"{len(manual_or_stuck)} flagged")
        for name, country, tags, reason in manual_or_stuck:
            tagstr = ", ".join(tags) if tags else "(no tags)"
            print(f"      ⚠️  {name} ({country}) — {reason}  [tags: {tagstr}]")
    else:
        line(OK, "manual-review / stuck orders", "none in recent window")


# ─────────────────────────────────────────────────────────────────────────
# 6. Webhook registration
# ─────────────────────────────────────────────────────────────────────────
def check_webhooks():
    header("6. Webhook registration")
    try:
        from generate_invoice import get_access_token, GRAPHQL_URL
        token = get_access_token()
        q = """
        { webhookSubscriptions(first: 50) { edges { node {
            topic endpoint { __typename ... on WebhookHttpEndpoint { callbackUrl } } } } } }
        """
        r = requests.post(GRAPHQL_URL,
            headers={"X-Shopify-Access-Token": token, "Content-Type": "application/json"},
            json={"query": q}, timeout=20)
        r.raise_for_status()
        edges = r.json()["data"]["webhookSubscriptions"]["edges"]
        paid = [ (e["node"].get("endpoint") or {}).get("callbackUrl")
                 for e in edges if e["node"]["topic"] == WEBHOOK_TOPIC ]

        if not paid:
            line(FAIL, f"{WEBHOOK_TOPIC} webhook registered", "no ORDERS_PAID webhook found",
                 action="Register the orders/paid webhook (python register_webhook.py)")
        else:
            on_railway = [u for u in paid if u and RAILWAY_BASE in u]
            if on_railway:
                line(OK, f"{WEBHOOK_TOPIC} → Railway", on_railway[0])
            else:
                line(FAIL, f"{WEBHOOK_TOPIC} → Railway", f"points elsewhere: {paid}",
                     action=f"Webhook points to wrong URL {paid} — re-register to {RAILWAY_BASE}")
    except Exception as e:
        line(FAIL, "webhook registration", err_detail(e))


# ─────────────────────────────────────────────────────────────────────────
# 7. Box config sanity
# ─────────────────────────────────────────────────────────────────────────
def check_boxes():
    header("7. Box config sanity")
    try:
        from box_chooser import PACKAGES
        print("  Configured packages (name — L×W×H cm, tare g, type):")
        for name, L, W, H, tare, is_env in PACKAGES:
            print(f"      - {name:18s} {L}×{W}×{H}cm  {tare}g  {'envelope' if is_env else 'box'}")
        m = next((p for p in PACKAGES if p[0] == "3XL Shoebox"), None)
        if m and (m[1], m[2], m[3]) == (42, 27, 15):
            line(OK, "3XL Shoebox = 42×27×15", "matches applied fix")
        elif m:
            line(FAIL, "3XL Shoebox dimensions", f"got {m[1]}×{m[2]}×{m[3]}, expected 42×27×15",
                 action="Restore 3XL Shoebox to 42×27×15 in box_chooser.py")
        else:
            line(FAIL, "3XL Shoebox present", "not found in PACKAGES")
    except Exception as e:
        line(FAIL, "box config", err_detail(e))


# ─────────────────────────────────────────────────────────────────────────
def main():
    global VERBOSE
    ap = argparse.ArgumentParser(description="Read-only health check for the FedEx automation pipeline.")
    ap.add_argument("--verbose", action="store_true", help="Show tracebacks / raw API errors on failures.")
    args = ap.parse_args()
    VERBOSE = args.verbose

    print("FedEx Automation — System Health Check")
    print("Read-only diagnostic. Nothing is written to Shopify, Drive, Sheets, FedEx, or webhooks.")

    for fn in (check_env, check_auth, check_deployment, check_ddu,
               check_processing, check_webhooks, check_boxes):
        try:
            fn()
        except Exception as e:  # belt-and-braces: a check should never kill the run
            line(FAIL, f"{fn.__name__} crashed", err_detail(e))

    passed = _results.count(OK)
    warned = _results.count(WARN)
    failed = _results.count(FAIL)

    header("System Health Summary")
    print(f"  ✅ {passed} checks passed")
    print(f"  ⚠️  {warned} warnings")
    print(f"  ❌ {failed} failures")

    if _actions:
        print()
        print("ACTION REQUIRED:")
        for a in _actions:
            print(f"  - {a}")

    dry = (os.getenv("FEDEX_DRY_RUN") or "").lower()
    if dry == "true":
        print()
        print("CURRENTLY PAUSED:")
        print("  FEDEX_DRY_RUN=true — automation will NOT create labels until set to false.")

    print()
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
