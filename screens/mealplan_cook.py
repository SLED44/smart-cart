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
import streamlit.components.v1 as components

from mealplan import library
from mealplan.event_log import (
    EVT_RECIPE_CHANGED,
    EVT_RECIPE_COOKED,
    EVT_RECIPE_NEVER_AGAIN,
    EVT_RECIPE_RATED,
    log_event,
)
from mealplan.rules import load_rules, save_rules
from mealplan.swap import mark_never_again

# Favorite cadence applied when you favorite from the cook screen (mirrors
# the paste-recipe screen). [due_week, force_week].
_DEFAULT_FAV_CADENCE = [4, 6]

from screens._shared import go
from screens import _recipe_view
from screens._cook_pane import build_cook_pane, PANE_HEIGHT

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
    _render_cook_body(rid, recipe, scale)
    st.divider()
    _render_rating(rid, recipe, rules)
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
    # The title / art tile / cuisine pill / meta chips now live inside the
    # cooking-pane iframe (so the two columns own the frame and scroll
    # independently). Here we keep only the bits the pane header omits:
    # household scaling, how many times it's been cooked, and the source link.
    bits = []
    if household_size != original_servings:
        bits.append(f"Scaled to your household of {household_size} "
                    f"(recipe yields {original_servings})")
    else:
        bits.append(f"Serves {household_size}")
    if recipe.get("times_cooked"):
        bits.append(f"Cooked {recipe['times_cooked']}×")
    st.caption(" · ".join(bits))
    if recipe.get("source_url"):
        st.markdown(f"[Source ↗]({recipe['source_url']})")


def _render_notes_callout(recipe: dict):
    """Pinned-above-ingredients yellow callout per PRD §11.1."""
    notes = (recipe.get("user_notes") or "").strip()
    if not notes:
        return
    st.warning(f"📝 **Your notes**\n\n{notes}")


# ---------------------------------------------------------------------------
# Cooking body — two INDEPENDENTLY-SCROLLING columns (ingredients | steps),
# rendered as an embedded HTML/JS pane so the panes scroll separately and the
# check-off / active-step / highlight interactions stay client-side (persisted
# to localStorage, so they survive Streamlit reruns). See screens/_cook_pane.py.
# ---------------------------------------------------------------------------

def _render_cook_body(rid: str, recipe: dict, scale: float):
    steps = recipe.get("instructions") or []
    if not (recipe.get("ingredients") or steps):
        st.caption("No ingredients or steps recorded for this recipe.")
        return
    components.html(
        build_cook_pane(recipe, scale),
        height=PANE_HEIGHT + 8,  # +8 so the cards' shadow isn't clipped
        scrolling=False,
    )


# ---------------------------------------------------------------------------
# Star rating + favorite toggle
# ---------------------------------------------------------------------------

def _toggle_favorite(rid: str, recipe: dict, rules: dict):
    """Add/remove the recipe from rules.favorites (with default cadence) and
    mirror its library status. Returns the new favorite state."""
    favs = list(rules.get("favorites") or [])
    if _recipe_view.is_favorite(recipe, rules):
        favs = [f for f in favs if f.get("recipe_id") != rid]
        new_state = False
        # Only drop the status back to active if it was the favorite marker.
        if recipe.get("status") == "favorite":
            library.set_status(rid, "active")
    else:
        favs.append({"recipe_id": rid, "cadence_weeks": list(_DEFAULT_FAV_CADENCE),
                     "last_used_week": None})
        new_state = True
        if recipe.get("status") == "active":
            library.set_status(rid, "favorite")
    rules["favorites"] = favs
    save_rules(rules)
    return new_state


def _render_rating(rid: str, recipe: dict, rules: dict):
    current = recipe.get("rating")
    if current:
        st.subheader(f"You rated this {current}/5")
    else:
        st.subheader("How was tonight's dinner?")
        st.caption("4–5★ bumps it up your rotation · 1–2★ makes it rarer "
                   "(that's different from “never again”).")

    # Five star buttons in a row. Filled up to the current rating.
    cols = st.columns(11)
    for i in range(1, 6):
        with cols[i - 1]:
            filled = current and i <= current
            if st.button("★" if filled else "☆", key=f"cook_star_{i}",
                         help=f"{i} star{'s' if i > 1 else ''}"):
                library.set_rating(rid, i)
                log_event(EVT_RECIPE_RATED, {
                    "recipe_id": rid, "title": recipe.get("title", ""), "stars": i,
                })
                st.session_state[_FLASH_KEY] = f"Rated {i}/5 — noted for next week's plan."
                st.rerun()

    if current:
        if st.button("Clear rating", key="cook_rating_clear"):
            library.set_rating(rid, None)
            st.rerun()

    # Favorite toggle: surfaced for high ratings or recipes already favorited.
    is_fav = _recipe_view.is_favorite(recipe, rules)
    if (current and current >= 4) or is_fav:
        label = "★ In your favorites — tap to remove" if is_fav else "☆ Add to favorites"
        if st.button(label, key="cook_fav_toggle"):
            now_fav = _toggle_favorite(rid, recipe, rules)
            st.session_state[_FLASH_KEY] = (
                "Added to favorites — it'll come around on its cadence."
                if now_fav else "Removed from favorites."
            )
            st.rerun()


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

def _clear_session():
    """Reset the cook-screen ephemeral state. Recipe id stays — it's set
    by whoever navigated us here (active screen, library)."""
    for k in (_RECIPE_KEY, _NOTES_EDIT_KEY, _DELETE_CONFIRM_KEY):
        st.session_state.pop(k, None)
