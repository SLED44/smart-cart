"""
product_matcher.py
------------------
Core product matching engine for SmartCart.

For each normalised grocery list item, this module:
  1. Checks for a saved household preference (preference_store.py)
  2. Queries the Kroger Products API at the household's store location
  3. Uses Claude Haiku to select the best match for unpreferred items
  4. Detects out-of-stock preferred products and promotes a substitute
  5. Returns a fully matched item list ready for the review screen

Public interface:
    match_items(parsed_items) -> list[MatchedItem]

MatchedItem structure:
    {
        # Original parsed item fields (item_name, quantity, unit, etc.)

        "match_type":     str,   One of the MATCH_TYPE constants below
        "confidence":     float, 0.0–1.0
        "primary":        dict,  The recommended Kroger product (or None)
        "alternatives":   list,  Up to 3 alternative Kroger products
        "match_reason":   str,   Plain-English explanation of why this was chosen
    }

Match types (PRD PM-02 through PM-07):
    PREFERRED       Saved preference found and in stock
    PREFERRED_OOS   Saved preference found but out of stock
    BEST_MATCH      No preference — Claude selected best result
    NEEDS_PICK      Low confidence — user should review carefully
    NOT_FOUND       No Kroger products found for this item

Kroger product structure (from API, normalised):
    {
        "upc":          str,
        "product_name": str,
        "brand":        str,
        "size":         str,
        "price":        float | None,
        "promo_price":  float | None,   Sale price if on promotion
        "on_sale":      bool,
        "in_stock":     bool,
        "image_url":    str | None,
        "category":     str,
    }

Module boundary (PRD PM-08):
    Accepts normalised item list + preferences, returns matched item list.
    No UI dependency. Can be called directly by future modules.
"""

import os
import json
import time
from concurrent.futures import ThreadPoolExecutor
from typing import Any

import anthropic
import requests
from dotenv import load_dotenv

from kroger_auth import get_valid_token
from preference_store import get_all_preferences, normalise_item_key

load_dotenv(override=True)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

KROGER_PRODUCTS_URL = "https://api.kroger.com/v1/products"

# Match type labels (these appear as badges in the review UI)
MATCH_PREFERRED     = "Preferred Match"
MATCH_PREFERRED_OOS = "Preferred OOS"
MATCH_BEST          = "Best Match"
MATCH_NEEDS_PICK    = "Needs Your Pick"
MATCH_NOT_FOUND     = "Not Found"

# Confidence threshold below which an item is flagged as NEEDS_PICK
CONFIDENCE_THRESHOLD = 0.65

# Max results to request from Kroger Products API per item
MAX_KROGER_RESULTS = 10

# Max alternatives to surface in the review screen
MAX_ALTERNATIVES = 3

# Concurrency for per-item Kroger lookups. 5 workers gives a ~5x speedup on
# typical 15-item lists. Kroger's published rate limits (a few QPS) absorb this
# easily for a single-user app; bump higher only if you see consistent throttling.
MATCH_WORKERS = 5

# ---------------------------------------------------------------------------
# Kroger Products API
# ---------------------------------------------------------------------------

def _search_kroger_products(
    query: str,
    location_id: str,
    token: str,
    limit: int = MAX_KROGER_RESULTS,
) -> list[dict]:
    """
    Search Kroger Products API for a keyword, scoped to a store location.
    Returns a list of normalised product dicts.
    Returns empty list on any error (caller handles gracefully).
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
        print(f"  ⚠ Kroger API request failed for '{query}': {e}")
        return []

    if response.status_code == 401:
        # Token expired mid-session — shouldn't happen with our refresh logic
        # but handle gracefully
        print("  ⚠ Kroger token expired mid-match. Refresh and retry.")
        return []

    if not response.ok:
        print(f"  ⚠ Kroger API error {response.status_code} for '{query}': {response.text[:100]}")
        return []

    data = response.json()
    raw_products = data.get("data", [])

    return [_normalise_kroger_product(p) for p in raw_products]


def _get_product_by_upc(upc: str, location_id: str, token: str) -> dict | None:
    """
    Fetch a specific Kroger product by UPC, scoped to a store location.
    Returns normalised product dict, or None if not found.
    """
    params = {
        "filter.term":       upc,
        "filter.locationId": location_id,
        "filter.limit":      1,
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
    except requests.RequestException:
        return None

    if not response.ok:
        return None

    data = response.json()
    products = data.get("data", [])
    if not products:
        return None

    return _normalise_kroger_product(products[0])


def _normalise_kroger_product(raw: dict) -> dict:
    """
    Convert a raw Kroger API product object into SmartCart's standard format.
    Handles missing fields gracefully — the Kroger API is inconsistent.
    """
    # Product identity
    upc = raw.get("upc", "")
    description = raw.get("description", "Unknown Product")
    brand = raw.get("brandName", "")

    # Size — Kroger puts this in "items[0].size" or "size"
    size = ""
    items = raw.get("items", [])
    if items:
        size = items[0].get("size", "")
    if not size:
        size = raw.get("size", "")

    # Pricing — in items[0].price
    price = None
    promo_price = None
    on_sale = False
    in_stock = True  # Default to True; False if explicitly flagged

    if items:
        item_data = items[0]
        price_data = item_data.get("price", {})

        if price_data:
            regular = price_data.get("regular")
            promo = price_data.get("promo")

            if regular is not None:
                try:
                    price = float(regular)
                except (TypeError, ValueError):
                    pass

            if promo is not None and promo != 0:
                try:
                    promo_price = float(promo)
                    on_sale = True
                except (TypeError, ValueError):
                    pass

        # Stock status
        fulfillment = item_data.get("fulfillment", {})
        # Kroger returns fulfillment options; if none available, treat as OOS
        if fulfillment:
            # "curbside" or "delivery" being True means available
            in_stock = any([
                fulfillment.get("curbside", False),
                fulfillment.get("delivery", False),
                fulfillment.get("inStore", False),
            ])

    # Image URL — in images array, prefer "medium" or "small" perspective
    image_url = None
    images = raw.get("images", [])
    for img in images:
        if img.get("perspective") in ("front", "back"):
            sizes = img.get("sizes", [])
            for sz in sizes:
                if sz.get("size") in ("medium", "small"):
                    image_url = sz.get("url")
                    break
        if image_url:
            break

    # Category from Kroger's taxonomy
    categories = raw.get("categories", [])
    category = categories[0] if categories else ""

    return {
        "upc":          upc,
        "product_name": description,
        "brand":        brand,
        "size":         size,
        "price":        price,
        "promo_price":  promo_price,
        "on_sale":      on_sale,
        "in_stock":     in_stock,
        "image_url":    image_url,
        "category":     category,
    }


# ---------------------------------------------------------------------------
# Claude product match reasoning
# ---------------------------------------------------------------------------

MATCH_SYSTEM_PROMPT = """Pick the best Kroger product for a grocery list item.
Prefer exact name matches, standard household sizes, and lower prices when quality is equivalent.
If nothing fits, return best_match_index=-1.

Respond with ONLY this JSON, no preamble:
{"best_match_index": 0, "confidence": 0.92, "reason": "one short sentence", "ranked_alternatives": [1,2,3]}

Indices are 0-based into the provided list. Exclude best_match_index from ranked_alternatives. Max 3 alternatives."""


def _claude_select_best_match(
    item_name: str,
    quantity: float,
    unit: str,
    notes: str,
    products: list[dict],
) -> dict:
    """
    Ask Claude Haiku to select the best matching product from search results.
    Returns a dict with best_match_index, confidence, reason, ranked_alternatives.
    Falls back to index 0 with low confidence on any error.
    """
    if not products:
        return {
            "best_match_index": -1,
            "confidence": 0.0,
            "reason": "No products found.",
            "ranked_alternatives": [],
        }

    # Build a compact product list for the prompt
    product_summary = []
    for i, p in enumerate(products):
        price_str = f"${p['price']:.2f}" if p["price"] else "price unknown"
        sale_str = f" (SALE: ${p['promo_price']:.2f})" if p["on_sale"] and p["promo_price"] else ""
        stock_str = " [OUT OF STOCK]" if not p["in_stock"] else ""
        product_summary.append(
            f"[{i}] {p['brand']} {p['product_name']} | {p['size']} | {price_str}{sale_str}{stock_str}"
        )

    item_description = item_name
    if notes and "lbs" in notes:
        # Weight-sold item — the notes contain the real quantity signal
        item_description += f" ({notes})"
    elif quantity and quantity != 1.0:
        unit_str = unit if unit and unit not in ("count","each","ea") else "x"
        item_description += f" ({quantity} {unit_str})".strip()
        if notes:
            item_description += f" — {notes}"
    elif notes:
        item_description += f" — {notes}"

    user_message = (
        f"Item needed: {item_description}\n"
        f"Household note: Pick the product that best matches this size/quantity. "
        f"If buying multiple units, prefer individual portions over a single large pack when available.\n\n"
        f"Available products:\n" + "\n".join(product_summary)
    )

    api_key = os.getenv("ANTHROPIC_API_KEY", "")
    if not api_key:
        # Fall back gracefully if API key missing
        return {
            "best_match_index": 0,
            "confidence": 0.5,
            "reason": "API key not configured — defaulting to first result.",
            "ranked_alternatives": list(range(1, min(4, len(products)))),
        }

    client = anthropic.Anthropic(api_key=api_key)

    try:
        message = client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=256,
            system=MATCH_SYSTEM_PROMPT,
            messages=[{"role": "user", "content": user_message}],
        )
    except anthropic.APIError as e:
        print(f"  ⚠ Claude match API error for '{item_name}': {e}")
        return {
            "best_match_index": 0,
            "confidence": 0.5,
            "reason": "Claude API error — defaulting to first result.",
            "ranked_alternatives": list(range(1, min(4, len(products)))),
        }

    response_text = "".join(
        block.text for block in message.content if hasattr(block, "text")
    )

    # Parse JSON response
    result = _parse_match_response(response_text, len(products))
    return result


def _parse_match_response(text: str, num_products: int) -> dict:
    """Parse Claude's match selection response, with fallback on failure."""
    import re

    text = text.strip()

    # Try direct JSON parse
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        # Strip code fences
        fence = re.search(r"```(?:json)?\s*([\s\S]*?)\s*```", text)
        if fence:
            try:
                data = json.loads(fence.group(1))
            except json.JSONDecodeError:
                data = None
        else:
            data = None

    if not data:
        return {
            "best_match_index": 0,
            "confidence": 0.5,
            "reason": "Could not parse match response.",
            "ranked_alternatives": list(range(1, min(4, num_products))),
        }

    # Validate index bounds
    idx = data.get("best_match_index", 0)
    if not isinstance(idx, int) or idx >= num_products:
        idx = 0

    confidence = float(data.get("confidence", 0.5))
    confidence = max(0.0, min(1.0, confidence))

    alts = data.get("ranked_alternatives", [])
    alts = [
        a for a in alts
        if isinstance(a, int) and 0 <= a < num_products and a != idx
    ][:MAX_ALTERNATIVES]

    return {
        "best_match_index": idx,
        "confidence": confidence,
        "reason": str(data.get("reason", "")),
        "ranked_alternatives": alts,
    }


# ---------------------------------------------------------------------------
# Main matching logic
# ---------------------------------------------------------------------------

def _try_shortcut_match(item_name: str, products: list[dict]) -> dict | None:
    """
    Return the top product if it's an obvious match, skipping the Claude call.
    Returns None when the choice is ambiguous and Claude should arbitrate.

    Triggers:
      - Exactly 1 search result → use it.
      - All words from item_name appear in the top product's name, it's in stock,
        and no OTHER top-5 result has all those words (no ambiguity).
    """
    import re

    if not products:
        return None

    # Single-result shortcut
    if len(products) == 1 and products[0].get("in_stock"):
        return products[0]

    # Tokenise the item name into significant words (drop tiny stopwords)
    words = [w for w in re.findall(r"[a-z0-9]+", item_name.lower()) if len(w) > 2]
    if not words:
        return None

    def has_all_words(prod: dict) -> bool:
        haystack = f"{prod.get('brand','')} {prod.get('product_name','')}".lower()
        return all(re.search(rf"\b{re.escape(w)}\b", haystack) for w in words)

    top = products[0]
    if not (top.get("in_stock") and has_all_words(top)):
        return None

    # Ambiguity check — if any of the next 4 also fully contain the item name,
    # we don't know which the user wants. Hand off to Claude.
    for other in products[1:5]:
        if has_all_words(other):
            return None

    return top


def _adjust_quantity_for_pack_size(item_quantity: float, product: dict) -> float:
    """
    Convert a count of individual units into the number of multi-pack packages
    needed. E.g. "4 eggs" matched to a 12-pack → 1 carton; "24 eggs" → 2 cartons.

    Returns item_quantity unchanged if the product isn't a recognisable pack
    (single unit, or no count phrase like "12 ct" / "6 pack" in the title/size).
    """
    import math
    import re
    if item_quantity <= 1:
        return item_quantity
    product_name = (product.get("product_name","") + " " + product.get("size","")).lower()
    match = re.search(r"(\d+)\s*(?:ct|count|pack|pk|piece|pc)\b", product_name)
    if not match:
        return item_quantity
    pack_count = int(match.group(1))
    if pack_count < 2:
        return item_quantity
    # Round up: 4 eggs / 12-pack → 1 carton; 24 / 12 → 2; 13 / 12 → 2.
    return float(math.ceil(item_quantity / pack_count))


def _match_single_item(
    item: dict,
    preferences: dict,
    location_id: str,
    token: str,
) -> dict:
    """
    Match a single parsed item to a Kroger product.
    Returns the item dict enriched with match fields.
    """
    item_key = item["item_key"]
    item_name = item["item_name"]
    saved_pref = preferences.get(item_key)

    result = {**item}  # Copy all original item fields

    # -------------------------------------------------------------------
    # Case 1: Saved preference exists — look up by UPC
    # -------------------------------------------------------------------
    if saved_pref and saved_pref.get("kroger_upc"):
        upc = saved_pref["kroger_upc"]
        preferred_product = _get_product_by_upc(upc, location_id, token)

        if preferred_product:
            # Also search for alternatives (same keyword search)
            search_results = _search_kroger_products(item_name, location_id, token)

            # Build alternatives: exclude the preferred UPC, take top 3
            alternatives = [
                p for p in search_results
                if p["upc"] != upc
            ][:MAX_ALTERNATIVES]

            if preferred_product["in_stock"]:
                adjusted_qty = _adjust_quantity_for_pack_size(item.get("quantity", 1), preferred_product)
                result.update({
                    "match_type":   MATCH_PREFERRED,
                    "confidence":   1.0,
                    "primary":      preferred_product,
                    "alternatives": alternatives,
                    "match_reason": f"Saved preference: {saved_pref['product_name']}",
                    "quantity":     adjusted_qty,
                })
            else:
                # Preferred item is OOS — promote best alternative
                in_stock_alts = [p for p in search_results if p["in_stock"] and p["upc"] != upc]

                if in_stock_alts:
                    substitute = in_stock_alts[0]
                    remaining_alts = in_stock_alts[1:MAX_ALTERNATIVES + 1]
                    # Put the OOS preferred back in alternatives with label
                    preferred_product["_oos_preferred"] = True
                    remaining_alts.insert(0, preferred_product)
                    remaining_alts = remaining_alts[:MAX_ALTERNATIVES]

                    result.update({
                        "match_type":   MATCH_PREFERRED_OOS,
                        "confidence":   0.85,
                        "primary":      substitute,
                        "alternatives": remaining_alts,
                        "match_reason": (
                            f"Your preferred {saved_pref['brand']} is out of stock. "
                            f"Showing next best match."
                        ),
                    })
                else:
                    # Nothing in stock at all
                    result.update({
                        "match_type":   MATCH_PREFERRED_OOS,
                        "confidence":   0.0,
                        "primary":      None,
                        "alternatives": [preferred_product],
                        "match_reason": (
                            f"Your preferred {saved_pref['brand']} is out of stock "
                            f"and no substitutes are available."
                        ),
                    })
            return result

        # UPC lookup returned nothing — fall through to keyword search
        print(f"  ⚠ Preferred UPC {upc} not found for '{item_name}' — falling back to search")

    # -------------------------------------------------------------------
    # Case 2: No preference (or UPC lookup failed) — keyword search + Claude
    # -------------------------------------------------------------------
    search_results = _search_kroger_products(item_name, location_id, token)

    if not search_results:
        result.update({
            "match_type":   MATCH_NOT_FOUND,
            "confidence":   0.0,
            "primary":      None,
            "alternatives": [],
            "match_reason": f"No Kroger products found for '{item_name}'.",
        })
        return result

    # Skip Claude when the top result is obviously the right answer.
    shortcut = _try_shortcut_match(item_name, search_results)
    if shortcut is not None:
        adjusted_qty = _adjust_quantity_for_pack_size(item.get("quantity", 1), shortcut)
        alternatives = [p for p in search_results if p["upc"] != shortcut["upc"]][:MAX_ALTERNATIVES]
        result.update({
            "match_type":   MATCH_BEST,
            "confidence":   0.9,
            "primary":      shortcut,
            "alternatives": alternatives,
            "match_reason": "Top Kroger result matches item name unambiguously.",
            "quantity":     adjusted_qty,
        })
        return result

    # Ask Claude to pick the best match — top 5 only (search is relevance-sorted).
    match = _claude_select_best_match(
        item_name=item_name,
        quantity=item.get("quantity", 1.0),
        unit=item.get("unit", ""),
        notes=item.get("notes", ""),
        products=search_results[:5],
    )

    best_idx = match["best_match_index"]
    confidence = match["confidence"]

    if best_idx == -1:
        # Claude said nothing is a good match
        result.update({
            "match_type":   MATCH_NOT_FOUND,
            "confidence":   0.0,
            "primary":      None,
            "alternatives": search_results[:MAX_ALTERNATIVES],
            "match_reason": match["reason"],
        })
        return result

    primary = search_results[best_idx]
    alternatives = [search_results[i] for i in match["ranked_alternatives"] if i < len(search_results)]

    match_type = MATCH_BEST if confidence >= CONFIDENCE_THRESHOLD else MATCH_NEEDS_PICK

    # Adjust quantity if product is a pack that already satisfies the requested count
    adjusted_qty = _adjust_quantity_for_pack_size(item.get("quantity", 1), primary)

    result.update({
        "match_type":   match_type,
        "confidence":   confidence,
        "primary":      primary,
        "alternatives": alternatives,
        "match_reason": match["reason"],
        "quantity":     adjusted_qty,
    })
    return result


def search_kroger_for_review(query: str, location_id: str, token: str) -> list:
    """
    Public wrapper for manual product search from the review screen.
    Returns up to 10 normalised product dicts, or empty list on failure.
    """
    return _search_kroger_products(query, location_id, token, limit=10)


def match_items(parsed_items: list) -> list:
    """
    Match a list of parsed grocery items to Kroger products.

    Args:
        parsed_items: List of item dicts from list_parser.parse_grocery_list()
                      (or from the staples trigger — same structure).

    Returns:
        List of MatchedItem dicts, one per input item, in the same order.
        Each dict contains all original item fields plus:
            match_type, confidence, primary, alternatives, match_reason

    Raises:
        RuntimeError: If Kroger credentials or location ID are missing.
    """
    location_id = os.getenv("KROGER_LOCATION_ID", "").strip()
    if not location_id or location_id == "your_store_location_id_here":
        raise RuntimeError(
            "KROGER_LOCATION_ID is not set in your .env file. "
            "Complete the store location setup first (Settings → Find My Store)."
        )

    # Get a fresh Kroger token (auto-refreshes if needed)
    token = get_valid_token()

    # Load all preferences once (avoid repeated disk reads)
    preferences = get_all_preferences()

    print(
        f"Matching {len(parsed_items)} items against Kroger catalog at "
        f"location {location_id} ({MATCH_WORKERS} workers)..."
    )

    # Parallel per-item matching. Order is preserved by submitting in input
    # order and relying on executor.map.
    with ThreadPoolExecutor(max_workers=MATCH_WORKERS) as pool:
        matched = list(pool.map(
            lambda item: _match_single_item(item, preferences, location_id, token),
            parsed_items,
        ))

    # Single summary print (per-item logging from worker threads is noisy)
    summary = {}
    for m in matched:
        mt = m.get("match_type", "Unknown")
        summary[mt] = summary.get(mt, 0) + 1
    print(f"Matching complete. {len(matched)} items: " + ", ".join(f"{v} {k}" for k, v in summary.items()))
    return matched


# ---------------------------------------------------------------------------
# Summary helpers (used by UI)
# ---------------------------------------------------------------------------

def match_summary(matched_items: list) -> dict:
    """
    Returns counts by match type for display on the review screen header.
    """
    counts = {
        MATCH_PREFERRED:     0,
        MATCH_PREFERRED_OOS: 0,
        MATCH_BEST:          0,
        MATCH_NEEDS_PICK:    0,
        MATCH_NOT_FOUND:     0,
    }
    for item in matched_items:
        mt = item.get("match_type", "")
        if mt in counts:
            counts[mt] += 1
    return counts


# ---------------------------------------------------------------------------
# Size parsing and cost-per-oz helpers (used by UI)
# ---------------------------------------------------------------------------

import re as _re

# Conversion table: everything to ounces
_TO_OZ = {
    "oz":    1.0,
    "fl oz": 1.0,
    "floz":  1.0,
    "fl":    1.0,
    "lb":    16.0,
    "lbs":   16.0,
    "pound": 16.0,
    "pounds":16.0,
    "g":     0.03527396,
    "gram":  0.03527396,
    "grams": 0.03527396,
    "kg":    35.27396,
    "ml":    0.033814,
    "l":     33.814,
    "liter": 33.814,
    "litre": 33.814,
}


def parse_size_to_oz(size_str: str) -> float | None:
    """
    Parse a Kroger size string into a total-ounce float.
    Returns None if the size can't be converted (e.g. "1 ct", "variety").

    Examples:
        "32 oz"       -> 32.0
        "1 lb"        -> 16.0
        "6 oz"        ->  6.0
        "1.5 lbs"     -> 24.0
        "750 ml"      -> 25.36
        "12 ct"       -> None
        "1 each"      -> None
    """
    if not size_str:
        return None
    s = size_str.lower().strip()

    # Match patterns like "32 oz", "1.5 lbs", "750ml", "1 lb 4 oz"
    # Try compound "X lb Y oz" first
    compound = _re.search(
        r"(\d+(?:\.\d+)?)\s*lb[s]?\s+(\d+(?:\.\d+)?)\s*oz", s
    )
    if compound:
        return float(compound.group(1)) * 16.0 + float(compound.group(2))

    # Simple "number unit"
    simple = _re.search(
        r"(\d+(?:\.\d+)?)\s*"
        r"(fl\s*oz|floz|lbs?|pounds?|kg|grams?|g|ml|liters?|litres?|l\b|oz)",
        s
    )
    if simple:
        amount = float(simple.group(1))
        unit   = _re.sub(r"\s+", "", simple.group(2))  # collapse "fl oz" -> "floz"
        factor = _TO_OZ.get(unit)
        if factor:
            return round(amount * factor, 4)

    return None


def parse_weight_from_notes(notes_str: str) -> float | None:
    """
    Extract a weight in ounces from a parsed item's notes field.
    Notes for butcher items look like "1.5 lbs", "2 lbs", "~6 oz each".
    Returns total oz, or None if not parseable.
    """
    return parse_size_to_oz(notes_str)


def cost_per_oz(product: dict) -> float | None:
    """
    Calculate cost per oz for a Kroger product dict.
    Uses promo_price if on sale, otherwise regular price.
    Returns None if size can't be parsed or price is missing.
    """
    price = product.get("promo_price") or product.get("price")
    if not price:
        return None
    size_oz = parse_size_to_oz(product.get("size", ""))
    if not size_oz or size_oz <= 0:
        return None
    return price / size_oz


def suggested_quantity(item_notes: str, product: dict) -> int | None:
    """
    For protein/butcher items: calculate how many units of `product`
    are needed to cover the weight requested in `item_notes`.
    Rounds up so the user doesn't under-buy.

    Returns None if either weight can't be determined.

    Examples:
        item_notes="1.5 lbs", product size="6 oz"  -> ceil(24/6)  = 4
        item_notes="2 lbs",   product size="12 oz" -> ceil(32/12) = 3
        item_notes="1.5 lbs", product size="1.5 lbs" -> 1  (exact match)
    """
    import math
    needed_oz  = parse_weight_from_notes(item_notes)
    product_oz = parse_size_to_oz(product.get("size", ""))
    if not needed_oz or not product_oz:
        return None
    return max(1, math.ceil(needed_oz / product_oz))


# ---------------------------------------------------------------------------
# CLI entry point — run directly to test matching
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import sys
    from list_parser import parse_grocery_list

    TEST_LIST = "whole milk 1 gallon, large eggs 1 dozen, bananas, chicken breast 2 lbs, olive oil"

    print("SmartCart — Product Matcher Test")
    print("-" * 40)

    location_id = os.getenv("KROGER_LOCATION_ID", "")
    if not location_id or location_id == "your_store_location_id_here":
        print("✗ KROGER_LOCATION_ID not set in .env")
        print("  Complete store setup first: python3 kroger_auth.py")
        sys.exit(1)

    print(f"Store location: {location_id}")
    print(f"Test list: {TEST_LIST}\n")

    try:
        parse_result = parse_grocery_list(TEST_LIST)
        print(f"Parsed {parse_result['item_count']} items. Starting match...\n")

        matched = match_items(parse_result["items"])

        print("\n--- Match Results ---")
        for item in matched:
            name = item["item_name"]
            mt = item["match_type"]
            primary = item.get("primary")

            if primary:
                product_str = f"{primary['brand']} {primary['product_name']} {primary['size']}"
                price_str = f"${primary['price']:.2f}" if primary["price"] else "no price"
                print(f"  {name:25} → [{mt}] {product_str} ({price_str})")
            else:
                print(f"  {name:25} → [{mt}]")

        summary = match_summary(matched)
        print(f"\nSummary: {summary}")

    except (RuntimeError, ValueError) as e:
        print(f"\n✗ Error: {e}")
        sys.exit(1)