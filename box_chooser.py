"""
Choose the smallest package that fits an order.

Rules:
  - Calculate total volume of items × 1.3 padding factor
  - Single item < 500g AND fits flat → envelope
  - Otherwise → box
  - Pick smallest package where volume + max single dimension fit
  - If nothing fits → return largest + flag for manual review

Packages match Precision Kart's real packaging stock.
Dimensions in cm, weights in grams.
"""

PADDING_FACTOR = 1.3

# Ordered smallest → largest by volume (within each category)
# Format: (name, length_cm, width_cm, height_cm, weight_grams, is_envelope)
PACKAGES = [
    # Envelopes (flexible — thin profile)
    ("Black Envelope",     22,   16,   2,   20,   True),
    ("M1 Envelope",        28,   22,   1,   60,   True),
    ("M3 Envelope",        35,   27,   1,   71,   True),
    # Boxes — small to large
    ("Small Box",          11.5, 16.5, 5,   32,   False),
    ("Medium Box",         18,   15,   7.5, 53,   False),
    ("Carrier Fold Box",   16,   8,    16,  62,   False),
    ("Large Box",          26,   24,   4,   68,   False),
    ("M1 Box Envelope",    28,   22,   7,   60,   False),
    ("X-Large Box",        26,   24,   8,   96,   False),
    ("M3 BOX ENVELOPE",    35,   28,   6,   100,  False),
    ("M3 Box Envelope",    32,   21,   8,   60,   False),
    ("XX-Large Box",       26,   26,   10,  136,  False),
    ("Chain Lube Box",     26,   21,   21,  100,  False),
    ("3XL Shoebox",        40,   25.5, 15,  150,  False),
]


def calc_volume(L, W, H):
    return L * W * H


def fits_in_package(item_L, item_W, item_H, pkg_L, pkg_W, pkg_H):
    item_dims = sorted([item_L, item_W, item_H])
    pkg_dims  = sorted([pkg_L, pkg_W, pkg_H])
    return all(i <= p for i, p in zip(item_dims, pkg_dims))


def choose_package(line_items):
    """
    Args: list of dicts like
      {"length_cm": 13.5, "width_cm": 6.6, "height_cm": 2,
       "weight_kg": 0.4, "quantity": 1}

    Returns: dict with package selection + total weight + manual_review flag.
    """
    if not line_items:
        return {"manual_review": True, "reason": "No line items"}

    for it in line_items:
        for k in ("length_cm", "width_cm", "height_cm", "weight_kg", "quantity"):
            if it.get(k) in (None, 0):
                return {
                    "manual_review": True,
                    "reason": f"Item missing {k} — add to product metafields",
                }

    total_volume_needed = sum(
        calc_volume(it["length_cm"], it["width_cm"], it["height_cm"]) * it["quantity"]
        for it in line_items
    ) * PADDING_FACTOR
    total_weight_kg = sum(it["weight_kg"] * it["quantity"] for it in line_items)
    total_items     = sum(it["quantity"] for it in line_items)
    max_single_dim  = max(
        max(it["length_cm"], it["width_cm"], it["height_cm"])
        for it in line_items
    )

    use_envelope_class = (
        total_items == 1 and total_weight_kg < 0.5 and max_single_dim < 35
    )

    for name, L, W, H, pkg_g, is_env in PACKAGES:
        if not use_envelope_class and is_env:
            continue
        pkg_volume = calc_volume(L, W, H)
        if pkg_volume < total_volume_needed:
            continue
        if max_single_dim > max(L, W, H):
            continue
        if not all(
            fits_in_package(it["length_cm"], it["width_cm"], it["height_cm"], L, W, H)
            for it in line_items
        ):
            continue
        return {
            "package_name":  name,
            "length_cm":     L,
            "width_cm":      W,
            "height_cm":     H,
            "weight_kg":     round(total_weight_kg + pkg_g / 1000, 3),
            "is_envelope":   is_env,
            "manual_review": False,
            "reason":        f"Smallest fitting (vol {total_volume_needed:.0f}cm³, max dim {max_single_dim}cm)",
        }

    # Nothing fit — use largest
    name, L, W, H, pkg_g, is_env = PACKAGES[-1]
    return {
        "package_name":  name,
        "length_cm":     L,
        "width_cm":      W,
        "height_cm":     H,
        "weight_kg":     round(total_weight_kg + pkg_g / 1000, 3),
        "is_envelope":   is_env,
        "manual_review": True,
        "reason":        "No package large enough — using largest, flag for manual review",
    }


if __name__ == "__main__":
    print("Testing Precision Chain scenarios...")
    print()
    print("1 chain @ 350g:", choose_package([
        {"length_cm": 13.5, "width_cm": 6.6, "height_cm": 2, "weight_kg": 0.35, "quantity": 1}
    ]))
