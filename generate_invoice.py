"""
End-to-end invoice generator.

Pulls a real Shopify order by name, reads variant metafields,
applies FX conversion to shipping, and writes a customs-ready PDF.

Usage:
    python generate_invoice.py 04058-SHP
    python generate_invoice.py            (uses DEFAULT_ORDER below)
"""

import os
import sys
import re
import requests
from datetime import datetime, timezone
from dotenv import load_dotenv
from drive_upload import upload_invoice

from reportlab.lib.pagesizes import A4
from reportlab.lib import colors
from reportlab.lib.units import mm
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.enums import TA_RIGHT, TA_CENTER
from reportlab.platypus import (
    SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle
)

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
load_dotenv()

STORE_DOMAIN  = os.getenv("SHOPIFY_STORE_DOMAIN")
CLIENT_ID     = os.getenv("SHOPIFY_CLIENT_ID")
CLIENT_SECRET = os.getenv("SHOPIFY_CLIENT_SECRET")

API_VERSION = "2026-01"
GRAPHQL_URL = f"https://{STORE_DOMAIN}/admin/api/{API_VERSION}/graphql.json"

DEFAULT_ORDER = "04058-SHP"

SHIPPER = {
    "name": "Precision Kart",
    "tagline": "Unit 2 Pacemanor, Bellbrook Ind Estate · East Sussex, Uckfield · United Kingdom",
    "vat": "500090847",
    "eori": "GB042777994000",
    "address_lines": [
        "Unit 2 Pacemanor, Bellbrook Ind Estate",
        "East Sussex, Uckfield",
        "TN22 1YA",
        "United Kingdom",
    ],
}

GBP_SYMBOL = "£"

# Visual palette
INK     = colors.HexColor("#111827")
MUTED   = colors.HexColor("#6B7280")
RULE    = colors.HexColor("#E5E7EB")
ROW_ALT = colors.HexColor("#F9FAFB")
WARN    = colors.HexColor("#B45309")

S = {
    "company":  ParagraphStyle("company",  fontName="Helvetica-Bold", fontSize=12, textColor=INK,   leading=14),
    "tagline":  ParagraphStyle("tagline",  fontName="Helvetica",      fontSize=8,  textColor=MUTED, leading=10),
    "vat":      ParagraphStyle("vat",      fontName="Helvetica",      fontSize=8,  textColor=MUTED, leading=10),
    "h1":       ParagraphStyle("h1",       fontName="Helvetica-Bold", fontSize=16, textColor=INK,   leading=18, alignment=TA_RIGHT),
    "h1sub":    ParagraphStyle("h1sub",    fontName="Helvetica",      fontSize=8,  textColor=MUTED, leading=10, alignment=TA_RIGHT),
    "label":    ParagraphStyle("label",    fontName="Helvetica-Bold", fontSize=7,  textColor=MUTED, leading=9,  spaceAfter=2),
    "value":    ParagraphStyle("value",    fontName="Helvetica",      fontSize=9,  textColor=INK,   leading=11),
    "value_b":  ParagraphStyle("value_b",  fontName="Helvetica-Bold", fontSize=9,  textColor=INK,   leading=11),
    "th":       ParagraphStyle("th",       fontName="Helvetica-Bold", fontSize=7.5,textColor=MUTED, leading=9),
    "td":       ParagraphStyle("td",       fontName="Helvetica",      fontSize=8.5,textColor=INK,   leading=10),
    "td_r":     ParagraphStyle("td_r",     fontName="Helvetica",      fontSize=8.5,textColor=INK,   leading=10, alignment=TA_RIGHT),
    "tot_lbl":  ParagraphStyle("tot_lbl",  fontName="Helvetica",      fontSize=9,  textColor=MUTED, leading=11, alignment=TA_RIGHT),
    "tot_val":  ParagraphStyle("tot_val",  fontName="Helvetica",      fontSize=9,  textColor=INK,   leading=11, alignment=TA_RIGHT),
    "grand_l":  ParagraphStyle("grand_l",  fontName="Helvetica-Bold", fontSize=10, textColor=INK,   leading=12, alignment=TA_RIGHT),
    "grand_v":  ParagraphStyle("grand_v",  fontName="Helvetica-Bold", fontSize=11, textColor=INK,   leading=13, alignment=TA_RIGHT),
    "decl":     ParagraphStyle("decl",     fontName="Helvetica",      fontSize=7.5,textColor=INK,   leading=10),
    "decl_lbl": ParagraphStyle("decl_lbl", fontName="Helvetica-Bold", fontSize=7,  textColor=MUTED, leading=9, spaceAfter=2),
    "decl_val": ParagraphStyle("decl_val", fontName="Helvetica",      fontSize=8,  textColor=INK,   leading=10),
    "fx_note":  ParagraphStyle("fx_note",  fontName="Helvetica",      fontSize=6.5,textColor=MUTED, leading=8, alignment=TA_CENTER),
    "footer":   ParagraphStyle("footer",   fontName="Helvetica-Oblique", fontSize=7, textColor=MUTED, leading=9, alignment=TA_CENTER),
}


# ---------------------------------------------------------------------------
# Shopify API
# ---------------------------------------------------------------------------
def get_access_token():
    url = f"https://{STORE_DOMAIN}/admin/oauth/access_token"
    response = requests.post(url,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        data={
            "client_id": CLIENT_ID,
            "client_secret": CLIENT_SECRET,
            "grant_type": "client_credentials",
        }, timeout=15)
    response.raise_for_status()
    return response.json()["access_token"]


def graphql(token, query, variables=None):
    response = requests.post(GRAPHQL_URL,
        headers={"X-Shopify-Access-Token": token,
                 "Content-Type": "application/json"},
        json={"query": query, "variables": variables or {}},
        timeout=20)
    response.raise_for_status()
    payload = response.json()
    if "errors" in payload:
        raise RuntimeError(f"GraphQL errors: {payload['errors']}")
    return payload["data"]


ORDER_QUERY = """
query getOrderByName($q: String!) {
  orders(first: 1, query: $q) {
    edges {
      node {
        name
        createdAt
        currencyCode
        totalPriceSet { shopMoney { amount currencyCode } }
        shippingLine {
          title
          originalPriceSet { shopMoney { amount currencyCode } }
        }
        shippingAddress {
          name address1 address2 city zip
          provinceCode country countryCodeV2 phone
        }
        lineItems(first: 50) {
          edges {
            node {
              title quantity sku variantTitle
              variant {
                id
                title
                inventoryItem {
                  measurement {
                    weight { value unit }
                  }
                }
                metafields(first: 20, namespace: "custom") {
                  edges { node { key value } }
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


def fetch_order(token, order_name):
    """Fetch one order by its name (e.g. '04058-SHP' or '#04058-SHP')."""
    # Shopify expects the name without leading '#' in its search syntax
    clean = order_name.lstrip("#").strip()
    data = graphql(token, ORDER_QUERY, {"q": f"name:{clean}"})
    edges = data["orders"]["edges"]
    if not edges:
        raise RuntimeError(f"Order '{order_name}' not found.")
    return edges[0]["node"]


# ---------------------------------------------------------------------------
# FX
# ---------------------------------------------------------------------------
def get_fx_rate(from_currency, to_currency="GBP"):
    if from_currency == to_currency:
        return 1.0
    url = f"https://api.frankfurter.dev/v1/latest?base={from_currency}&symbols={to_currency}"
    r = requests.get(url, timeout=10)
    r.raise_for_status()
    rate = r.json().get("rates", {}).get(to_currency)
    if rate is None:
        raise RuntimeError(f"No FX rate for {from_currency}→{to_currency}")
    return rate


# ---------------------------------------------------------------------------
# Data transformation
# ---------------------------------------------------------------------------
def normalise_country_code(value):
    """
    Country-of-origin metafield safety net.
    Some products have 'GBP' (currency) set by mistake — treat as 'GB'.
    """
    if not value:
        return ("XX", "missing")
    v = value.strip().upper()
    if len(v) == 2:
        return (v, "ok")
    if v == "GBP":
        return ("GB", "fixed_from_GBP")
    if len(v) == 3 and v.endswith("R") is False:
        # Other 3-letter codes are unexpected — flag but use first 2 chars
        return (v[:2], "truncated")
    return (v[:2], "unknown")


def kg(weight_value, weight_unit):
    """Normalise any weight unit to kilograms."""
    if weight_value is None:
        return 0.0
    unit = (weight_unit or "GRAMS").upper()
    if unit in ("KILOGRAMS", "KG"):
        return float(weight_value)
    if unit in ("GRAMS", "G"):
        return float(weight_value) / 1000.0
    if unit in ("POUNDS", "LB"):
        return float(weight_value) * 0.453592
    if unit in ("OUNCES", "OZ"):
        return float(weight_value) * 0.0283495
    return float(weight_value)


def build_line_items(order):
    """
    Convert Shopify line items into invoice rows.
    Aggregates identical variants (same SKU). Pulls metafields and weight.
    """
    aggregated = {}
    warnings = []

    for edge in order["lineItems"]["edges"]:
        li = edge["node"]
        variant = li.get("variant") or {}
        sku = li.get("sku") or ""
        qty = li.get("quantity") or 0

        # Pull metafields
        mfs = {mfe["node"]["key"]: mfe["node"]["value"]
               for mfe in variant.get("metafields", {}).get("edges", [])}

        description = mfs.get("fedex_product_title") or li.get("title") or "(no description)"
        hs_code     = mfs.get("fedex_hs_code") or ""
        origin_raw  = mfs.get("fedex_country_of_origin") or ""
        unit_price  = mfs.get("fedex_shipping_cost")

        origin, origin_status = normalise_country_code(origin_raw)

        # Track data issues for warnings
        if not hs_code:
            warnings.append(f"  • {sku}: missing custom.fedex_hs_code")
        if origin_status == "fixed_from_GBP":
            warnings.append(f"  • {sku}: country_of_origin was 'GBP' — using 'GB'. Please fix in Shopify.")
        if origin_status in ("missing", "unknown", "truncated"):
            warnings.append(f"  • {sku}: country_of_origin '{origin_raw}' — using '{origin}'.")
        if unit_price is None:
            warnings.append(f"  • {sku}: missing custom.fedex_shipping_cost (unit price)")
            unit_price = 0
        try:
            unit_price = float(unit_price)
        except (TypeError, ValueError):
            warnings.append(f"  • {sku}: invalid unit price '{unit_price}' — using 0")
            unit_price = 0

        # Weight from built-in field
        meas = (variant.get("inventoryItem") or {}).get("measurement") or {}
        w = meas.get("weight") or {}
        unit_w = kg(w.get("value"), w.get("unit"))
        if unit_w == 0:
            warnings.append(f"  • {sku}: variant weight not set in Shopify")

        # Aggregate by (sku, description, hs_code, origin)
        key = (sku, description, hs_code, origin)
        if key in aggregated:
            aggregated[key]["qty"] += qty
        else:
            aggregated[key] = {
                "description": description,
                "sku": sku,
                "hs_code": hs_code,
                "origin": origin,
                "qty": qty,
                "unit_weight_kg": unit_w,
                "unit_price_gbp": unit_price,
            }

    return list(aggregated.values()), warnings


# ---------------------------------------------------------------------------
# PDF rendering
# ---------------------------------------------------------------------------
def money(amount):
    return f"{GBP_SYMBOL}\u00a0{amount:,.2f}"


def render_pdf(invoice, output_path):
    doc = SimpleDocTemplate(output_path, pagesize=A4,
        leftMargin=18*mm, rightMargin=18*mm, topMargin=16*mm, bottomMargin=14*mm,
        title=f"Commercial Invoice {invoice['invoice_no']}")
    story = []

    # Header
    company_block = [
        Paragraph(SHIPPER["name"], S["company"]),
        Paragraph(SHIPPER["tagline"], S["tagline"]),
        Spacer(1, 2),
        Paragraph(f"VAT: {SHIPPER['vat']} · EORI: {SHIPPER['eori']}", S["vat"]),
    ]
    invoice_block = [
        Paragraph("Commercial Invoice", S["h1"]),
        Paragraph("For customs purposes only", S["h1sub"]),
    ]
    header = Table([[company_block, invoice_block]], colWidths=[105*mm, 69*mm])
    header.setStyle(TableStyle([("VALIGN",(0,0),(-1,-1),"TOP"),
        ("LEFTPADDING",(0,0),(-1,-1),0),("RIGHTPADDING",(0,0),(-1,-1),0),
        ("TOPPADDING",(0,0),(-1,-1),0),("BOTTOMPADDING",(0,0),(-1,-1),0)]))
    story.append(header)
    story.append(Spacer(1, 6))

    rule = Table([[""]], colWidths=[174*mm], rowHeights=[0.6])
    rule.setStyle(TableStyle([("LINEABOVE",(0,0),(-1,-1),0.6,RULE)]))
    story.append(rule)
    story.append(Spacer(1, 8))

    meta = Table([[
        [Paragraph("INVOICE NO.", S["label"]),
         Paragraph(f"#{invoice['invoice_no']}", S["value_b"]),
         Paragraph(f"Order: {invoice['order_no']}", S["value"])],
        [Paragraph("DATE", S["label"]),
         Paragraph(invoice["date"], S["value"])],
        [Paragraph("", S["label"])],
    ]], colWidths=[58*mm, 58*mm, 58*mm])
    meta.setStyle(TableStyle([("VALIGN",(0,0),(-1,-1),"TOP"),
        ("LEFTPADDING",(0,0),(-1,-1),0),("RIGHTPADDING",(0,0),(-1,-1),0),
        ("TOPPADDING",(0,0),(-1,-1),0),("BOTTOMPADDING",(0,0),(-1,-1),0)]))
    story.append(meta)
    story.append(Spacer(1, 10))

    # Shipper / Consignee
    shipper_cell = [Paragraph("SHIPPER", S["label"]), Paragraph(SHIPPER["name"], S["value_b"])]
    for ln in SHIPPER["address_lines"]:
        shipper_cell.append(Paragraph(ln, S["value"]))
    consignee = invoice["consignee"]
    consignee_cell = [Paragraph("CONSIGNEE", S["label"]), Paragraph(consignee["name"], S["value_b"])]
    for ln in consignee["address_lines"]:
        consignee_cell.append(Paragraph(ln, S["value"]))
    if consignee.get("phone"):
        consignee_cell.append(Paragraph(consignee["phone"], S["value"]))

    addr = Table([[shipper_cell, consignee_cell]], colWidths=[87*mm, 87*mm])
    addr.setStyle(TableStyle([("VALIGN",(0,0),(-1,-1),"TOP"),
        ("BOX",(0,0),(0,0),0.4,RULE),("BOX",(1,0),(1,0),0.4,RULE),
        ("LEFTPADDING",(0,0),(-1,-1),8),("RIGHTPADDING",(0,0),(-1,-1),8),
        ("TOPPADDING",(0,0),(-1,-1),8),("BOTTOMPADDING",(0,0),(-1,-1),8)]))
    story.append(addr)
    story.append(Spacer(1, 10))

    # Line items
    headers = ["Description", "HS Code", "Origin", "Qty",
               "Unit wt.", "Total wt.", f"Unit ({GBP_SYMBOL})", f"Total ({GBP_SYMBOL})"]
    rows = [[Paragraph(h, S["th"]) for h in headers]]
    subtotal = 0.0
    total_weight = 0.0
    for li in invoice["line_items"]:
        line_total = li["qty"] * li["unit_price_gbp"]
        line_weight = li["qty"] * li["unit_weight_kg"]
        subtotal += line_total
        total_weight += line_weight
        rows.append([
            Paragraph(li["description"], S["td"]),
            Paragraph(li["hs_code"], S["td"]),
            Paragraph(li["origin"], S["td"]),
            Paragraph(str(li["qty"]), S["td_r"]),
            Paragraph(f"{li['unit_weight_kg']:.2f} kg", S["td_r"]),
            Paragraph(f"{line_weight:.2f} kg", S["td_r"]),
            Paragraph(f"{li['unit_price_gbp']:,.2f}", S["td_r"]),
            Paragraph(f"{line_total:,.2f}", S["td_r"]),
        ])

    line_tbl = Table(rows, colWidths=[52*mm, 22*mm, 14*mm, 12*mm, 18*mm, 20*mm, 18*mm, 18*mm])
    line_tbl.setStyle(TableStyle([("VALIGN",(0,0),(-1,-1),"MIDDLE"),
        ("LINEBELOW",(0,0),(-1,0),0.6,INK),("LINEBELOW",(0,1),(-1,-1),0.3,RULE),
        ("TOPPADDING",(0,0),(-1,-1),6),("BOTTOMPADDING",(0,0),(-1,-1),6),
        ("LEFTPADDING",(0,0),(-1,-1),4),("RIGHTPADDING",(0,0),(-1,-1),4),
        ("ROWBACKGROUNDS",(0,1),(-1,-1),[colors.white,ROW_ALT])]))
    story.append(line_tbl)
    story.append(Spacer(1, 6))

    shipping_gbp = invoice["shipping_gbp"]
    grand_total = subtotal + shipping_gbp
    totals = Table([
        [Paragraph("Subtotal", S["tot_lbl"]), Paragraph(money(subtotal), S["tot_val"])],
        [Paragraph("Shipping (FedEx)", S["tot_lbl"]), Paragraph(money(shipping_gbp), S["tot_val"])],
        [Paragraph("Total weight", S["tot_lbl"]), Paragraph(f"{total_weight:.2f} kg", S["tot_val"])],
        [Paragraph("Total", S["grand_l"]), Paragraph(money(grand_total), S["grand_v"])],
    ], colWidths=[40*mm, 35*mm], hAlign="RIGHT")
    totals.setStyle(TableStyle([("LINEABOVE",(0,3),(-1,3),0.6,INK),
        ("TOPPADDING",(0,0),(-1,-1),3),("BOTTOMPADDING",(0,0),(-1,-1),3),
        ("TOPPADDING",(0,3),(-1,3),6)]))
    story.append(totals)
    story.append(Spacer(1, 14))

    decl_block = [
        Paragraph("REASON FOR EXPORT", S["decl_lbl"]),
        Paragraph(invoice["reason_for_export"], S["decl_val"]),
        Spacer(1, 8),
        Paragraph("INCOTERMS", S["decl_lbl"]),
        Paragraph(invoice["incoterms"], S["decl_val"]),
        Spacer(1, 8),
        Paragraph("DECLARATION", S["decl_lbl"]),
        Paragraph("I declare that the information on this invoice is true and correct, and that the contents and value are as stated above.", S["decl"]),
    ]
    decl_tbl = Table([[decl_block]], colWidths=[174*mm])
    decl_tbl.setStyle(TableStyle([("VALIGN",(0,0),(-1,-1),"TOP"),
        ("LEFTPADDING",(0,0),(-1,-1),0),("RIGHTPADDING",(0,0),(-1,-1),0),
        ("TOPPADDING",(0,0),(-1,-1),0),("BOTTOMPADDING",(0,0),(-1,-1),0)]))
    story.append(decl_tbl)
    story.append(Spacer(1, 14))

    if invoice.get("fx_note"):
        story.append(Paragraph(invoice["fx_note"], S["fx_note"]))
        story.append(Spacer(1, 4))

    story.append(Paragraph(
        f"Generated automatically from Shopify order {invoice['order_no']} on {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}.",
        S["footer"],
    ))

    doc.build(story)


# ---------------------------------------------------------------------------
# Build invoice dict from Shopify order
# ---------------------------------------------------------------------------
def build_invoice_from_order(order):
    line_items, warnings = build_line_items(order)

    # Shipping FX
    sl = order.get("shippingLine") or {}
    sp = (sl.get("originalPriceSet") or {}).get("shopMoney") or {}
    shipping_amt = float(sp.get("amount") or 0)
    shipping_ccy = sp.get("currencyCode") or order.get("currencyCode") or "GBP"

    fx_rate = get_fx_rate(shipping_ccy, "GBP")
    shipping_gbp = round(shipping_amt * fx_rate, 2)
    fx_note = None
    if shipping_ccy != "GBP":
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        fx_note = (f"Shipping converted from {shipping_ccy} {shipping_amt:.2f} "
                   f"at 1 {shipping_ccy} = {fx_rate:.4f} GBP (ECB rate, retrieved {today}).")

    # Consignee
    addr = order.get("shippingAddress") or {}
    cons_lines = [addr.get("address1", "")]
    if addr.get("address2"):
        cons_lines.append(addr["address2"])
    cons_lines.append(f"{addr.get('zip', '')} {addr.get('city', '')}".strip())
    cons_lines.append(addr.get("country") or "")
    cons_lines = [ln for ln in cons_lines if ln.strip()]

    order_no_clean = order["name"].lstrip("#")
    created = order["createdAt"]
    date_str = datetime.fromisoformat(created.replace("Z", "+00:00")).strftime("%d %b %Y")

    return {
        "invoice_no": f"SH-{order_no_clean}",
        "order_no": order["name"],
        "date": date_str,
        "consignee": {
            "name": addr.get("name", ""),
            "address_lines": cons_lines,
            "phone": addr.get("phone", ""),
        },
        "line_items": line_items,
        "shipping_gbp": shipping_gbp,
        "fx_note": fx_note,
        "reason_for_export": "Sale of goods",
        "incoterms": "DAP - Delivered At Place",
    }, warnings


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def main():
    order_name = sys.argv[1] if len(sys.argv) > 1 else DEFAULT_ORDER
    print(f"🔌 Connecting to {STORE_DOMAIN}...")
    token = get_access_token()
    print(f"✅ Authenticated.")
    print(f"📥 Fetching order {order_name}...")
    order = fetch_order(token, order_name)
    print(f"✅ Got order {order['name']} ({order['currencyCode']})")
    print(f"🔧 Building invoice data...")
    invoice, warnings = build_invoice_from_order(order)

    if warnings:
        print()
        print("⚠️  Data warnings (these don't block invoice generation):")
        for w in warnings:
            print(w)
        print()

    safe_name = re.sub(r"[^A-Za-z0-9_-]", "_", order_name.lstrip("#"))
    output_path = f"Invoice_{safe_name}.pdf"
    render_pdf(invoice, output_path)
    print(f"✅ Invoice written to: {output_path}")
    print()
    print("Summary:")
    print(f"  Invoice #:        {invoice['invoice_no']}")
    print(f"  Date:             {invoice['date']}")
    print(f"  Consignee:        {invoice['consignee']['name']} ({(order.get('shippingAddress') or {}).get('countryCodeV2')})")
    print(f"  Line items:       {len(invoice['line_items'])}")
    print(f"  Shipping (GBP):   £{invoice['shipping_gbp']:.2f}")



    # --- Upload to Google Drive ---
    folder_id = os.getenv("GOOGLE_DRIVE_FOLDER_ID")
    if folder_id:
        print(f"☁️  Uploading to Google Drive...")
        try:
            from datetime import datetime
            order_dt = datetime.fromisoformat(order["createdAt"].replace("Z", "+00:00"))
            result = upload_invoice(output_path, folder_id, order_date=order_dt)
            print(f"   ✅ Uploaded to /FedEx Invoices/{result['folder']}/{result['name']}")
            print(f"   🔗 {result['link']}")
        except Exception as e:
            print(f"   ⚠️  Upload failed: {e}")
            print(f"   PDF is still on disk at {output_path}")
    else:
        print(f"ℹ️  GOOGLE_DRIVE_FOLDER_ID not set in .env — skipping Drive upload.")


if __name__ == "__main__":
    main()
