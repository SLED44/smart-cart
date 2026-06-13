"""
Cooking-mode screen — single-recipe read-only view with feedback buttons.

Entry: ``st.session_state.mealplan_cook_recipe_id`` is set by the active
screen's Open button (or the library browser).

Feedback per PRD §11.2:
    Made it      — times_cooked += 1, last_cooked_at = now()
    Made changes — inline notes editor; on save replaces user_notes AND
                   bumps times_cooked + last_cooked_at (implies you cooked it)
    Never again  — two-step confirm; status=never_again, append to exclusions

Ingredients display scaled to household.size (read from rules).
"""

from datetime import datetime, timezone

import streamlit as st

from mealplan import library
from mealplan.event_log import (
    EVT_RECIPE_CHANGED,
    EVT_RECIPE_COOKED,
    EVT_RECIPE_NEVER_AGAIN,
    log_event,
)
from mealplan.rules import load_rules, save_rules
from mealplan.swap import mark_never_again

from screens._shared import go

_RECIPE_KEY = "mealplan_cook_recipe_id"
_NOTES_EDIT_KEY = "mealplan_cook_notes_edit_open"
_DELETE_CONFIRM_KEY = "mealplan_cook_never_again_confirm"
_FLASH_KEY = "mealplan_cook_flash"


def render():
    rid = st.session_state.get(_RECIPE_KEY)
    if not rid:
        st.title("🍴 No recipe selected")
        st.caption("Open one from your active plan or the library browser.")
        if st.button("← Back to active plan", key="cook_back_none"):
            go("mealplan_active")
        return

    recipe = library.get(rid)
    if not recipe:
        st.title("🍴 Recipe not found")
        st.caption(f"`{rid}` no longer exists in the library.")
        if st.button("← Back to active plan", key="cook_back_missing"):
            _clear_session()
            go("mealplan_active")
        return

    rules = load_rules()
    household_size = int(((rules.get("household") or {}).get("size")) or 4)
    original_servings = int(recipe.get("servings_original") or 4) or 4
    scale = household_size / original_servings

    # Flash messages (after Made it / Made changes / Never again, before nav)
    flash = st.session_state.pop(_FLASH_KEY, None)
    if flash:
        st.success(flash)

    _render_top_bar()
    _render_hero(recipe, household_size, original_servings)
    _render_notes_callout(recipe)
    _render_ingredients(recipe, scale)
    _render_instructions(recipe)
    st.divider()
    _render_action_buttons(rid, recipe, rules)


# ---------------------------------------------------------------------------
# Layout pieces
# ---------------------------------------------------------------------------

def _render_top_bar():
    col_back, _ = st.columns([1, 5])
    with col_back:
        if st.button("← Back to plan", key="cook_back_top"):
            _clear_session()
            go("mealplan_active")


def _render_hero(recipe: dict, household_size: int, original_servings: int):
    col_img, col_body = st.columns([1, 2])
    with col_img:
        if recipe.get("image_url"):
            st.image(recipe["image_url"], use_container_width=True)
        else:
            st.caption("(no image)")
    with col_body:
        st.title(recipe.get("title", "(untitled)"))
        if recipe.get("cuisines"):
            st.caption(" · ".join(c.title() for c in recipe["cuisines"]))

        col_t, col_s, col_c = st.columns(3)
        with col_t:
            st.metric("Ready in", f"{recipe.get('ready_in_minutes','?')} min")
        with col_s:
            if household_size == original_servings:
                st.metric("Serves", household_size)
            else:
                st.metric("Serves", household_size,
                          delta=f"recipe yields {original_servings}",
                          delta_color="off")
        with col_c:
            st.metric("Cooked", recipe.get("times_cooked", 0))

        if recipe.get("equipment"):
            st.caption(f"Equipment: {', '.join(recipe['equipment'])}")
        if recipe.get("source_url"):
            st.markdown(f"[Source ↗]({recipe['source_url']})")


def _render_notes_callout(recipe: dict):
    """Pinned-above-ingredients yellow callout per PRD §11.1."""
    notes = (recipe.get("user_notes") or "").strip()
    if not notes:
        return
    st.warning(f"📝 **Your notes**\n\n{notes}")


def _render_ingredients(recipe: dict, scale: float):
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
            st.write(f"• {_format_ingredient_line(ing, scale)}")


def _format_ingredient_line(ing: dict, scale: float) -> str:
    """One amount, one unit per line. The old renderer printed the scaled
    decimal AND original_text ('**0.67** lb chow mein noodles (16 ounces
    (454 gram) package...)') — two or three units per ingredient.

    Rules:
    - Spoonacular 'servings'-unit rows are amountless placeholders (to-taste
      items and section headers leaked into the list) → no fake amount.
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
    amount_str = _fmt_amount(scaled_amount)
    if not scaled_amount:
        return original or name
    return f"**{amount_str}** {unit} {name}".replace("  ", " ")


def _render_instructions(recipe: dict):
    steps = recipe.get("instructions") or []
    if not steps:
        return
    st.subheader("Instructions")
    for step in steps:
        st.write(f"**{step.get('step_number','?')}.** {step.get('text','')}")


# ---------------------------------------------------------------------------
# Action buttons (Made it / Made changes / Never again)
# ---------------------------------------------------------------------------

def _render_action_buttons(rid: str, recipe: dict, rules: dict):
    notes_editing = bool(st.session_state.get(_NOTES_EDIT_KEY))
    confirming_never = bool(st.session_state.get(_DELETE_CONFIRM_KEY))

    if notes_editing:
        _render_notes_editor(rid, recipe)
        return
    if confirming_never:
        _render_never_again_confirm(rid, recipe, rules)
        return

    col_made, col_changes, col_never = st.columns(3)
    with col_made:
        if st.button("✓ Made it", type="primary",
                     use_container_width=True, key="cook_made"):
            library.record_cooked(rid)
            log_event(EVT_RECIPE_COOKED, {
                "recipe_id":         rid,
                "title":             recipe.get("title", ""),
                "new_times_cooked":  int(recipe.get("times_cooked") or 0) + 1,
            })
            st.session_state[_FLASH_KEY] = "Logged — times cooked +1."
            _clear_session()
            go("mealplan_active")
    with col_changes:
        if st.button("✏ Made changes (edit notes)",
                     use_container_width=True, key="cook_changes"):
            st.session_state[_NOTES_EDIT_KEY] = True
            st.rerun()
    with col_never:
        if recipe.get("status") == "never_again":
            st.caption("🚫 Already excluded from planning")
        elif st.button("🚫 Never again",
                       use_container_width=True, key="cook_never"):
            st.session_state[_DELETE_CONFIRM_KEY] = True
            st.rerun()


def _render_notes_editor(rid: str, recipe: dict):
    st.subheader("Capture changes")
    st.caption("Replaces your existing notes (V1 keeps it as a single field). "
               "Saving also marks the recipe as cooked.")
    new_notes = st.text_area(
        "Notes",
        value=recipe.get("user_notes", ""),
        height=140,
        key="cook_notes_input",
        placeholder="e.g. cut beef quantity by ~25%, kids preferred no onion, "
                    "air fryer 12 min instead of 15",
    )
    col_cancel, col_save = st.columns(2)
    with col_cancel:
        if st.button("Cancel", key="cook_notes_cancel",
                     use_container_width=True):
            st.session_state[_NOTES_EDIT_KEY] = False
            st.rerun()
    with col_save:
        if st.button("Save notes + mark cooked",
                     type="primary", use_container_width=True,
                     key="cook_notes_save"):
            prior_notes = recipe.get("user_notes", "")
            library.record_cooked(rid, notes=new_notes)
            log_event(EVT_RECIPE_CHANGED, {
                "recipe_id":         rid,
                "title":             recipe.get("title", ""),
                "prior_notes":       prior_notes,
                "new_notes":         new_notes,
                "new_times_cooked":  int(recipe.get("times_cooked") or 0) + 1,
            })
            st.session_state[_FLASH_KEY] = "Notes saved; times cooked +1."
            _clear_session()
            go("mealplan_active")


def _render_never_again_confirm(rid: str, recipe: dict, rules: dict):
    st.error(
        f"Mark **{recipe.get('title','(untitled)')}** as never again? "
        f"It'll be excluded from future planning and added to your rules' "
        f"exclusion list."
    )
    col_cancel, col_yes = st.columns(2)
    with col_cancel:
        if st.button("Cancel", key="cook_never_cancel",
                     use_container_width=True):
            st.session_state[_DELETE_CONFIRM_KEY] = False
            st.rerun()
    with col_yes:
        if st.button("Yes, never again", type="primary",
                     use_container_width=True, key="cook_never_yes"):
            new_rules = mark_never_again(rid, rules)
            save_rules(new_rules)
            # Guard against double-logging (e.g. marked again on a later
            # visit) — the event log feeds scoring/curation stats.
            if recipe.get("status") != "never_again":
                log_event(EVT_RECIPE_NEVER_AGAIN, {
                    "recipe_id": rid,
                    "title":     recipe.get("title", ""),
                    "via":       "cook_screen",
                })
            st.session_state[_FLASH_KEY] = (
                f"Excluded {recipe.get('title','recipe')} from future planning."
            )
            _clear_session()
            go("mealplan_active")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_KITCHEN_FRACTIONS = (
    (0.125, "⅛"), (0.25, "¼"), (0.333, "⅓"), (0.375, "⅜"), (0.5, "½"),
    (0.625, "⅝"), (0.667, "⅔"), (0.75, "¾"), (0.875, "⅞"),
)


def _fmt_amount(q: float) -> str:
    """Kitchen-friendly amounts: 0.67 → '⅔', 2.5 → '2½', 0.171 → '0.17'."""
    if q == int(q):
        return str(int(q))
    whole = int(q)
    frac = q - whole
    for value, glyph in _KITCHEN_FRACTIONS:
        if abs(frac - value) <= 0.04:
            return f"{whole}{glyph}" if whole else glyph
    return f"{q:.2f}".rstrip("0").rstrip(".")


def _clear_session():
    """Reset the cook-screen ephemeral state. Recipe id stays — it's set
    by whoever navigated us here (active screen, library)."""
    for k in (_RECIPE_KEY, _NOTES_EDIT_KEY, _DELETE_CONFIRM_KEY):
        st.session_state.pop(k, None)
