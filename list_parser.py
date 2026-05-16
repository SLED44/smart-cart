"""
list_parser.py
--------------
Accepts raw grocery list text in any format and normalises it into a
structured item list via the Claude API (Haiku model).

Public interface:
    parse_grocery_list(raw_text) -> ParseResult
    validate_parsed_items(items)  -> list[dict]   (correct obvious errors)

ParseResult is a dict:
    {
        "items": [
            {
                "item_name":    str,    Human-readable name e.g. "Chicken Breast"
                "item_key":     str,    Normalised key e.g. "chicken_breast"
                "quantity":     float,  1.0 if not specified
                "unit":         str,    "lbs", "oz", "count", "" etc.
                "category":     str,    "Produce", "Dairy", "Meat" etc.
                "notes":        str,    Any qualifier e.g. "organic", "boneless"
                "has_preference": bool  True if a saved preference exists
            },
            ...
        ],
        "raw_text":       str,   The original input (preserved for display)
        "item_count":     int,   Total number of parsed items
        "parse_warnings": list   Any items Claude flagged as ambiguous
    }

Module boundary (PRD LI-07):
    This module accepts a raw string and returns structured JSON.
    It has no UI dependency and can be called by any future module
    (meal planner, file upload handler, etc.) without modification.
"""

import json
import os
import re

import anthropic
from dotenv import load_dotenv

from preference_store import get_all_preferences, normalise_item_key

load_dotenv(override=True)

# ---------------------------------------------------------------------------
# Claude client
# ---------------------------------------------------------------------------

def _get_claude_client() -> anthropic.Anthropic:
    api_key = os.getenv("ANTHROPIC_API_KEY", "")
    if not api_key:
        raise RuntimeError(
            "ANTHROPIC_API_KEY is not set in your .env file. "
            "See README.md → Step 5 for Anthropic API setup."
        )
    return anthropic.Anthropic(api_key=api_key)


# ---------------------------------------------------------------------------
# Prompt
# ---------------------------------------------------------------------------

SYSTEM_PROMPT = """You are a grocery list parser for a household shopping app.
Your job is to convert raw grocery list text into a structured JSON list.

Rules:
- Extract every distinct grocery item mentioned
- Preserve category labels if present in the input (Produce, Dairy, Meat, Frozen, Bakery, Pantry, Beverages, Household, Personal Care, Other)
- If no category is given for an item, infer the most likely one from the item name
- Normalise item names to title case, singular form, without brand names
  (brand preferences are stored separately — just the generic item name)
- If an item is ambiguous or unclear, still include it and add a note in "warnings"
- Do not add items that were not in the original list
- Do not merge items that appear to be distinct (e.g. "red onion" and "yellow onion" stay separate)

Quantity rules — read carefully:
- For items sold by COUNT (eggs, apples, cans, bags, bottles, fillets, portions, pieces):
  quantity = the count number, unit = "" or "count"
  Example: "4 salmon fillets" -> quantity: 4, unit: "count"
- For items sold by WEIGHT at the deli or butcher (ground beef, chicken breast, pork tenderloin, steak):
  quantity = 1, unit = "", notes must include the weight e.g. "1.5 lbs"
  Example: "1.5 lbs pork tenderloin" -> quantity: 1, unit: "", notes: "1.5 lbs"
  Example: "2 lbs ground beef" -> quantity: 1, unit: "", notes: "2 lbs"
- For items sold by VOLUME (milk, broth, juice, olive oil):
  quantity = the number of containers, unit = the container size
  Example: "1 gallon whole milk" -> quantity: 1, unit: "gallon"
  Example: "32 oz chicken broth" -> quantity: 1, unit: "32 oz"
- Always preserve size or weight qualifiers in notes even if already captured in quantity/unit
  Example: "4 salmon portions ~6 oz each" -> quantity: 4, unit: "count", notes: "~6 oz each"
- Extract all other qualifiers into notes (organic, boneless, low-fat, extra-large, etc.)

Respond ONLY with a valid JSON object in exactly this format — no preamble, no explanation:
{
  "items": [
    {
      "item_name": "Chicken Breast",
      "quantity": 2.0,
      "unit": "lbs",
      "category": "Meat",
      "notes": "boneless skinless"
    }
  ],
  "warnings": []
}

The "warnings" array should contain plain-English strings describing any items
that were ambiguous, unclear, or that you had to make assumptions about."""

# ---------------------------------------------------------------------------
# Core parsing function
# ---------------------------------------------------------------------------

def parse_grocery_list(raw_text: str) -> dict:
    """
    Parse a raw grocery list string into structured items.

    Args:
        raw_text: Any freeform grocery list text — bullet list, numbered list,
                  prose, categorised sections, CSV, or mixed formats.

    Returns:
        ParseResult dict (see module docstring for full structure).

    Raises:
        RuntimeError: If the Claude API call fails or returns unparseable output.
    """
    if not raw_text or not raw_text.strip():
        raise ValueError("Grocery list cannot be empty.")

    raw_text = raw_text.strip()

    # Call Claude Haiku
    client = _get_claude_client()

    try:
        message = client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=4096,
            system=SYSTEM_PROMPT,
            messages=[
                {
                    "role": "user",
                    "content": f"Parse this grocery list:\n\n{raw_text}",
                }
            ],
        )
    except anthropic.APIConnectionError:
        raise RuntimeError(
            "Could not connect to the Anthropic API. Check your internet connection."
        )
    except anthropic.AuthenticationError:
        raise RuntimeError(
            "Anthropic API key is invalid. Check ANTHROPIC_API_KEY in your .env file."
        )
    except anthropic.RateLimitError:
        raise RuntimeError(
            "Anthropic API rate limit reached. Wait a moment and try again."
        )
    except anthropic.APIError as e:
        raise RuntimeError(f"Anthropic API error: {e}")

    # Extract text content from response
    response_text = ""
    for block in message.content:
        if hasattr(block, "text"):
            response_text += block.text

    # Parse JSON response
    parsed = _extract_json(response_text)
    if parsed is None:
        raise RuntimeError(
            f"Claude returned an unexpected response format. "
            f"Raw response: {response_text[:200]}"
        )

    # Validate and enrich items
    items = parsed.get("items", [])
    warnings = parsed.get("warnings", [])

    if not items:
        raise RuntimeError(
            "No grocery items were found in the list. "
            "Please check your input and try again."
        )

    # Enrich each item with normalised key and preference flag
    enriched_items = _enrich_items(items)

    return {
        "items":          enriched_items,
        "raw_text":       raw_text,
        "item_count":     len(enriched_items),
        "parse_warnings": warnings,
    }


# ---------------------------------------------------------------------------
# JSON extraction
# ---------------------------------------------------------------------------

def _extract_json(text: str) -> dict | None:
    """
    Extract a JSON object from Claude's response text.
    Handles cases where Claude wraps the JSON in markdown code fences.
    """
    text = text.strip()

    # Try direct parse first
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass

    # Strip markdown code fences if present
    fence_match = re.search(r"```(?:json)?\s*([\s\S]*?)\s*```", text, re.DOTALL)
    if fence_match:
        try:
            return json.loads(fence_match.group(1).strip())
        except json.JSONDecodeError:
            pass

    # Last resort: find the first { ... } block
    brace_match = re.search(r"\{[\s\S]*\}", text)
    if brace_match:
        try:
            return json.loads(brace_match.group(0))
        except json.JSONDecodeError:
            pass

    return None


# ---------------------------------------------------------------------------
# Item enrichment
# ---------------------------------------------------------------------------

def _enrich_items(raw_items: list) -> list:
    """
    Add item_key and has_preference fields to each parsed item.
    Validates and normalises field types.
    """
    saved_preferences = get_all_preferences()
    enriched = []

    for item in raw_items:
        # Ensure all expected fields are present with sensible defaults
        item_name = str(item.get("item_name", "Unknown Item")).strip()
        if not item_name:
            continue  # Skip blank items

        item_key = normalise_item_key(item_name)

        # Normalise quantity to float
        qty = item.get("quantity", 1)
        try:
            quantity = float(qty)
            if quantity <= 0:
                quantity = 1.0
        except (TypeError, ValueError):
            quantity = 1.0

        # Normalise unit to string
        unit = str(item.get("unit", "")).strip().lower()

        # Normalise category
        category = str(item.get("category", "Other")).strip()
        if not category:
            category = "Other"

        # Normalise notes
        notes = str(item.get("notes", "")).strip()

        enriched.append({
            "item_name":      item_name,
            "item_key":       item_key,
            "quantity":       quantity,
            "unit":           unit,
            "category":       category,
            "notes":          notes,
            "has_preference": item_key in saved_preferences,
        })

    return enriched


# ---------------------------------------------------------------------------
# Post-parse validation / correction
# ---------------------------------------------------------------------------

def validate_parsed_items(items: list) -> list:
    """
    Apply light corrections to a parsed item list based on common
    Claude parsing quirks. Called after the user has reviewed the
    normalised list and before matching begins.

    Current corrections:
    - Strips any remaining markdown or bullet characters from item names
    - Collapses duplicate items (same item_key) by summing quantities
    - Ensures no item has quantity 0

    Args:
        items: List of enriched item dicts from parse_grocery_list().

    Returns:
        Cleaned item list.
    """
    seen_keys = {}
    cleaned = []

    for item in items:
        # Strip any stray bullet/markdown chars from item name
        name = re.sub(r"^[\s\-\*\•\·]+", "", item["item_name"]).strip()
        if not name:
            continue
        item["item_name"] = name
        item["item_key"] = normalise_item_key(name)

        # Ensure quantity is at least 1
        if item.get("quantity", 0) <= 0:
            item["quantity"] = 1.0

        # Collapse duplicates by summing quantities
        key = item["item_key"]
        if key in seen_keys:
            seen_keys[key]["quantity"] += item["quantity"]
        else:
            seen_keys[key] = item
            cleaned.append(item)

    return cleaned


# ---------------------------------------------------------------------------
# Formatting helpers (used by UI)
# ---------------------------------------------------------------------------

def format_quantity(quantity: float, unit: str) -> str:
    """
    Format a quantity + unit for display.

    Examples:
        (2.0, "lbs")   -> "2 lbs"
        (1.0, "")      -> "1"
        (0.5, "cup")   -> "0.5 cup"
        (12.0, "count")-> "12"
    """
    # Show integer if whole number
    qty_str = str(int(quantity)) if quantity == int(quantity) else str(quantity)

    if not unit or unit in ("count", "each", "ea"):
        return qty_str
    return f"{qty_str} {unit}"


def group_items_by_category(items: list) -> dict:
    """
    Group a list of items by category for display.

    Returns:
        OrderedDict-style dict: { "Produce": [...], "Dairy": [...], ... }
        Categories sorted in standard grocery store aisle order.
    """
    # Standard aisle order for display
    category_order = [
        "Produce", "Meat", "Seafood", "Dairy", "Frozen",
        "Bakery", "Pantry", "Beverages", "Household", "Personal Care", "Other"
    ]

    grouped = {}
    for item in items:
        cat = item.get("category", "Other")
        if cat not in grouped:
            grouped[cat] = []
        grouped[cat].append(item)

    # Return in standard aisle order, with any unexpected categories at the end
    ordered = {}
    for cat in category_order:
        if cat in grouped:
            ordered[cat] = grouped[cat]
    for cat in grouped:
        if cat not in ordered:
            ordered[cat] = grouped[cat]

    return ordered


# ---------------------------------------------------------------------------
# CLI entry point — run directly to test parsing
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import sys

    TEST_LIST = """
    Produce:
    - 2 lbs chicken breast (boneless skinless)
    - 1 dozen large eggs
    - 2 lbs bananas
    - organic baby spinach
    - 3 roma tomatoes

    Dairy:
    - whole milk, 1 gallon
    - salted butter (2 sticks)
    - shredded mozzarella cheese

    Pantry:
    - olive oil
    - 2 cans diced tomatoes (14.5 oz)
    - pasta, 1 lb
    - chicken broth, 32 oz

    Frozen:
    - 1 bag frozen peas
    """

    print("SmartCart — List Parser Test")
    print("-" * 40)

    if "--list" in sys.argv:
        # Read from stdin or next arg
        idx = sys.argv.index("--list")
        if idx + 1 < len(sys.argv):
            raw = sys.argv[idx + 1]
        else:
            print("Paste your grocery list (Ctrl+D when done):")
            raw = sys.stdin.read()
    else:
        print("Using built-in test list. Pass --list 'your list' to test custom input.\n")
        raw = TEST_LIST

    try:
        result = parse_grocery_list(raw)

        print(f"✓ Parsed {result['item_count']} items\n")

        grouped = group_items_by_category(result["items"])
        for category, items in grouped.items():
            print(f"  {category}:")
            for item in items:
                qty = format_quantity(item["quantity"], item["unit"])
                pref = " ★" if item["has_preference"] else ""
                notes = f" ({item['notes']})" if item["notes"] else ""
                print(f"    • {qty} {item['item_name']}{notes}{pref}")
        print()

        if result["parse_warnings"]:
            print("⚠ Warnings:")
            for w in result["parse_warnings"]:
                print(f"  - {w}")

    except (RuntimeError, ValueError) as e:
        print(f"✗ Error: {e}")
        sys.exit(1)
