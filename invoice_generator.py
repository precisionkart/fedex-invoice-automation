"""
FedEx Commercial Invoice — PDF generator.

Always renders in GBP. Line item prices come from Shopify variant metafields
(already GBP). Shipping cost arrives in the customer's order currency and is
converted to GBP at invoice-generation time using a live FX rate.

FX source: Frankfurter (api.frankfurter.dev) — European Central Bank rates,
free, no API key required.
"""

import requests
from datetime import datetime
from reportlab.lib.pagesizes import A4
from reportlab.lib import colors
from reportlab.lib.units import mm
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.enums import TA_RIGHT, TA_CENTER
from reportlab.platypus import (
    SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle
)

INVOICE = {
    "shipper": {
        "name": "Precision Kart",
        "tagline": "Unit 2 Pacemanor, Bellbrook Ind Estate · East Sussex, Uckfield · United Kingdom",
        "vat": "500090847",
        "eori": "GB042777994000",
    },
    "consignee": {
        "name": "Marco Bianchi",
        "address_lines": ["Via Roma 42", "20121 Milano (MI)", "Italy"],
        "phone": "+39 02 555 0123",
    },
    "invoice_no": "SH-10472",
    "order_no": "10472",
    "date": "13 May 2026",
    "reason_for_export": "Sale of goods",
    "incoterms": "DAP - Delivered At Place",
    "order_currency": "EUR",
    "shipping_in_order_currency": 21.72,
    "line_items": [
        ("Go-kart brake pads",    "6813.81", "GB", 2, 0.30, 4.46),
        ("Racing chain, karting", "7315.11", "GB", 1, 0.85, 52.83),
        ("Go-kart brake pads",    "6813.81", "GB", 3, 0.30, 4.46),
    ],
}

SHIPPER_FULL = {
    "name": "Precision Kart",
    "lines": [
        "Unit 2 Pacemanor, Bellbrook Ind Estate",
        "East Sussex, Uckfield",
        "TN22 1YA",
        "United Kingdom",
    ],
}

GBP_SYMBOL = "£"

INK     = colors.HexColor("#111827")
MUTED   = colors.HexColor("#6B7280")
RULE    = colors.HexColor("#E5E7EB")
ROW_ALT = colors.HexColor("#F9FAFB")

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


def money(amount):
    return f"{GBP_SYMBOL}\u00a0{amount:,.2f}"


def get_fx_rate(from_currency, to_currency="GBP"):
    """
    Live FX rate via Frankfurter (api.frankfurter.dev).
    European Central Bank rates. Free, no API key required.
    """
    if from_currency == to_currency:
        return 1.0
    try:
        url = f"https://api.frankfurter.dev/v1/latest?base={from_currency}&symbols={to_currency}"
        response = requests.get(url, timeout=10)
        response.raise_for_status()
        data = response.json()
        rate = data.get("rates", {}).get(to_currency)
        if rate is None:
            raise ValueError(f"No rate returned for {from_currency}→{to_currency}")
        return rate
    except Exception as e:
        raise RuntimeError(f"FX conversion failed ({from_currency}→{to_currency}): {e}")


def build_invoice(data, output_path):
    order_ccy = data["order_currency"]
    shipping_orig = data["shipping_in_order_currency"]
    fx_rate = get_fx_rate(order_ccy, "GBP")
    shipping_gbp = round(shipping_orig * fx_rate, 2)

    doc = SimpleDocTemplate(output_path, pagesize=A4,
        leftMargin=18*mm, rightMargin=18*mm, topMargin=16*mm, bottomMargin=14*mm,
        title=f"Commercial Invoice {data['invoice_no']}")
    story = []

    company_block = [
        Paragraph(data["shipper"]["name"], S["company"]),
        Paragraph(data["shipper"]["tagline"], S["tagline"]),
        Spacer(1, 2),
        Paragraph(f"VAT: {data['shipper']['vat']} · EORI: {data['shipper']['eori']}", S["vat"]),
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
         Paragraph(f"#{data['invoice_no']}", S["value_b"]),
         Paragraph(f"Order: #{data['order_no']}", S["value"])],
        [Paragraph("DATE", S["label"]),
         Paragraph(data["date"], S["value"])],
        [Paragraph("", S["label"])],
    ]], colWidths=[58*mm, 58*mm, 58*mm])
    meta.setStyle(TableStyle([("VALIGN",(0,0),(-1,-1),"TOP"),
        ("LEFTPADDING",(0,0),(-1,-1),0),("RIGHTPADDING",(0,0),(-1,-1),0),
        ("TOPPADDING",(0,0),(-1,-1),0),("BOTTOMPADDING",(0,0),(-1,-1),0)]))
    story.append(meta)
    story.append(Spacer(1, 10))

    shipper_cell = [Paragraph("SHIPPER", S["label"]), Paragraph(SHIPPER_FULL["name"], S["value_b"])]
    for ln in SHIPPER_FULL["lines"]:
        shipper_cell.append(Paragraph(ln, S["value"]))
    consignee_cell = [Paragraph("CONSIGNEE", S["label"]), Paragraph(data["consignee"]["name"], S["value_b"])]
    for ln in data["consignee"]["address_lines"]:
        consignee_cell.append(Paragraph(ln, S["value"]))
    consignee_cell.append(Paragraph(data["consignee"]["phone"], S["value"]))

    addr = Table([[shipper_cell, consignee_cell]], colWidths=[87*mm, 87*mm])
    addr.setStyle(TableStyle([("VALIGN",(0,0),(-1,-1),"TOP"),
        ("BOX",(0,0),(0,0),0.4,RULE),("BOX",(1,0),(1,0),0.4,RULE),
        ("LEFTPADDING",(0,0),(-1,-1),8),("RIGHTPADDING",(0,0),(-1,-1),8),
        ("TOPPADDING",(0,0),(-1,-1),8),("BOTTOMPADDING",(0,0),(-1,-1),8)]))
    story.append(addr)
    story.append(Spacer(1, 10))

    headers = ["Description", "HS Code", "Origin", "Qty",
               "Unit wt.", "Total wt.", f"Unit ({GBP_SYMBOL})", f"Total ({GBP_SYMBOL})"]
    rows = [[Paragraph(h, S["th"]) for h in headers]]

    subtotal = 0.0
    total_weight = 0.0
    for desc, hs, origin, qty, unit_w, unit_p in data["line_items"]:
        line_total = qty * unit_p
        line_weight = qty * unit_w
        subtotal += line_total
        total_weight += line_weight
        rows.append([
            Paragraph(desc, S["td"]),
            Paragraph(hs, S["td"]),
            Paragraph(origin, S["td"]),
            Paragraph(str(qty), S["td_r"]),
            Paragraph(f"{unit_w:.2f} kg", S["td_r"]),
            Paragraph(f"{line_weight:.2f} kg", S["td_r"]),
            Paragraph(f"{unit_p:,.2f}", S["td_r"]),
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
        Paragraph(data["reason_for_export"], S["decl_val"]),
        Spacer(1, 8),
        Paragraph("INCOTERMS", S["decl_lbl"]),
        Paragraph(data["incoterms"], S["decl_val"]),
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

    if order_ccy != "GBP":
        fx_text = (
            f"Shipping converted from {order_ccy} {shipping_orig:.2f} "
            f"at 1 {order_ccy} = {fx_rate:.4f} GBP "
            f"(ECB rate, retrieved {datetime.utcnow().strftime('%Y-%m-%d')})."
        )
        story.append(Paragraph(fx_text, S["fx_note"]))
        story.append(Spacer(1, 4))

    story.append(Paragraph(
        "Example only. Real invoices populate automatically from Shopify order and variant metafields.",
        S["footer"],
    ))

    doc.build(story)
    print(f"Invoice written to: {output_path}")
    print(f"  Order currency: {order_ccy}")
    print(f"  Shipping in {order_ccy}: {shipping_orig:.2f}")
    print(f"  FX rate {order_ccy}→GBP: {fx_rate:.4f}")
    print(f"  Shipping in GBP: {shipping_gbp:.2f}")
    print(f"  Grand total: £{grand_total:.2f}")


if __name__ == "__main__":
    build_invoice(INVOICE, "sample_invoice.pdf")
