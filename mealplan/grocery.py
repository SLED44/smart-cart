"""
mealplan/grocery.py
-------------------
Aggregate the ingredients of a confirmed meal plan into a SmartCart-ready
grocery list.

Public surface:
    aggregate_grocery_list(recipe_ids, household_size, library=None)
        -> list[Item]
    AISLE_MAP                # Spoonacular aisle -> SmartCart category

The returned Item dicts are byte-compatible with what list_parser produces,
so the meal-plan hand-off can drop them straight into
``st.session_state.parsed_result["items"]`` and the rest of the SmartCart
pipeline (preview → item_filter → matcher → cart_post) keeps working
without any further translation.

V1 scope decisions (per PRD §12.4):
    - Sum quantities only when (canonical_name, unit) match exactly. If
      two recipes call for "milk" in different units, they stay as separate
      line items. User reconciles on preview.
    - Aisle → category mapping is a static dict (AISLE_MAP below). Unmapped
      aisles default to "Pantry".
    - has_preference is checked against preference_store so the matcher's
      auto-confirm path lights up for ingredients the user already has a
      preferred product for.
"""

import re

from preference_store import normalise_item_key, get_preference
from mealplan import library as _library


# ---------------------------------------------------------------------------
# Spoonacular aisle → SmartCart category (PRD §12.3)
# ---------------------------------------------------------------------------
# SmartCart's 8-category schema (from preference_store):
#   Dairy, Produce, Meat, Frozen, Pantry, Beverages, Bakery, Household
#   (+ "Personal Care" and "Other" as catch-alls)
#
# Spoonacular's aisle values are free-form-ish strings; lower-cased substring
# lookup against this list is the matching strategy. First hit wins.
AISLE_MAP = {
    # Meat / Seafood
    "meat":                 "Meat",
    "seafood":              "Meat",
    "fish":                 "Meat",
    # Dairy / eggs
    "milk":                 "Dairy",
    "eggs":                 "Dairy",
    "cheese":               "Dairy",
    "dairy":                "Dairy",
    "refrigerated":         "Dairy",
    # Produce
    "produce":              "Produce",
    "vegetable":            "Produce",
    "fruit":                "Produce",
    # Frozen
    "frozen":               "Frozen",
    # Bakery
    "bread":                "Bakery",
    "bakery":               "Bakery",
    # Beverages
    "beverage":             "Beverages",
    "tea":                  "Beverages",
    "coffee":               "Beverages",
    "alcohol":              "Beverages",
    "wine":                 "Beverages",
    "beer":                 "Beverages",
    # Household
    "cleaning":             "Household",
    "paper":                "Household",
    "household":            "Household",
    # Pantry (largest bucket — keep last so more specific matches win)
    "spice":                "Pantry",
    "seasoning":            "Pantry",
    "canned":               "Pantry",
    "jarred":               "Pantry",
    "pasta and rice":       "Pantry",
    "baking":               "Pantry",
    "oil":                  "Pantry",
    "vinegar":              "Pantry",
    "salad dressing":       "Pantry",
    "condiment":            "Pantry",
    "nut butter":           "Pantry",
    "jam":                  "Pantry",
    "honey":                "Pantry",
    "ethnic":               "Pantry",
    "health food":          "Pantry",
    "pantry":               "Pantry",
}

DEFAULT_CATEGORY = "Pantry"

# SmartCart category sort order (matches the user's rules doc §7a).
_CATEGORY_ORDER = (
    "Produce", "Meat", "Dairy", "Frozen", "Bakery",
    "Pantry", "Beverages", "Household", "Personal Care", "Other",
)


def map_aisle_to_category(aisle: str) -> str:
    """Best-effort Spoonacular → SmartCart category. Unmapped → Pantry."""
    if not aisle:
        return DEFAULT_CATEGORY
    needle = aisle.lower()
    for fragment, category in AISLE_MAP.items():
        if fragment in needle:
            return category
    return DEFAULT_CATEGORY


# ---------------------------------------------------------------------------
# Name canonicalization
# ---------------------------------------------------------------------------

_PAREN_RE = re.compile(r"\s*\([^)]*\)")


def canonicalize_name(name: str) -> str:
    """
    Light cleanup for dedup grouping. Strips parenthetical asides and
    extra whitespace but preserves the rest of the ingredient name so
    "ground beef (80/20)" and "ground beef" merge into one line item.
    """
    if not name:
        return ""
    cleaned = _PAREN_RE.sub("", name).strip().lower()
    cleaned = re.sub(r"\s+", " ", cleaned)
    return cleaned


# ---------------------------------------------------------------------------
# Aggregator
# ---------------------------------------------------------------------------

def aggregate_grocery_list(
    recipe_ids: list[str],
    household_size: int,
    library=None,
) -> list[dict]:
    """
    Collapse N recipes' ingredients into a SmartCart Item list.

    Args:
        recipe_ids:     recipe ids to aggregate (from current_plan.meals)
        household_size: scale recipes from their servings_original up/down
                        to this size (default 4 per PRD §7.1)
        library:        injected for testability; defaults to the real
                        mealplan.library module

    Returns:
        list[Item] with the SmartCart Item shape used by list_parser:
            {
                "item_name":      str,
                "item_key":       str,    # normalised
                "quantity":       float,  # scaled + summed
                "unit":           str,
                "category":       str,    # SmartCart's 8-category schema
                "notes":          str,    # source recipes, joined with ", "
                "has_preference": bool,
                "source":         "meal_plan",
                "aisle":          str,    # original Spoonacular aisle (debug)
            }

    Items are sorted by category (Produce → Meat → ... → Other), then by
    item_name within each category.
    """
    lib = library if library is not None else _library
    raw_items: list[dict] = []

    for rid in recipe_ids:
        recipe = lib.get(rid) if hasattr(lib, "get") else lib.get_all().get(rid)
        if not recipe:
            continue
        original_servings = int(recipe.get("servings_original") or 4) or 4
        scale = household_size / original_servings
        recipe_title = recipe.get("title", rid)
        for ing in (recipe.get("ingredients") or []):
            # Spoonacular emits unit="servings" for amountless rows: to-taste
            # items ("salt and pepper") and section headers that leaked into
            # the ingredient list ("Salad", "Pea-mond Dressing"). Neither
            # belongs on a shopping list.
            if (ing.get("unit") or "").strip().lower() in ("serving", "servings"):
                continue
            raw_items.append({
                "canonical":    canonicalize_name(ing.get("name", "")),
                "display_name": (ing.get("name") or "").strip(),
                "amount":       float(ing.get("amount") or 0) * scale,
                "unit":         (ing.get("unit") or "").strip().lower(),
                "aisle":        ing.get("aisle") or "",
                "source":       recipe_title,
            })

    # Dedup + sum by (canonical_name, unit). Keep first display_name + first
    # aisle seen so the user sees something sensible.
    merged: dict[tuple[str, str], dict] = {}
    for item in raw_items:
        key = (item["canonical"], item["unit"])
        if key in merged:
            merged[key]["amount"] += item["amount"]
            if item["source"] not in merged[key]["sources"]:
                merged[key]["sources"].append(item["source"])
        else:
            merged[key] = {
                "canonical":    item["canonical"],
                "display_name": item["display_name"],
                "amount":       item["amount"],
                "unit":         item["unit"],
                "aisle":        item["aisle"],
                "sources":      [item["source"]],
            }

    # Build SmartCart Item dicts.
    out: list[dict] = []
    for m in merged.values():
        category = map_aisle_to_category(m["aisle"])
        item_name = m["display_name"] or m["canonical"] or "(unknown)"
        item_key = normalise_item_key(m["canonical"] or item_name)
        try:
            has_pref = get_preference(item_key) is not None
        except Exception:
            has_pref = False
        out.append({
            "item_name":      item_name,
            "item_key":       item_key,
            "quantity":       _round_qty(m["amount"]),
            "unit":           m["unit"],
            "category":       category,
            "notes":          ", ".join(m["sources"]),
            "has_preference": has_pref,
            "source":         "meal_plan",
            "aisle":          m["aisle"],
        })

    out.sort(key=lambda i: (_category_sort_key(i["category"]), i["item_name"].lower()))
    return out


def _round_qty(q: float) -> float:
    """Trim noisy fractional scaling to 2 dp (e.g. 0.6666666 → 0.67)."""
    if q == int(q):
        return float(int(q))
    return round(q, 2)


def _category_sort_key(cat: str) -> int:
    try:
        return _CATEGORY_ORDER.index(cat)
    except ValueError:
        return len(_CATEGORY_ORDER)  # unknown categories go last


# ---------------------------------------------------------------------------
# Helper for the active screen — builds the parsed_result blob
# ---------------------------------------------------------------------------

def build_parsed_result(items: list[dict]) -> dict:
    """
    Wrap a list of aggregated Items in the same shape list_parser produces,
    so the existing SmartCart preview/match pipeline reads it unchanged.
    """
    return {
        "items":          items,
        "raw_text":       "<generated-from-meal-plan>",
        "item_count":     len(items),
        "parse_warnings": [],
    }
