"""
mealplan/spoonacular.py
-----------------------
Spoonacular API client + recipe normalization.

Public interface:
    search(...)            -> list[dict]   (normalized Recipe dicts)
    get_recipe(sp_id)      -> dict
    points_used_today()    -> int
    points_remaining_today(daily_cap=150) -> int

All returned recipes conform to the schema in PRD §7.2 and can be passed
straight into ``mealplan.library.save()``.

Key conventions, per PRD §14:
    - Always: addRecipeInformation=true, instructionsRequired=true,
      fillIngredients=true, addRecipeNutrition=false
    - ``mild=True`` filters out titles containing 'spicy', 'hot', 'fire',
      'ghost', 'habanero' (case-insensitive). Done client-side — Spoonacular
      doesn't expose a spice-level filter.
    - Points budget tracked in ``spoonacular_usage`` KV key (resets daily).
      Free tier ceiling is 150/day.

Spoonacular point cost (approximation):
    complexSearch w/ addRecipeInformation=true:  ~ 1 + (number of results)
    /recipes/{id}/information:                   ~ 1
"""

import os
import re
from datetime import datetime, timezone
from typing import Any, Iterable

import requests

from supabase_kv import kv_get, kv_put

API_BASE = "https://api.spoonacular.com"
DEFAULT_TIMEOUT = 20
DAILY_POINT_CAP = 150

KEY_USAGE = "spoonacular_usage"

SPICY_TITLE_KEYWORDS = ("spicy", "hot", "fire", "ghost", "habanero")


# ---------------------------------------------------------------------------
# Auth + transport
# ---------------------------------------------------------------------------

def _require_key() -> str:
    key = os.getenv("SPOONACULAR_API_KEY", "").strip()
    if not key:
        raise RuntimeError(
            "SPOONACULAR_API_KEY is not set. Add it to your .env (local) or "
            "Streamlit Cloud secrets."
        )
    return key


def _today_str() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _record_points(cost: int) -> None:
    """Increment today's points-used counter. Resets on date rollover."""
    today = _today_str()
    usage = kv_get(KEY_USAGE, {}) or {}
    if usage.get("date") != today:
        usage = {"date": today, "points": 0}
    usage["points"] = int(usage.get("points", 0)) + int(cost)
    usage["updated_at"] = _now_iso()
    kv_put(KEY_USAGE, usage)


def points_used_today() -> int:
    usage = kv_get(KEY_USAGE, {}) or {}
    if usage.get("date") != _today_str():
        return 0
    return int(usage.get("points", 0))


def points_remaining_today(daily_cap: int = DAILY_POINT_CAP) -> int:
    return max(0, daily_cap - points_used_today())


def _request(endpoint: str, params: dict, estimated_cost: int) -> dict:
    """Wrap requests.get with auth, timeout, and points tracking."""
    params = dict(params)
    params["apiKey"] = _require_key()
    url = f"{API_BASE}{endpoint}"
    r = requests.get(url, params=params, timeout=DEFAULT_TIMEOUT)
    # Use Spoonacular's own X-API-Quota-Used header if present (authoritative),
    # else fall back to our estimate.
    used_header = r.headers.get("X-API-Quota-Request")
    try:
        cost = int(float(used_header)) if used_header else estimated_cost
    except (TypeError, ValueError):
        cost = estimated_cost
    _record_points(max(1, cost))

    if r.status_code == 402:
        raise RuntimeError("Spoonacular daily point quota exhausted (HTTP 402).")
    if not r.ok:
        raise RuntimeError(
            f"Spoonacular {endpoint} HTTP {r.status_code}: {r.text[:200]}"
        )
    return r.json()


# ---------------------------------------------------------------------------
# Public search
# ---------------------------------------------------------------------------

def search(
    cuisine: str | list[str] | None = None,
    protein: str | None = None,                  # → includeIngredients
    diet: str | None = None,                     # e.g. "vegetarian"
    query: str | None = None,                    # free-text title search
    max_ready: int = 60,
    mild: bool = True,
    sort: str = "popularity",                    # "popularity" | "random" | ...
    number: int = 5,
    exclude_ingredients: Iterable[str] | None = None,
    dish_type: str = "main course",
) -> list[dict]:
    """
    Return up to ``number`` normalized Recipe dicts matching the filters.

    Caller is responsible for passing the returned dicts to
    ``mealplan.library.save()`` if they want to cache them.

    Mild filter is applied client-side, so the returned count may be less
    than ``number`` even when Spoonacular returned a full page.
    """
    params: dict[str, Any] = {
        "addRecipeInformation": "true",
        "instructionsRequired": "true",
        "fillIngredients":      "true",
        "addRecipeNutrition":   "false",
        "type":                 dish_type,
        "maxReadyTime":         max_ready,
        "sort":                 sort,
        "number":               number,
    }
    if cuisine:
        params["cuisine"] = ",".join(cuisine) if isinstance(cuisine, list) else cuisine
    if protein:
        params["includeIngredients"] = protein
    if diet:
        params["diet"] = diet
    if query:
        params["query"] = query
    if exclude_ingredients:
        params["excludeIngredients"] = ",".join(exclude_ingredients)

    estimated = 1 + int(number)
    data = _request("/recipes/complexSearch", params, estimated_cost=estimated)

    raw = data.get("results", []) or []
    recipes = [_normalize_recipe(item) for item in raw]
    if mild:
        recipes = [r for r in recipes if not _is_spicy_title(r.get("title", ""))]
    return recipes


def get_recipe(spoonacular_id: int | str) -> dict:
    """
    Fetch a single recipe by Spoonacular id. Used when ``search()`` returned
    a hit without enough detail (rare — ``addRecipeInformation=true`` usually
    covers it) or when re-hydrating from a saved ``source_id``.
    """
    params = {
        "includeNutrition": "false",
    }
    data = _request(
        f"/recipes/{spoonacular_id}/information",
        params,
        estimated_cost=1,
    )
    return _normalize_recipe(data)


# ---------------------------------------------------------------------------
# Normalization
# ---------------------------------------------------------------------------

def _normalize_recipe(payload: dict) -> dict:
    """Map Spoonacular's API payload onto the PRD §7.2 Recipe schema."""
    sp_id = payload.get("id")
    title = payload.get("title", "")
    ingredients = [
        _normalize_ingredient(ing)
        for ing in (payload.get("extendedIngredients") or [])
    ]
    analyzed = payload.get("analyzedInstructions") or []
    instructions = _flatten_instructions(analyzed)
    equipment = _aggregate_equipment(analyzed)

    return {
        # id is filled in by library.save() if not preset
        "source":            "spoonacular",
        "source_id":         str(sp_id) if sp_id is not None else "",
        "source_url":        payload.get("sourceUrl") or payload.get("spoonacularSourceUrl", ""),
        "title":             title,
        "image_url":         payload.get("image", ""),
        "servings_original": int(payload.get("servings") or 0) or 4,
        "ready_in_minutes":  int(payload.get("readyInMinutes") or 0),
        "prep_minutes":      int(payload.get("preparationMinutes") or 0) or None,
        "cook_minutes":      int(payload.get("cookingMinutes") or 0) or None,
        "cuisines":          [c.lower() for c in (payload.get("cuisines") or [])],
        "diet_tags":         list(payload.get("diets") or []),
        "dish_types":        list(payload.get("dishTypes") or []),
        "equipment":         equipment,
        "proteins":          infer_proteins(ingredients),
        "carbs":             infer_carbs(ingredients),
        "ingredients":       ingredients,
        "instructions":      instructions,
    }


def _normalize_ingredient(ing: dict) -> dict:
    measures = ((ing.get("measures") or {}).get("us")) or {}
    amount = measures.get("amount") if measures.get("amount") is not None else ing.get("amount", 0)
    unit = measures.get("unitShort") or ing.get("unit", "") or ""
    return {
        "name":          (ing.get("nameClean") or ing.get("name") or "").lower().strip(),
        "amount":        float(amount or 0),
        "unit":          unit.strip(),
        "aisle":         ing.get("aisle", "") or "",
        "original_text": ing.get("original", ""),
    }


def _flatten_instructions(analyzed: list) -> list[dict]:
    out = []
    step_no = 0
    for block in analyzed:
        for step in block.get("steps") or []:
            step_no += 1
            out.append({
                "step_number": step_no,
                "text":        (step.get("step") or "").strip(),
            })
    return out


def _aggregate_equipment(analyzed: list) -> list[str]:
    seen = []
    for block in analyzed:
        for step in block.get("steps") or []:
            for eq in step.get("equipment") or []:
                name = (eq.get("name") or "").strip().lower().replace(" ", "_")
                if name and name not in seen:
                    seen.append(name)
    return seen


# ---------------------------------------------------------------------------
# Spice + protein/carb classification
# ---------------------------------------------------------------------------

def _is_spicy_title(title: str) -> bool:
    """Word-boundary match against SPICY_TITLE_KEYWORDS (PRD §8.2 step 7)."""
    t = (title or "").lower()
    return any(re.search(rf"\b{kw}\b", t) for kw in SPICY_TITLE_KEYWORDS)


# Keyword → category. First-match-wins for proteins so e.g. "chicken sausage"
# classifies as chicken, not pork.
_PROTEIN_RULES: list[tuple[str, tuple[str, ...]]] = [
    ("chicken", ("chicken",)),
    ("turkey",  ("turkey",)),
    ("shrimp",  ("shrimp", "prawn")),
    ("fish",    ("salmon", "tuna", "cod", "halibut", "tilapia", "trout",
                 "mahi", "sea bass", "snapper", "swordfish", "anchovy",
                 "sardine", "fish")),
    ("lamb",    ("lamb",)),
    ("pork",    ("pork", "bacon", "ham ", "prosciutto", "pancetta",
                 "chorizo", "sausage", "carnitas", "salami", "pepperoni")),
    ("beef",    ("beef", "steak", "brisket", "ribeye", "sirloin",
                 "chuck roast", "flank", "skirt steak", "hanger", "short rib",
                 "ground chuck", "veal")),
    ("plant",   ("tofu", "tempeh", "seitan", "chickpea", "garbanzo",
                 "lentil", "black bean", "kidney bean", "pinto bean",
                 "white bean", "edamame", "soy curl")),
]

# Order matters — rules with the most specific keywords come first so
# compound terms like "rice vermicelli" classify as pasta (not rice) and
# "sweet potato" classifies as potato (not anything else).
_CARB_RULES: list[tuple[str, tuple[str, ...]]] = [
    ("pasta",  ("pasta", "spaghetti", "penne", "fettuccine", "linguine",
                "rigatoni", "macaroni", "lasagna", "noodle", "ravioli",
                "gnocchi", "orzo", "soba", "udon", "vermicelli",
                "rice noodle", "rice vermicelli")),
    ("rice",   ("rice", "basmati", "jasmine", "arborio", "paella")),
    ("bread",  ("bread", "bun", "baguette", "ciabatta", "focaccia",
                "naan", "pita", "tortilla", "flatbread", "roll", "burger bun")),
    ("grain",  ("quinoa", "couscous", "barley", "farro", "bulgur", "oat",
                "polenta")),
    ("potato", ("potato", "fingerling", "yukon", "russet")),
    ("salad",  ("lettuce", "arugula", "romaine", "spinach", "kale",
                "mixed greens", "salad")),
]


def infer_proteins(ingredients: list[dict]) -> list[str]:
    """Return the deduped set of protein categories present in the ingredients."""
    found: list[str] = []
    for ing in ingredients:
        name = (ing.get("name") or "").lower()
        for category, keywords in _PROTEIN_RULES:
            if category in found:
                continue
            if any(kw in name for kw in keywords):
                found.append(category)
                break
    return found


def infer_carbs(ingredients: list[dict]) -> list[str]:
    found: list[str] = []
    for ing in ingredients:
        name = (ing.get("name") or "").lower()
        for category, keywords in _CARB_RULES:
            if category in found:
                continue
            if any(kw in name for kw in keywords):
                found.append(category)
                break
    return found


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import argparse
    import json

    parser = argparse.ArgumentParser(
        description="Hand-test the Spoonacular client. Costs real API points!"
    )
    sub = parser.add_subparsers(dest="cmd")

    p_usage = sub.add_parser("usage", help="Show today's Spoonacular point usage")

    p_search = sub.add_parser("search", help="Run a complexSearch")
    p_search.add_argument("--cuisine")
    p_search.add_argument("--protein")
    p_search.add_argument("--query")
    p_search.add_argument("--number", type=int, default=3)
    p_search.add_argument("--max-ready", type=int, default=60)
    p_search.add_argument("--no-mild", action="store_true",
                          help="Disable mild-spice filter")
    p_search.add_argument("--save", action="store_true",
                          help="Also save results to the library")

    p_get = sub.add_parser("recipe", help="Fetch one recipe by Spoonacular id")
    p_get.add_argument("id")
    p_get.add_argument("--save", action="store_true")

    args = parser.parse_args()

    if args.cmd == "usage":
        print(json.dumps({
            "points_used_today":      points_used_today(),
            "points_remaining_today": points_remaining_today(),
        }, indent=2))
    elif args.cmd == "search":
        results = search(
            cuisine=args.cuisine,
            protein=args.protein,
            query=args.query,
            number=args.number,
            max_ready=args.max_ready,
            mild=not args.no_mild,
        )
        if args.save:
            from mealplan import library
            for r in results:
                rid = library.save(r)
                r["_saved_as"] = rid
        print(json.dumps(results, indent=2, default=str))
        print(f"\n[{len(results)} kept after filtering, "
              f"{points_used_today()}/{DAILY_POINT_CAP} points used today]")
    elif args.cmd == "recipe":
        r = get_recipe(args.id)
        if args.save:
            from mealplan import library
            r["_saved_as"] = library.save(r)
        print(json.dumps(r, indent=2, default=str))
        print(f"\n[{points_used_today()}/{DAILY_POINT_CAP} points used today]")
    else:
        parser.print_help()
