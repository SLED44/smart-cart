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


# ---------------------------------------------------------------------------
# Cooking body — two columns: check-off ingredients + active-step instructions
# ---------------------------------------------------------------------------

# Common words too generic to anchor a step→ingredient highlight match.
_HIGHLIGHT_STOP = {"oil", "salt", "water", "sugar", "pepper", "butter", "broth"}


def _step_uses_ingredient(step_text: str, ing: dict) -> bool:
    """Lightweight match: does this step mention this ingredient? Full name
    substring, or the head noun (≥4 chars, not a generic pantry word)."""
    name = (ing.get("name") or "").lower().strip()
    if not name:
        return False
    text = step_text.lower()
    if name in text:
        return True
    head = name.split()[-1] if name.split() else ""
    return len(head) >= 4 and head not in _HIGHLIGHT_STOP and head in text


def _render_cook_body(rid: str, recipe: dict, scale: float):
    steps = recipe.get("instructions") or []
    active = int(st.session_state.get(f"cook_step_{rid}", 0) or 0)
    if active > len(steps):
        active = 0
    active_text = steps[active - 1].get("text", "") if 1 <= active <= len(steps) else ""

    col_ing, col_steps = st.columns([1, 1.3])
    with col_ing:
        _render_cook_ingredients(rid, recipe, scale, active, active_text)
    with col_steps:
        _render_cook_steps(rid, steps, active)


def _render_cook_ingredients(rid: str, recipe: dict, scale: float,
                             active: int, active_text: str):
    ings = recipe.get("ingredients") or []
    total = len(ings)
    checked = sum(1 for i in range(total) if st.session_state.get(f"cook_ck_{rid}_{i}"))

    st.subheader(f"Ingredients · {checked} of {total} in")
    if active:
        st.caption(f"Highlighted ingredients are used in step {active}.")
    else:
        st.caption("Tap to check off as you add them.")

    # Group preserving each ingredient's original index (stable checkbox keys).
    groups: dict[str, list] = {}
    for i, ing in enumerate(ings):
        groups.setdefault((ing.get("group") or "").strip(), []).append((i, ing))

    for group_name, items in groups.items():
        if group_name:
            st.markdown(f"**{group_name}**")
        for i, ing in items:
            line = _recipe_view.format_ingredient_line(ing, scale)
            if active_text and _step_uses_ingredient(active_text, ing):
                # Scaled lines already contain '**amount**'; don't re-wrap in
                # bold (that yields malformed markdown). Bold only plain lines.
                line = f"👉 {line}" if "**" in line else f"👉 **{line}**"
            st.checkbox(line, key=f"cook_ck_{rid}_{i}")


def _render_cook_steps(rid: str, steps: list, active: int):
    n = len(steps)
    step_key = f"cook_step_{rid}"
    st.subheader(f"Step {active} of {n}" if active else f"Instructions · {n} steps")

    col_prev, col_next, col_clear = st.columns(3)
    with col_prev:
        if st.button("← Prev", key="cook_step_prev", use_container_width=True,
                     disabled=active <= 1):
            st.session_state[step_key] = max(1, active - 1)
            st.rerun()
    with col_next:
        if st.button("Next →", key="cook_step_next", use_container_width=True,
                     disabled=active >= n):
            st.session_state[step_key] = (active + 1) if active else 1
            st.rerun()
    with col_clear:
        if st.button("Clear", key="cook_step_clear", use_container_width=True,
                     disabled=not active):
            st.session_state[step_key] = 0
            st.rerun()

    # Identify the active step by 1-based position (matches steps[active-1] in
    # _render_cook_body), not by step_number — step_number may be missing or
    # duplicated, which would collide widget keys and break the position math.
    for idx, step in enumerate(steps):
        pos = idx + 1
        num = step.get("step_number", pos)  # display label; fall back to position
        text = step.get("text", "")
        is_active = pos == active
        with st.container(border=True):
            if is_active:
                st.success(f"**{num}.** {text}")
            else:
                st.markdown(f"**{num}.** {text}")
                if st.button("▶ Cook this step", key=f"cook_step_set_{idx}"):
                    st.session_state[step_key] = pos
                    st.rerun()


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
