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

import streamlit as st


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


def format_ingredient_line(ing: dict, scale: float) -> str:
    """One amount, one unit per line.

    - Spoonacular 'servings'-unit rows are amountless placeholders (to-taste
      items, leaked section headers) → no fake amount.
    - Unscaled recipe + original_text present → show original_text verbatim
      (natural phrasing, e.g. '1 1/2 tablespoons soy sauce').
    - Scaled → kitchen-fraction amount + unit + name; original_text is
      omitted because its numbers contradict the scaled ones.
    """
    name = ing.get("name") or "(unknown)"
    unit = (ing.get("unit") or "").strip()
    original = (ing.get("original_text") or "").strip()

    if unit.lower() in ("serving", "servings"):
        return original if original else f"{name} — to taste / as needed"

    if scale == 1.0 and original:
        return original

    scaled_amount = float(ing.get("amount") or 0) * scale
    amount_str = fmt_amount(scaled_amount)
    if not scaled_amount:
        return original or name
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

def _is_favorite(recipe: dict, rules: dict) -> bool:
    rid = recipe.get("id")
    if recipe.get("status") == "favorite":
        return True
    return any(f.get("recipe_id") == rid for f in (rules.get("favorites") or []))


def _star_str(n: int) -> str:
    return "★" * n + "☆" * (5 - n)


def recipe_reasons(recipe: dict, others: list[dict], rules: dict) -> list[tuple[str, str]]:
    """User-facing reason chips for why a recipe fits the week, mirroring the
    design hand-off. Returns up to 3 (text, tone) tuples. Distinct from the
    planner's internal scoring reasons (which carry point values)."""
    out: list[tuple[str, str]] = []
    rating = recipe.get("rating") or 0

    if _is_favorite(recipe, rules):
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
