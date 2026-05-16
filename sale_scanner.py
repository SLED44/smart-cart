"""
sale_scanner.py
---------------
Pre-review sale alternative scanner for SmartCart.

Runs once per session, immediately after list normalisation and staples
loading, before the item-by-item review queue opens.

For every item in the session that has a saved household preference,
this module queries the Kroger Products API to find comparable products
that are currently on sale at the household's store.

Comparable is defined as (PRD PM-04):
    - Same Kroger product category as the preferred item
    - Size within 20% of the preferred item's size (where size is parseable)

Public interface:
    scan_for_sale_alternatives(session_items) -> ScanResult

ScanResult structure:
    {
        "sale_alerts": [
            {
                "item_key":          str,   Normalised item key
                "item_name":         str,   Display name
                "preferred_product": dict,  The saved preferred Kroger product
                "sale_product":      dict,  The on-sale alternative found
                "savings_amount":    float, Dollar savings vs preferred
                "savings_pct":       float, Percentage savings vs preferred
            },
            ...
        ],
        "scanned_count":  int,   Number of preferred items scanned
        "alert_count":    int,   Number of sale alternatives found
    }

Module boundary (PRD SS-01):
    Called once per session before review begins.
    No UI dependency. Returns data only.
"""

import os
import re
from concurrent.futures import ThreadPoolExecutor

import requests
from dotenv import load_dotenv

from kroger_auth import get_valid_token
from preference_store import get_all_preferences, get_preference

load_dotenv(override=True)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

KROGER_PRODUCTS_URL = "https://api.kroger.com/v1/products"

# Size comparability tolerance (PRD PM-04: within 20%)
SIZE_TOLERANCE = 0.20

# Max products to fetch per scan query
SCAN_RESULTS_LIMIT = 15

# Concurrent workers for sale-alternative lookups
SCAN_WORKERS = 5

# Minimum savings to surface an alert (avoid flagging $0.01 differences)
MIN_SAVINGS_THRESHOLD = 0.25

# ---------------------------------------------------------------------------
# Size parsing
# ---------------------------------------------------------------------------

# Common unit conversions to ounces for size comparison
_UNIT_TO_OZ = {
    "oz":    1.0,
    "fl oz": 1.0,
    "floz":  1.0,
    "lb":    16.0,
    "lbs":   16.0,
    "pound": 16.0,
    "pounds":16.0,
    "g":     0.03527,
    "gram":  0.03527,
    "grams": 0.03527,
    "kg":    35.274,
    "l":     33.814,
    "liter": 33.814,
    "litre": 33.814,
    "ml":    0.03381,
    "ct":    1.0,   # count — treat as-is
    "count": 1.0,
    "pk":    1.0,
    "pack":  1.0,
}


def _parse_size_to_oz(size_str: str) -> float | None:
    """
    Parse a size string into a normalised float (oz-equivalent).
    Returns None if the size string cannot be parsed.

    Examples:
        "32 oz"     -> 32.0
        "1 lb"      -> 16.0
        "1 gallon"  -> 128.0
        "12 count"  -> 12.0
        "500g"      -> 17.64
        "2 liter"   -> 67.63
    """
    if not size_str:
        return None

    size_str = size_str.lower().strip()

    # Handle gallon specially
    gallon_match = re.search(r"([\d.]+)\s*gal", size_str)
    if gallon_match:
        try:
            return float(gallon_match.group(1)) * 128.0
        except ValueError:
            pass

    # General pattern: number + unit
    match = re.search(r"([\d.]+)\s*([a-z\s]+)", size_str)
    if not match:
        return None

    try:
        value = float(match.group(1))
    except ValueError:
        return None

    unit = match.group(2).strip().rstrip("s")  # basic singularisation
    # Try exact match first, then stripped version
    multiplier = _UNIT_TO_OZ.get(unit) or _UNIT_TO_OZ.get(match.group(2).strip())

    if multiplier is None:
        return None

    return value * multiplier


def _sizes_are_comparable(size_a: str, size_b: str, tolerance: float = SIZE_TOLERANCE) -> bool:
    """
    Returns True if two size strings are within `tolerance` of each other.
    Returns True (benefit of the doubt) if either size cannot be parsed.
    """
    oz_a = _parse_size_to_oz(size_a)
    oz_b = _parse_size_to_oz(size_b)

    # If we can't parse either size, assume comparable
    if oz_a is None or oz_b is None:
        return True

    # Avoid division by zero
    if oz_a == 0:
        return oz_b == 0

    ratio = abs(oz_a - oz_b) / oz_a
    return ratio <= tolerance


# ---------------------------------------------------------------------------
# Kroger API helpers
# ---------------------------------------------------------------------------

def _search_products_for_scan(
    query: str,
    location_id: str,
    token: str,
    limit: int = SCAN_RESULTS_LIMIT,
) -> list[dict]:
    """
    Fetch products for sale scanning. Returns raw Kroger product dicts
    (not normalised — we need raw price fields for sale detection).
    """
    params = {
        "filter.term":       query,
        "filter.locationId": location_id,
        "filter.limit":      limit,
    }
    headers = {
        "Authorization": f"Bearer {token}",
        "Accept":        "application/json",
    }

    try:
        response = requests.get(
            KROGER_PRODUCTS_URL,
            params=params,
            headers=headers,
            timeout=15,
        )
    except requests.RequestException as e:
        print(f"  ⚠ Sale scan API error for '{query}': {e}")
        return []

    if not response.ok:
        return []

    return response.json().get("data", [])


def _extract_pricing(raw_product: dict) -> tuple[float | None, float | None]:
    """
    Extract (regular_price, promo_price) from a raw Kroger product dict.
    Returns (None, None) if pricing data is unavailable.
    """
    items = raw_product.get("items", [])
    if not items:
        return None, None

    price_data = items[0].get("price", {})
    if not price_data:
        return None, None

    regular = price_data.get("regular")
    promo = price_data.get("promo")

    try:
        regular_price = float(regular) if regular is not None else None
    except (TypeError, ValueError):
        regular_price = None

    try:
        promo_price = float(promo) if promo is not None and promo != 0 else None
    except (TypeError, ValueError):
        promo_price = None

    return regular_price, promo_price


def _is_on_sale(regular_price: float | None, promo_price: float | None) -> bool:
    """
    Determine if a product is on sale.

    Kroger's API doesn't always return a clean boolean "on_sale" flag
    (PRD Open Question #4). We infer it from pricing:
    - promo_price exists AND is less than regular_price -> on sale
    """
    if regular_price is None or promo_price is None:
        return False
    return promo_price < regular_price


def _normalise_scan_product(raw: dict) -> dict:
    """Convert a raw Kroger product to SmartCart's standard format for display."""
    upc = raw.get("upc", "")
    description = raw.get("description", "Unknown Product")
    brand = raw.get("brandName", "")

    items = raw.get("items", [])
    size = items[0].get("size", "") if items else ""

    regular_price, promo_price = _extract_pricing(raw)
    on_sale = _is_on_sale(regular_price, promo_price)

    categories = raw.get("categories", [])
    category = categories[0] if categories else ""

    # Image
    image_url = None
    for img in raw.get("images", []):
        if img.get("perspective") in ("front", "back"):
            for sz in img.get("sizes", []):
                if sz.get("size") in ("medium", "small"):
                    image_url = sz.get("url")
                    break
        if image_url:
            break

    return {
        "upc":          upc,
        "product_name": description,
        "brand":        brand,
        "size":         size,
        "price":        regular_price,
        "promo_price":  promo_price,
        "on_sale":      on_sale,
        "in_stock":     True,
        "image_url":    image_url,
        "category":     category,
    }


# ---------------------------------------------------------------------------
# Core scan logic
# ---------------------------------------------------------------------------

def _scan_single_item(
    item_key: str,
    item_name: str,
    preferred_product: dict,
    location_id: str,
    token: str,
) -> dict | None:
    """
    Scan for sale alternatives for a single preferred item.
    Returns a sale_alert dict if a comparable sale item is found,
    or None if nothing qualifies.
    """
    preferred_price = preferred_product.get("price")
    preferred_size  = preferred_product.get("size", "")
    preferred_upc   = preferred_product.get("kroger_upc", "")

    # Search by item name in the same category
    raw_products = _search_products_for_scan(item_name, location_id, token)

    best_alert = None
    best_savings = MIN_SAVINGS_THRESHOLD  # Must beat this to qualify

    for raw in raw_products:
        # Skip the preferred product itself
        if raw.get("upc") == preferred_upc:
            continue

        regular_price, promo_price = _extract_pricing(raw)

        # Must be on sale
        if not _is_on_sale(regular_price, promo_price):
            continue

        # Must have a parseable sale price
        if promo_price is None:
            continue

        sale_product = _normalise_scan_product(raw)

        # Size comparability check (PRD PM-04)
        if not _sizes_are_comparable(preferred_size, sale_product["size"]):
            continue

        # Calculate savings vs preferred product's price
        # If preferred price is unknown, compare against regular price of sale item
        reference_price = preferred_price or regular_price
        if reference_price is None:
            continue

        savings_amount = reference_price - promo_price
        if savings_amount <= best_savings:
            continue

        # This is the best qualifying sale alternative so far
        savings_pct = (savings_amount / reference_price) * 100
        best_savings = savings_amount
        best_alert = {
            "item_key":          item_key,
            "item_name":         item_name,
            "preferred_product": preferred_product,
            "sale_product":      sale_product,
            "savings_amount":    round(savings_amount, 2),
            "savings_pct":       round(savings_pct, 1),
        }

    return best_alert


def scan_for_sale_alternatives(session_items: list) -> dict:
    """
    Scan all preferred items in the current session for on-sale alternatives.

    Args:
        session_items: List of item dicts from list_parser / staples trigger.
                       Items without a saved preference are skipped (PRD SS-08).

    Returns:
        ScanResult dict:
        {
            "sale_alerts":    list of sale alert dicts (may be empty),
            "scanned_count":  int,
            "alert_count":    int,
        }

    Does not raise — any individual item failure is caught and skipped.
    The scan is best-effort; a partial result is better than no result.
    """
    location_id = os.getenv("KROGER_LOCATION_ID", "").strip()
    if not location_id or location_id == "your_store_location_id_here":
        # Can't scan without a location — return empty result silently
        return {"sale_alerts": [], "scanned_count": 0, "alert_count": 0}

    all_preferences = get_all_preferences()

    # Filter to items that have saved preferences (PRD SS-08)
    items_to_scan = [
        item for item in session_items
        if item.get("item_key") in all_preferences
    ]

    if not items_to_scan:
        return {"sale_alerts": [], "scanned_count": 0, "alert_count": 0}

    token = get_valid_token()

    print(f"Sale Scan: checking {len(items_to_scan)} preferred items for sale alternatives...")

    def _scan_one(item: dict):
        item_key  = item["item_key"]
        item_name = item["item_name"]
        pref      = all_preferences[item_key]
        preferred_product = {
            "kroger_upc":   pref.get("kroger_upc", ""),
            "product_name": pref.get("product_name", ""),
            "brand":        pref.get("brand", ""),
            "size":         pref.get("size", ""),
            "price":        pref.get("price"),
        }
        try:
            return _scan_single_item(
                item_key=item_key,
                item_name=item_name,
                preferred_product=preferred_product,
                location_id=location_id,
                token=token,
            )
        except Exception as e:  # noqa: BLE001 — one bad item shouldn't kill the scan
            print(f"  ⚠ Scan error for '{item_name}': {e}")
            return None

    with ThreadPoolExecutor(max_workers=SCAN_WORKERS) as pool:
        results = list(pool.map(_scan_one, items_to_scan))

    sale_alerts = [r for r in results if r is not None]

    print(f"Sale Scan complete: {len(sale_alerts)} alert(s) found across {len(items_to_scan)} items.")

    return {
        "sale_alerts":   sale_alerts,
        "scanned_count": len(items_to_scan),
        "alert_count":   len(sale_alerts),
    }


# ---------------------------------------------------------------------------
# UI helper — apply session switches from Sale Scan screen
# ---------------------------------------------------------------------------

def apply_sale_switches(
    matched_items: list,
    switched_item_keys: list[str],
    scan_result: dict,
) -> list:
    """
    Apply the user's choices from the Sale Scan screen to the matched item list.

    For each item_key in switched_item_keys, replace the primary product
    with the sale alternative identified in the scan. Marks it with
    match_type "On Sale Alt" so the review screen shows the right badge.

    Args:
        matched_items:      Output of product_matcher.match_items()
        switched_item_keys: Item keys the user chose to switch to sale items
        scan_result:        Output of scan_for_sale_alternatives()

    Returns:
        Updated matched_items list.
    """
    if not switched_item_keys:
        return matched_items

    # Build a lookup from item_key to sale alert
    alert_by_key = {
        alert["item_key"]: alert
        for alert in scan_result.get("sale_alerts", [])
    }

    updated = []
    for item in matched_items:
        item_key = item.get("item_key", "")
        if item_key in switched_item_keys and item_key in alert_by_key:
            alert = alert_by_key[item_key]
            sale_product = alert["sale_product"]

            # Replace primary with sale product
            updated_item = {**item}
            updated_item["primary"]      = sale_product
            updated_item["match_type"]   = "On Sale Alt"
            updated_item["match_reason"] = (
                f"Switched to sale item — saving ${alert['savings_amount']:.2f} "
                f"({alert['savings_pct']:.1f}% less than your usual choice)."
            )
            updated.append(updated_item)
        else:
            updated.append(item)

    return updated


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import sys
    from list_parser import parse_grocery_list

    print("SmartCart — Sale Scanner Test")
    print("-" * 40)

    location_id = os.getenv("KROGER_LOCATION_ID", "")
    if not location_id or location_id == "your_store_location_id_here":
        print("✗ KROGER_LOCATION_ID not set in .env")
        sys.exit(1)

    # Use a short test list — only items with saved preferences will be scanned
    TEST_LIST = "whole milk, eggs, butter, olive oil, chicken breast"

    try:
        parse_result = parse_grocery_list(TEST_LIST)
        items = parse_result["items"]

        print(f"Parsed {len(items)} items.")
        print("Note: Only items with saved preferences will be scanned.")
        print("If you have no preferences saved yet, alert_count will be 0.\n")

        result = scan_for_sale_alternatives(items)

        print(f"\nScanned:     {result['scanned_count']} preferred items")
        print(f"Sale alerts: {result['alert_count']}")

        for alert in result["sale_alerts"]:
            print(f"\n  {alert['item_name']}")
            print(f"    Your usual: {alert['preferred_product']['brand']} "
                  f"{alert['preferred_product']['product_name']} "
                  f"(${alert['preferred_product']['price']:.2f})")
            print(f"    On sale:    {alert['sale_product']['brand']} "
                  f"{alert['sale_product']['product_name']} "
                  f"(${alert['sale_product']['promo_price']:.2f})")
            print(f"    Savings:    ${alert['savings_amount']:.2f} "
                  f"({alert['savings_pct']:.1f}%)")

    except (RuntimeError, ValueError) as e:
        print(f"\n✗ Error: {e}")
        sys.exit(1)
