"""
screens/_recipe_view.py
-----------------------
Shared recipe rendering used by the cook screen and by the recipe-preview
popup on the propose/swap screens. Keeping it in one place means a recipe
looks identical everywhere it's shown.

Public surface:
    compute_scale(recipe, rules)        -> float
    fmt_amount(q)                       -> str   (kitchen fractions)
    format_ingredient_line(ing, scale)  -> str
    render_ingredients(recipe, scale)   -> None  (grouped, st.* output)
    render_instructions(recipe)         -> None
    render_recipe_detail(recipe, scale) -> None  (meta + ingredients + steps)
    open_preview(recipe, scale)         -> None  (@st.dialog modal)
"""

import math

import streamlit as st

from sc_design import recipe_tile_html


# ---------------------------------------------------------------------------
# Thumbnail — real photo when present, else the cuisine-tinted plate tile.
# One helper so every card/modal falls back identically. Uses the CSS plate
# (recipe_tile_html), not the SVG art — Streamlit's st.html sanitizer strips
# inline <svg>, which rendered the SVG tiles as blank space.
# ---------------------------------------------------------------------------

def render_thumb(recipe: dict, size: int = 140):
    """Render a recipe thumbnail into the current container: the real
    image_url if the recipe has one, otherwise the cuisine-tinted plate tile
    at `size` px."""
    if recipe and recipe.get("image_url"):
        st.image(recipe["image_url"], width=size)
    else:
        st.html(recipe_tile_html(recipe or {}, size=size))


# ---------------------------------------------------------------------------
# Scaling + amount formatting
# ---------------------------------------------------------------------------

def compute_scale(recipe: dict, rules: dict) -> float:
    """Household size / recipe's original yield (defaults to 1.0)."""
    household = int(((rules.get("household") or {}).get("size")) or 4) or 4
    original = int(recipe.get("servings_original") or 4) or 4
    return household / original


_KITCHEN_FRACTIONS = (
    (0.125, "⅛"), (0.25, "¼"), (0.333, "⅓"), (0.375, "⅜"), (0.5, "½"),
    (0.625, "⅝"), (0.667, "⅔"), (0.75, "¾"), (0.875, "⅞"),
)


def fmt_amount(q: float) -> str:
    """Kitchen-friendly amounts: 0.67 → '⅔', 2.5 → '2½', 0.171 → '0.17'."""
    if q == int(q):
        return str(int(q))
    whole = int(q)
    frac = q - whole
    for value, glyph in _KITCHEN_FRACTIONS:
        if abs(frac - value) <= 0.04:
            return f"{whole}{glyph}" if whole else glyph
    return f"{q:.2f}".rstrip("0").rstrip(".")


# Units that describe a whole, indivisible purchase item. Scaling these to a
# fraction ("0.8 count tomatoes", "1.6 cans beans") reads as nonsense — you buy
# whole cans / onions / limes. For these we round to a sensible whole number
# (min 1) and, when rounding lands back on the original count, fall back to the
# original_text phrasing (which also carries can sizes and the word "canned").
_DISCRETE_UNITS = {
    "count", "can", "cans", "clove", "cloves", "package", "packages", "pkg",
    "stick", "sticks", "head", "heads", "slice", "slices", "loaf", "loaves",
    "ear", "ears", "sprig", "sprigs", "bunch", "bunches",
    "large", "medium", "small",
}


def _round_step(unit: str) -> float:
    """Granularity a scaled amount snaps to, so a cook never reads "3.2 cup" or
    "0.2 tsp". Returns the smallest increment we'll show for `unit`."""
    u = unit.lower()
    if u in ("lb", "lbs", "pound", "pounds"):
        return 0.5          # half-pound is the cutoff — never 1.25 lb
    if u in ("oz", "ounce", "ounces", "fl oz", "floz"):
        return 1.0          # whole ounces
    if u in ("cup", "cups"):
        return 0.25         # quarter-cup measuring marks
    if u in ("tsp", "teaspoon", "teaspoons", "tbsp", "tbsps", "tbsp.",
             "tablespoon", "tablespoons"):
        return 0.25         # quarter-spoon
    return 0.25             # generic default — keep everything on nice fractions


def _round_to(value: float, step: float) -> float:
    """Round half-up to the nearest `step` (Python's round() is banker's, which
    surprises in a kitchen: round(2.5) == 2)."""
    return math.floor(value / step + 0.5) * step


def format_ingredient_line(ing: dict, scale: float) -> str:
    """One amount, one unit per line.

    - Spoonacular 'servings'-unit rows are amountless placeholders (to-taste
      items, leaked section headers) → no fake amount.
    - Unscaled recipe + original_text present → show original_text verbatim
      (natural phrasing, e.g. '1 1/2 tablespoons soy sauce').
    - Discrete whole-item units (cans, cloves, count, …) → round the scaled
      amount to a whole number (min 1) instead of showing a fractional can.
      When the rounded count equals the original whole count, show original_text
      so the can size / "canned" wording survives.
    - Otherwise scaled → kitchen-fraction amount + unit + name; original_text is
      omitted because its numbers contradict the scaled ones.
    """
    name = ing.get("name") or "(unknown)"
    unit = (ing.get("unit") or "").strip()
    original = (ing.get("original_text") or "").strip()

    if unit.lower() in ("serving", "servings"):
        return original if original else f"{name} — to taste / as needed"

    if scale == 1.0 and original:
        return original

    raw_amount = float(ing.get("amount") or 0)
    scaled_amount = raw_amount * scale
    if not scaled_amount:
        return original or name

    if unit.lower() in _DISCRETE_UNITS:
        whole = max(1, int(_round_to(scaled_amount, 1.0)))
        # Scaling didn't change the whole-item count → original phrasing is
        # clearest (keeps "(28-oz) can", "drained", etc.). The parser sometimes
        # strips the food name out of original_text ("1 (28-oz) can" + name
        # "tomatoes"), so re-attach the name when it's missing.
        if original and whole == max(1, int(_round_to(raw_amount, 1.0))):
            if name.lower() not in original.lower():
                return f"{name} — {original}"
            return original
        # "count" is an internal placeholder, not a word a cook wants to read.
        unit_word = "" if unit.lower() == "count" else unit
        return f"**{whole}** {unit_word} {name}".replace("  ", " ").strip()

    # Snap to a kitchen-friendly increment, floored so a real ingredient never
    # rounds away to zero (e.g. 0.2 tsp → ¼ tsp, not nothing).
    step = _round_step(unit)
    snapped = max(step, _round_to(scaled_amount, step))
    amount_str = fmt_amount(snapped)
    return f"**{amount_str}** {unit} {name}".replace("  ", " ")


# ---------------------------------------------------------------------------
# Section renderers
# ---------------------------------------------------------------------------

def render_ingredients(recipe: dict, scale: float):
    st.subheader("Ingredients")
    st.caption(f"Scaled to your household ({scale:.2g}× recipe yield)" if scale != 1.0
               else "At recipe's original yield")
    # Group by component ("Sauce", "For serving", ...) preserving first-seen
    # order; ingredients without a group render first, unlabelled. Recipes
    # with flat ingredient lists look exactly as before.
    grouped: dict[str, list] = {}
    for ing in recipe.get("ingredients") or []:
        grouped.setdefault((ing.get("group") or "").strip(), []).append(ing)
    for group_name, ings in grouped.items():
        if group_name:
            st.markdown(f"**{group_name}**")
        for ing in ings:
            st.write(f"• {format_ingredient_line(ing, scale)}")


def render_instructions(recipe: dict):
    steps = recipe.get("instructions") or []
    if not steps:
        return
    st.subheader("Instructions")
    for step in steps:
        st.write(f"**{step.get('step_number','?')}.** {step.get('text','')}")


def render_recipe_detail(recipe: dict, scale: float = 1.0):
    """Full read-only recipe body: meta header, notes, ingredients, steps.
    No feedback buttons — those live only on the cook screen."""
    # Header: art/photo tile beside the title + meta (handoff preview modal).
    col_tile, col_head = st.columns([1, 3])
    with col_tile:
        render_thumb(recipe, size=80)
    with col_head:
        st.markdown(f"### {recipe.get('title','(untitled)')}")

        meta_bits = []
        if recipe.get("cuisines"):
            meta_bits.append(", ".join(c.title() for c in recipe["cuisines"]))
        if recipe.get("proteins"):
            meta_bits.append("· " + "/".join(recipe["proteins"]))
        if recipe.get("ready_in_minutes"):
            meta_bits.append(f"· {recipe['ready_in_minutes']} min")
        if recipe.get("servings_original"):
            meta_bits.append(f"· serves {recipe['servings_original']}")
        if recipe.get("rating"):
            meta_bits.append(f"·  {star_str(recipe['rating'])}")
        if meta_bits:
            st.caption(" ".join(meta_bits))
        if recipe.get("equipment"):
            st.caption(f"Equipment: {', '.join(recipe['equipment'])}")
        if recipe.get("source_url"):
            st.markdown(f"[Source ↗]({recipe['source_url']})")

    notes = (recipe.get("user_notes") or "").strip()
    if notes:
        st.warning(f"📝 **Your notes**\n\n{notes}")

    render_ingredients(recipe, scale)
    render_instructions(recipe)


@st.dialog("Recipe preview", width="large")
def open_preview(recipe: dict, scale: float = 1.0):
    """Open the recipe in a modal over the current page."""
    render_recipe_detail(recipe, scale)


# ---------------------------------------------------------------------------
# Friendly reason chips (propose / swap cards)
# ---------------------------------------------------------------------------

def is_favorite(recipe: dict, rules: dict) -> bool:
    """Single source of truth for favorite-ness: either the library status
    marker or membership in rules.favorites. Used by the reason chips and the
    cook-screen favorite toggle so they can't disagree."""
    rid = recipe.get("id")
    if recipe.get("status") == "favorite":
        return True
    return any(f.get("recipe_id") == rid for f in (rules.get("favorites") or []))


def star_str(n: int) -> str:
    """'★★★★☆' for a 1-5 rating."""
    return "★" * n + "☆" * (5 - n)


_star_str = star_str  # internal alias


def recipe_reasons(recipe: dict, others: list[dict], rules: dict) -> list[tuple[str, str]]:
    """User-facing reason chips for why a recipe fits the week, mirroring the
    design hand-off. Returns up to 3 (text, tone) tuples. Distinct from the
    planner's internal scoring reasons (which carry point values)."""
    out: list[tuple[str, str]] = []
    rating = recipe.get("rating") or 0

    if is_favorite(recipe, rules):
        out.append(("⭐ Favorite — auto-included", "green"))
    if rating >= 4:
        out.append((f"{_star_str(rating)} you loved this", "amber"))
    elif rating and rating <= 2:
        out.append((f"{_star_str(rating)} — included anyway", "amber"))

    if not recipe.get("last_cooked_at"):
        out.append(("🆕 New to your rotation", "neutral"))

    other_cuisines = {c.lower() for o in others for c in (o.get("cuisines") or [])}
    my_cuisines = [c for c in (recipe.get("cuisines") or [])]
    if my_cuisines and my_cuisines[0].lower() not in other_cuisines:
        out.append((f"Adds {my_cuisines[0].title()} to the week", "neutral"))

    other_proteins = {p.lower() for o in others for p in (o.get("proteins") or [])}
    my_proteins = recipe.get("proteins") or []
    if my_proteins and not any(p.lower() in other_proteins for p in my_proteins):
        out.append((f"{my_proteins[0].title()} — balances proteins", "neutral"))

    if recipe.get("last_cooked_at"):
        out.append((f"Last made {recipe['last_cooked_at'][:10]}", "neutral"))

    return out[:3]
