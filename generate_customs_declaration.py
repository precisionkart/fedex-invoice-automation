"""
Generate a US Customs Product Declaration PDF for a Shopify order.

Reads from Shopify variant + product metafields:
  Variant level:
    - fedex_product_title       Product name
    - fedex_hs_code             HS code (auto-padded to 8 digits)
    - fedex_country_of_origin   Country code (auto-corrects GBP -> GB)
    - fedex_shipping_cost       Declared value per unit
    - fedex_description         (optional) Detailed customs description
  Product level (shared across variants):
    - fedex_material_type       Drives composition + Section 232 fields
    - fedex_intended_use        Per-product use case

Hardcoded business details:
    - Manufacturer:  Precision Kart Ltd
    - Address:       Unit 2 Pacemanor, Bellbrook Industrial Estate
    - Signature:     Paul Cooper
    - UK-origin metals throughout

Usage:
    python generate_customs_declaration.py 04077-SHP
"""

import os
import sys
from datetime import datetime
from dotenv import load_dotenv

from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import cm
from reportlab.lib.enums import TA_LEFT
from reportlab.platypus import (
    SimpleDocTemplate, Paragraph, Spacer, KeepTogether,
    Table, TableStyle, HRFlowable,
)
from reportlab.lib.colors import black, HexColor
from reportlab.lib import colors as rl_colors

from generate_invoice import get_access_token, fetch_order

load_dotenv()

# ============================================================
# HARDCODED CONSTANTS
# ============================================================
COMPANY_NAME      = "Precision Kart Ltd"
COMPANY_ADDRESS_1 = "Unit 2 Pacemanor, Bellbrook Industrial Estate"
COMPANY_ADDRESS_2 = "East Sussex, Uckfield, TN22 1YA"
COMPANY_COUNTRY   = "United Kingdom"
COMPANY_EMAIL     = "info@precisionkart.co.uk"
SIGNATURE         = "Paul Cooper"
ORIGIN_COUNTRY    = "United Kingdom"


# ============================================================
# MATERIAL TYPE -> composition + Section 232 fields
# ============================================================
MATERIAL_RULES = {
    "steel": {
        "composition": "100% Steel",
        "section_232_fields": [("Steel Melt/Pour Country", ORIGIN_COUNTRY)],
    },
    "stainless_steel": {
        "composition": "Stainless Steel",
        "section_232_fields": [("Steel Melt/Pour Country", ORIGIN_COUNTRY)],
    },
    "aluminum": {
        "composition": "Aluminum alloy",
        "section_232_fields": [
            ("Aluminum Primary Country of Smelt",   ORIGIN_COUNTRY),
            ("Aluminum Secondary Country of Smelt", ORIGIN_COUNTRY),
            ("Aluminum Country of Cast",            ORIGIN_COUNTRY),
        ],
    },
    "magnesium": {
        "composition": "Magnesium alloy",
        "section_232_fields": [("Magnesium Melt/Cast Country", ORIGIN_COUNTRY)],
    },
    "titanium": {
        "composition": "Titanium alloy",
        "section_232_fields": [("Titanium Melt/Cast Country", ORIGIN_COUNTRY)],
    },
    "nylon": {
        "composition": "Nylon with metal inserts",
        "section_232_fields": [],
    },
    "carbon": {
        "composition": "Carbon fibre composite",
        "section_232_fields": [],
    },
    "plastic": {
        "composition": "Plastic",
        "section_232_fields": [],
    },
    "rubber": {
        "composition": "Rubber / synthetic elastomer",
        "section_232_fields": [],
    },
    "composite": {
        "composition": "Steel backing plate with composite friction material",
        "section_232_fields": [("Steel Melt/Pour Country", ORIGIN_COUNTRY)],
    },
    "foam": {
        "composition": "Polyurethane foam padding",
        "section_232_fields": [],
    },
    "fabric": {
        "composition": "Synthetic microfibre fabric",
        "section_232_fields": [],
    },
    "leather": {
        "composition": "Leather",
        "section_232_fields": [],
    },
    "lubricant": {
        "composition": "Petroleum-based lubricant",
        "section_232_fields": [],
    },
}

DEFAULT_RULE = {
    "composition": "Mixed materials",
    "section_232_fields": [],
}


# ============================================================
# Helpers
# ============================================================
def pad_hs_code(hs):
    """Pad HS code to 8 digits, formatted XXXX.XX.XX."""
    if not hs:
        return ""
    clean = str(hs).replace(".", "").strip()
    if len(clean) < 8:
        clean = clean.ljust(8, "0")
    return f"{clean[0:4]}.{clean[4:6]}.{clean[6:8]}"


def get_meta(variant, key, default=""):
    """Look up metafield key in variant first, then product. Returns default if missing."""
    for edge in (variant.get("metafields", {}).get("edges") or []):
        node = edge.get("node", {})
        if node.get("key") == key:
            return node.get("value", default)
    product = variant.get("product", {}) or {}
    for edge in (product.get("metafields", {}).get("edges") or []):
        node = edge.get("node", {})
        if node.get("key") == key:
            return node.get("value", default)
    return default


def normalize_material(value):
    """Lowercase + strip + replace spaces with underscores."""
    if not value:
        return ""
    return value.lower().strip().replace(" ", "_")


def build_line_items(order):
    """Extract line items with all customs fields."""
    items = []
    for edge in (order.get("lineItems", {}).get("edges") or []):
        node = edge.get("node", {})
        variant = node.get("variant") or {}
        if not variant:
            continue

        title       = get_meta(variant, "fedex_product_title") \
                      or node.get("title", "Unknown product")
        hs_code     = get_meta(variant, "fedex_hs_code")
        material    = normalize_material(get_meta(variant, "fedex_material_type"))
        use         = get_meta(variant, "fedex_intended_use") or "Karting component"
        description = get_meta(variant, "fedex_description") or title

        qty = node.get("quantity", 1)
        shipping_cost = get_meta(variant, "fedex_shipping_cost")
        try:
            unit_value = float(shipping_cost) if shipping_cost else 0.0
        except (TypeError, ValueError):
            unit_value = 0.0
        if not unit_value:
            price = variant.get("price") or "0"
            try:
                unit_value = float(price)
            except (TypeError, ValueError):
                unit_value = 0.0
        line_value = unit_value * qty

        items.append({
            "title":        title,
            "description":  description,
            "hs_code":      pad_hs_code(hs_code),
            "material":     material,
            "intended_use": use,
            "quantity":     qty,
            "sku":          (variant.get("sku") or ""),
            "unit_value":   unit_value,
            "line_value":   line_value,
        })
    return items


def group_line_items(items):
    """Group line items by (HS code + material type).

    Items with the same HS and material consolidate into a single
    customs declaration entry. Variants are shown inline with quantities.
    """
    from collections import OrderedDict
    groups = OrderedDict()

    for item in items:
        key = (item["hs_code"], item["material"])
        if key not in groups:
            groups[key] = {
                "description":  item["description"],
                "title":        item["title"],
                "hs_code":      item["hs_code"],
                "material":     item["material"],
                "intended_use": item["intended_use"],
                "quantity":     0,
                "variants":     [],
                "line_value":   0.0,
            }
        g = groups[key]
        g["quantity"]   += item["quantity"]
        g["line_value"] += item["line_value"]
        # Variant inline label: use the title (which is fedex_product_title)
        # If multiple variants share the same title (unusual), still list each
        variant_label = item["title"]
        # Try to shorten — pull anything after a pipe or hyphen if present
        if " | " in variant_label:
            variant_label = variant_label.split(" | ")[0]
        g["variants"].append({
            "label":    variant_label,
            "sku":      item["sku"],
            "quantity": item["quantity"],
        })

    return list(groups.values())


# ============================================================
# PDF rendering
# ============================================================
def render_customs_pdf(order_name, line_items, output_path,
                       consignee=None, ship_date=None,
                       invoice_ref=None, total_value=None, currency="GBP"):
    """Render the customs declaration PDF."""
    doc = SimpleDocTemplate(
        output_path,
        pagesize=A4,
        leftMargin=1.8*cm, rightMargin=1.8*cm,
        topMargin=1.5*cm, bottomMargin=1.5*cm,
    )

    styles = getSampleStyleSheet()
    H1 = ParagraphStyle("H1", parent=styles["Heading1"], fontSize=16,
                        alignment=TA_LEFT, spaceAfter=2, textColor=black,
                        fontName="Helvetica-Bold")
    H2 = ParagraphStyle("H2", parent=styles["Heading2"], fontSize=11,
                        spaceAfter=4, spaceBefore=8, textColor=black,
                        fontName="Helvetica-Bold")
    PROD_TITLE = ParagraphStyle("PT", parent=styles["Heading3"], fontSize=11,
                                spaceAfter=2, textColor=black,
                                fontName="Helvetica-Bold")
    N  = ParagraphStyle("N",  parent=styles["Normal"], fontSize=9.5,
                        spaceAfter=1, leading=12)
    B  = ParagraphStyle("B",  parent=N, fontName="Helvetica-Bold")
    SM = ParagraphStyle("SM", parent=N, fontSize=8.5, leading=11)
    LABEL = ParagraphStyle("L", parent=N, fontName="Helvetica-Bold",
                           fontSize=9, textColor=HexColor("#444444"))

    story = []

    # TITLE
    story.append(Paragraph("CUSTOMS PRODUCT DECLARATION", H1))
    story.append(HRFlowable(width="100%", thickness=1.5,
                            color=black, spaceBefore=2, spaceAfter=6))

    story.append(Paragraph(f"<b>Shipment Reference:</b> Order #{order_name}", N))
    if invoice_ref:
        story.append(Paragraph(f"<b>Commercial Invoice Reference:</b> {invoice_ref}", N))
    if ship_date:
        story.append(Paragraph(f"<b>Date of Shipment:</b> {ship_date}", N))
    story.append(Spacer(1, 0.5*cm))

    # PARTIES
    story.append(HRFlowable(width="100%", thickness=0.5,
                            color=HexColor("#999999"),
                            spaceBefore=0, spaceAfter=4))

    parties_header = Table(
        [[Paragraph("<b>SHIPPED FROM</b>", LABEL),
          Paragraph("<b>SHIPPED TO</b>", LABEL)]],
        colWidths=[8.5*cm, 8.5*cm],
    )
    parties_header.setStyle(TableStyle([
        ("LEFTPADDING", (0,0), (-1,-1), 0),
        ("RIGHTPADDING", (0,0), (-1,-1), 0),
        ("BOTTOMPADDING", (0,0), (-1,-1), 2),
        ("TOPPADDING", (0,0), (-1,-1), 0),
    ]))
    story.append(parties_header)

    shipper_lines = [
        COMPANY_NAME, COMPANY_ADDRESS_1, COMPANY_ADDRESS_2,
        COMPANY_COUNTRY, f"Email: {COMPANY_EMAIL}",
    ]
    if consignee:
        consignee_lines = []
        if consignee.get("name"):     consignee_lines.append(consignee["name"])
        if consignee.get("company"):  consignee_lines.append(consignee["company"])
        for s in (consignee.get("street_lines") or []):
            if s: consignee_lines.append(s)
        loc = ", ".join(filter(None, [
            consignee.get("city"),
            consignee.get("province"),
            consignee.get("zip"),
        ]))
        if loc: consignee_lines.append(loc)
        if consignee.get("country"): consignee_lines.append(consignee["country"])
        if consignee.get("email"):   consignee_lines.append(f"Email: {consignee['email']}")
    else:
        consignee_lines = ["(consignee details not available)"]

    shipper_para   = [Paragraph(line, N) for line in shipper_lines]
    consignee_para = [Paragraph(line, N) for line in consignee_lines]
    parties = Table([[shipper_para, consignee_para]], colWidths=[8.5*cm, 8.5*cm])
    parties.setStyle(TableStyle([
        ("VALIGN", (0,0), (-1,-1), "TOP"),
        ("LEFTPADDING", (0,0), (-1,-1), 0),
        ("RIGHTPADDING", (0,0), (-1,-1), 6),
        ("BOTTOMPADDING", (0,0), (-1,-1), 0),
        ("TOPPADDING", (0,0), (-1,-1), 0),
    ]))
    story.append(parties)
    story.append(HRFlowable(width="100%", thickness=0.5,
                            color=HexColor("#999999"),
                            spaceBefore=6, spaceAfter=10))

    # DECLARED ITEMS
    story.append(Paragraph("DECLARED ITEMS", H2))
    story.append(HRFlowable(width="100%", thickness=1.0,
                            color=black, spaceBefore=0, spaceAfter=8))

    for idx, item in enumerate(line_items, 1):
        rule = MATERIAL_RULES.get(item["material"], DEFAULT_RULE)

        # Header: FedEx Product Title (variant metafield)
        product_header = Paragraph(
            f"<b>PRODUCT {idx}</b> &nbsp;&nbsp;&nbsp; {item['title']}", PROD_TITLE)

        spec_rows = [
            ("HS Code",                item["hs_code"] or "N/A"),
            ("Material Composition",   rule["composition"]),
            ("Country of Manufacture", ORIGIN_COUNTRY),
            ("Intended Use",           item["intended_use"]),
            ("Quantity",               f"{item['quantity']} unit(s)"),
        ]
        spec_rows.append(("Manufacturer", COMPANY_NAME))
        for field_name, field_value in rule["section_232_fields"]:
            spec_rows.append((field_name, field_value))
        # FedEx Description goes at the bottom
        if item.get("description") and item["description"] != item["title"]:
            spec_rows.append(("Description", item["description"]))

        inner_rows = [[Paragraph(label, LABEL), Paragraph(str(value), N)]
                      for label, value in spec_rows]
        inner = Table(inner_rows, colWidths=[5.2*cm, 11.8*cm])
        inner.setStyle(TableStyle([
            ("VALIGN", (0,0), (-1,-1), "TOP"),
            ("LEFTPADDING", (0,0), (-1,-1), 0),
            ("RIGHTPADDING", (0,0), (-1,-1), 0),
            ("BOTTOMPADDING", (0,0), (-1,-1), 1),
            ("TOPPADDING", (0,0), (-1,-1), 1),
        ]))

        outer = Table([[product_header], [inner]], colWidths=[17.0*cm])
        outer.setStyle(TableStyle([
            ("BOX", (0,0), (-1,-1), 0.5, HexColor("#cccccc")),
            ("LINEBELOW", (0,0), (0,0), 0.4, HexColor("#cccccc")),
            ("BACKGROUND", (0,0), (0,0), HexColor("#f5f5f5")),
            ("LEFTPADDING", (0,0), (-1,-1), 8),
            ("RIGHTPADDING",(0,0), (-1,-1), 8),
            ("TOPPADDING",  (0,0), (-1,-1), 6),
            ("BOTTOMPADDING",(0,0),(-1,-1), 6),
        ]))
        story.append(KeepTogether([outer, Spacer(1, 0.25*cm)]))

    # COMPLIANCE
    story.append(Paragraph(
        "These goods are manufactured karting components intended for motorsport use.", N))
    story.append(Spacer(1, 0.2*cm))
    story.append(Paragraph(
        "<b>Section 232:</b> All steel and aluminum articles are sourced "
        "from UK-based suppliers who represent the metal as having been "
        "melted/poured and smelted/cast in the United Kingdom.", SM))
    story.append(Spacer(1, 0.3*cm))
    story.append(Paragraph(
        "<i>I certify that the information on this declaration is true and "
        "complete to the best of my knowledge, and has been prepared using "
        "reasonable care in accordance with 19 U.S.C. 1484.</i>", SM))

    # DECLARANT
    story.append(Spacer(1, 0.6*cm))
    story.append(HRFlowable(width="100%", thickness=0.5,
                            color=HexColor("#999999"),
                            spaceBefore=0, spaceAfter=6))
    story.append(Paragraph("<b>DECLARED BY</b>", LABEL))
    story.append(Spacer(1, 0.1*cm))
    story.append(Paragraph(COMPANY_NAME, N))
    story.append(Paragraph(COMPANY_ADDRESS_1, N))
    story.append(Paragraph(COMPANY_ADDRESS_2, N))
    story.append(Paragraph(COMPANY_COUNTRY, N))
    story.append(Paragraph(f"Email: {COMPANY_EMAIL}", N))
    story.append(Spacer(1, 0.5*cm))
    today = datetime.now().strftime("%d/%m/%Y")
    sig_table = Table(
        [[Paragraph(f"<b>Signature:</b>  {SIGNATURE}", N),
          Paragraph(f"<b>Date:</b>  {today}", N)]],
        colWidths=[10.0*cm, 7.0*cm],
    )
    sig_table.setStyle(TableStyle([
        ("LEFTPADDING", (0,0), (-1,-1), 0),
        ("BOTTOMPADDING", (0,0), (-1,-1), 0),
        ("TOPPADDING", (0,0), (-1,-1), 0),
    ]))
    story.append(sig_table)

    doc.build(story)


# ============================================================
# Main
# ============================================================
def main():
    if len(sys.argv) < 2:
        print("Usage: python generate_customs_declaration.py <ORDER_NAME>")
        sys.exit(1)

    order_name = sys.argv[1].strip().lstrip("#")

    print(f"Connecting to Shopify...")
    token = get_access_token()
    print(f"Fetching order #{order_name}...")
    order = fetch_order(token, order_name)
    print(f"OK Got order #{order_name}")

    print(f"Building customs declaration...")
    raw_items = build_line_items(order)
    if not raw_items:
        print(f"ERROR No line items found for order #{order_name}")
        sys.exit(1)

    line_items = group_line_items(raw_items)

    print(f"   {len(raw_items)} raw item(s) grouped into {len(line_items)} declaration(s):")
    for idx, group in enumerate(line_items, 1):
        material = group["material"] or "(no material set)"
        variant_count = len(group["variants"])
        desc = (group["description"] or group["title"])[:50]
        print(f"     {idx}. {desc:<50}  [{material}, {variant_count} variant(s), qty {group['quantity']}]")

    # Consignee from shipping address
    ship_addr = order.get("shippingAddress") or {}
    customer  = order.get("customer") or {}
    consignee = {
        "name":         (ship_addr.get("name")
                         or f"{ship_addr.get('firstName','')} {ship_addr.get('lastName','')}".strip()
                         or customer.get("displayName") or ""),
        "company":      ship_addr.get("company"),
        "street_lines": [ship_addr.get("address1"), ship_addr.get("address2")],
        "city":         ship_addr.get("city"),
        "province":     ship_addr.get("provinceCode") or ship_addr.get("province"),
        "zip":          ship_addr.get("zip"),
        "country":      ship_addr.get("country"),
        "email":        customer.get("email"),
    }

    total_value = sum(item["line_value"] for item in line_items)
    currency    = order.get("currencyCode") or order.get("presentmentCurrencyCode") or "GBP"
    invoice_ref = f"SH-{order_name}"

    created_at = order.get("createdAt", "")
    try:
        ship_date = datetime.fromisoformat(created_at.replace("Z", "+00:00")).strftime("%d/%m/%Y")
    except Exception:
        ship_date = datetime.now().strftime("%d/%m/%Y")

    output_path = f"Customs_Declaration_{order_name}.pdf"
    render_customs_pdf(
        order_name, line_items, output_path,
        consignee=consignee,
        ship_date=ship_date,
        invoice_ref=invoice_ref,
        total_value=total_value,
        currency=currency,
    )
    print(f"OK PDF written to: {output_path}")

    # Upload to Drive folder for customs declarations
    customs_folder_id = os.getenv("GOOGLE_DRIVE_CUSTOMS_FOLDER_ID")
    if customs_folder_id:
        try:
            from drive_upload import upload_invoice
            order_dt = datetime.now()
            try:
                order_dt = datetime.fromisoformat(
                    (order.get("createdAt", "") or "").replace("Z", "+00:00"))
            except Exception:
                pass
            result = upload_invoice(output_path, customs_folder_id, order_date=order_dt)
            print(f"Uploaded to Drive: {result.get('link', 'success')}")
            print(f"   Folder: /FedEx Customs Declarations/{result.get('folder', '')}")
        except Exception as e:
            print(f"WARNING Drive upload failed: {e}")
    else:
        print(f"INFO  Set GOOGLE_DRIVE_CUSTOMS_FOLDER_ID in .env to enable Drive upload")

    print(f"\nDone.")


if __name__ == "__main__":
    main()
