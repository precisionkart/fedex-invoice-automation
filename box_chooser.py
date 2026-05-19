"""
Box / package chooser for Precision Kart shipments.

Picks the smallest standard packaging that:
  - Fits the largest item in the order (after padding)
  - Has weight capacity to hold the total order weight

Rules:
  - Envelope (M3 Envelope) only used if: 1 item, <500g, max dimension <=35cm
  - Otherwise smallest-fitting box wins
  - 1.3x padding applied to longest dimension (room for bubble wrap)

NOTES:
  - X-Large Box is the "Majority Pick" — most orders end up here. Just an info note,
    the picker uses smallest-fitting box.
  - All package weights are the empty-box tare weight in kg.
  - When sandbox tracking numbers exist, this code is not used; that's purely FedEx.
"""

# (name, length_cm, width_cm, height_cm, tare_weight_g, is_envelope)
# Sorted ascending by total volume so smallest tested first.
PACKAGES = [
    ("Small Box",          11.5, 16.5,  5,    32,  False),
    ("Carrier Fold Box",   16,   8,    16,    62,  False),
    ("Large Box",          26,   24,    4,    68,  False),
    ("M1 Box Envelope",    28,   22,    7,    60,  False),
    ("M3 Envelope",        35,   27,    1,    71,  True),   # envelope — strict rules
    ("X-Large Box",        26,   24,    8,    96,  False),  # MAJORITY PICK
    ("M3 Box Envelope",    32,   21,    8,    60,  False),
    ("XX-Large Box",       26,   26,   10,   136,  False),
    ("Chain Lube Box",     26,   21,   21,   100,  False),
    ("3XL Shoebox",        40,   25.5, 15,   150,  False),
]


# Padding factor — 5% buffer on longest dimension only.
# Kart parts are rigid with defined edges; small buffer accounts for bubble wrap.
PADDING_FACTOR = 1.05

# Envelope rules
ENVELOPE_MAX_WEIGHT_KG = 0.5
ENVELOPE_MAX_DIM_CM    = 35.0


def _item_fits(item, pkg_dims):
    """Check if an item fits in a package. 5% padding on longest dim only."""
    item_dims = sorted([item["length_cm"], item["width_cm"], item["height_cm"]], reverse=True)
    pkg_sorted = sorted(pkg_dims, reverse=True)
    # Longest dim gets padding; mid + short fit exactly
    return (
        item_dims[0] * PADDING_FACTOR <= pkg_sorted[0]  # longest
        and item_dims[1]              <= pkg_sorted[1]  # mid
        and item_dims[2]              <= pkg_sorted[2]  # short
    )


def choose_package(line_items):
    """
    Decide which package to use for an order.

    Args:
        line_items: list of dicts with length_cm, width_cm, height_cm, weight_kg, quantity, title
    Returns:
        dict with package_name, length_cm, width_cm, height_cm, weight_kg, is_envelope
        OR dict with manual_review=True and reason if no fit
    """
    if not line_items:
        return {"manual_review": True, "reason": "No line items"}

    # Find largest single item (by longest dimension)
    largest = max(line_items, key=lambda it: max(
        it.get("length_cm", 0), it.get("width_cm", 0), it.get("height_cm", 0)
    ))

    total_qty    = sum(it.get("quantity", 1) for it in line_items)
    total_weight = sum(
        (it.get("weight_kg", 0) or 0) * it.get("quantity", 1)
        for it in line_items
    )

    # Envelope eligibility (M3 Envelope): strict rules
    envelope_eligible = (
        len(line_items) == 1
        and total_qty   == 1
        and total_weight < ENVELOPE_MAX_WEIGHT_KG
        and max(largest["length_cm"], largest["width_cm"], largest["height_cm"]) <= ENVELOPE_MAX_DIM_CM
    )

    # Try each package in size order (smallest to largest)
    for name, L, W, H, tare_g, is_envelope in PACKAGES:
        # Skip envelopes if not eligible
        if is_envelope and not envelope_eligible:
            continue

        # Does the largest item fit (with padding)?
        if not _item_fits(largest, (L, W, H)):
            continue

        # Total weight including tare
        total_kg = round(total_weight + (tare_g / 1000.0), 3)

        return {
            "package_name":  name,
            "length_cm":     L,
            "width_cm":      W,
            "height_cm":     H,
            "weight_kg":     total_kg,
            "is_envelope":   is_envelope,
        }

    # Nothing fits — flag for manual review
    return {
        "manual_review": True,
        "reason": (
            f"Largest item {largest.get('title', 'unknown')} "
            f"({largest['length_cm']}×{largest['width_cm']}×{largest['height_cm']}cm) "
            "doesn't fit any standard box"
        ),
    }


if __name__ == "__main__":
    # Quick self-test
    test_items_chain = [{
        "title": "Chain", "length_cm": 13.5, "width_cm": 6.6, "height_cm": 2,
        "weight_kg": 0.275, "quantity": 1,
    }]
    test_items_two_sprockets = [
        {"title": "Sprocket Protector", "length_cm": 2, "width_cm": 22, "height_cm": 22,
         "weight_kg": 0.41, "quantity": 1},
        {"title": "Sprocket Protector", "length_cm": 2, "width_cm": 22, "height_cm": 22,
         "weight_kg": 0.36, "quantity": 1},
    ]
    test_items_huge = [{
        "title": "Frame", "length_cm": 60, "width_cm": 40, "height_cm": 30,
        "weight_kg": 5, "quantity": 1,
    }]

    print("Chain (1 item, 0.275kg):")
    print(f"  → {choose_package(test_items_chain)}")
    print()
    print("2 Sprocket Protectors:")
    print(f"  → {choose_package(test_items_two_sprockets)}")
    print()
    print("Huge frame (won't fit):")
    print(f"  → {choose_package(test_items_huge)}")
