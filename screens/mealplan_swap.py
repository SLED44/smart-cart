"""
Swap screen — replace one slot in the pending lineup.

Entry: ``st.session_state.mealplan_swap_slot_index`` is set by the propose
screen before navigating here. Filters (cuisine / protein / name) live in
session_state too so they survive reruns when the user hits "Give me 5 new".

Library-first; falls back to Spoonacular when both cuisine + protein are
specified and the library has fewer than 5 eligible candidates (PRD §10.2).
"""

from datetime import datetime, timezone

import streamlit as st

from mealplan import library
from mealplan.event_log import (
    EVT_RECIPE_NEVER_AGAIN,
    EVT_SLOT_SWAPPED,
    log_event,
)
from mealplan.rules import _VALID_PROTEINS, default_rules, load_rules, save_rules
from mealplan.swap import get_swap_candidates, mark_never_again
from applog import get_logger

_log = get_logger(__name__)
from supabase_kv import kv_get, kv_put

from screens._shared import go
from screens import _recipe_view
from sc_design import reason_chips, planner_card


def _meta_line(recipe: dict) -> str:
    """'Greek · chicken · 35 min' meta line for a candidate card."""
    bits = []
    if recipe.get("cuisines"):
        bits.append(", ".join(c.title() for c in recipe["cuisines"]))
    if recipe.get("proteins"):
        bits.append("/".join(recipe["proteins"]))
    if recipe.get("ready_in_minutes"):
        bits.append(f"{recipe['ready_in_minutes']} min")
    return " · ".join(bits)


def _status_chip(recipe: dict) -> tuple[str, str]:
    """Lead chip on a candidate card: rating stars if rated, else how many
    times it's been cooked (handoff candidate-card status chip)."""
    if recipe.get("rating"):
        return (_recipe_view.star_str(recipe["rating"]), "amber")
    n = int(recipe.get("times_cooked") or 0)
    return (f"Cooked {n}×" if n else "Never cooked", "sky")

KEY_PENDING_LINEUP = "pending_lineup"
KEY_HISTORY = "meal_plan_history"

_SLOT_KEY = "mealplan_swap_slot_index"
_CUISINE_KEY = "mealplan_swap_cuisine"
_PROTEIN_KEY = "mealplan_swap_protein"
_NAME_KEY = "mealplan_swap_name_search"
_SEEN_KEY = "mealplan_swap_seen_ids"

_ANY = "any"


def render():
    pending = kv_get(KEY_PENDING_LINEUP, None)
    slot_index = st.session_state.get(_SLOT_KEY)
    if not pending or slot_index is None:
        st.warning("No slot to swap. Open a plan from the meal-planner home first.")
        if st.button("← Back to meal-plan home", key="mp_swap_back_lost"):
            _clear_session()
            go("mealplan_home")
        return

    meals = pending.get("meals") or []
    if slot_index >= len(meals):
        st.warning("Slot is out of range. Returning to propose screen.")
        _clear_session()
        go("mealplan_propose")
        return

    # One library fetch for the whole render — library.get() is a full KV
    # round-trip, and this screen resolves recipes for the current pick, the
    # lineup, and every candidate card's reason chips.
    lib = library.get_all()
    current_slot = meals[slot_index]
    current_recipe = lib.get(current_slot.get("recipe_id")) if current_slot.get("recipe_id") else None

    st.title(f"🔁 Replace slot {slot_index + 1}")
    if current_recipe:
        st.caption(f"Currently: **{current_recipe.get('title','(untitled)')}** — "
                   f"{'/'.join(current_recipe.get('proteins') or []) or 'no proteins'}, "
                   f"{', '.join(current_recipe.get('cuisines') or []) or 'no cuisine'}")
    else:
        st.caption("Slot is empty — pick anything that fits.")

    rules = load_rules()
    history = kv_get(KEY_HISTORY, []) or []

    _render_filter_bar(rules)

    cuisine = st.session_state.get(_CUISINE_KEY, _ANY)
    protein = st.session_state.get(_PROTEIN_KEY, _ANY)
    name_search = st.session_state.get(_NAME_KEY, "")

    cuisine_arg = None if cuisine == _ANY else cuisine
    protein_arg = None if protein == _ANY else protein
    name_arg = name_search.strip() or None
    seen_ids = set(st.session_state.get(_SEEN_KEY, []))

    current_lineup = []
    for m in meals:
        rid = m.get("recipe_id")
        r = lib.get(rid) if rid else None
        current_lineup.append(r if r else {})

    with st.spinner("Finding candidates…"):
        result = get_swap_candidates(
            slot_index=slot_index,
            current_lineup=current_lineup,
            rules=rules,
            history=history,
            cuisine=cuisine_arg,
            protein=protein_arg,
            name_search=name_arg,
            seen_ids=seen_ids,
        )

    st.divider()
    _render_action_bar(result)

    if result.note:
        st.info(result.note)
    if result.spoonacular_attempted:
        if result.spoonacular_error:
            st.warning(f"Spoonacular query failed: {result.spoonacular_error}")
        else:
            st.caption(f"💡 Pulled fresh from Spoonacular to fill the gap "
                       f"(only happens when library has <5 hits for this filter combo).")

    st.divider()
    if not result.candidates:
        st.warning("No candidates after filtering. Try loosening the filter, or pick "
                   "different cuisine + protein for a Spoonacular fallback.")
        return

    # Trace + stash the alternatives presented so the eventual pick can record
    # what was shown vs. what was passed over.
    shown = [{"recipe_id": c.recipe.get("id"),
              "title":     c.recipe.get("title"),
              "score":     c.score,
              "source":    c.source}
             for c in result.candidates]
    st.session_state["mp_swap_shown"] = shown
    _log.info("SWAP slot %d: showing %d alternative(s): %s",
              slot_index + 1, len(shown),
              ", ".join(f"{s['title']}({s['score']:.0f})" for s in shown))

    for cand in result.candidates:
        _render_candidate_card(cand, slot_index, meals, pending, rules, lib)


# ---------------------------------------------------------------------------
# Filter bar + action bar
# ---------------------------------------------------------------------------

def _render_filter_bar(rules: dict):
    cuisines_all = (rules.get("cuisines") or {}).get("rotation_set") \
        or default_rules()["cuisines"]["rotation_set"]

    col_cui, col_pro, col_name = st.columns(3)
    with col_cui:
        cur = st.session_state.get(_CUISINE_KEY, _ANY)
        options = [_ANY] + sorted(cuisines_all)
        if cur not in options:
            cur = _ANY
        choice = st.selectbox(
            "Cuisine", options, index=options.index(cur),
            key=f"mp_swap_cui_input",
            format_func=lambda v: "Any cuisine" if v == _ANY else v.title())
        if choice != cur:
            st.session_state[_CUISINE_KEY] = choice
            st.session_state[_SEEN_KEY] = []  # reset paging on filter change
            st.rerun()
    with col_pro:
        cur = st.session_state.get(_PROTEIN_KEY, _ANY)
        options = [_ANY] + list(_VALID_PROTEINS)
        if cur not in options:
            cur = _ANY
        choice = st.selectbox(
            "Protein", options, index=options.index(cur),
            key=f"mp_swap_pro_input",
            format_func=lambda v: "Any protein" if v == _ANY else v.title())
        if choice != cur:
            st.session_state[_PROTEIN_KEY] = choice
            st.session_state[_SEEN_KEY] = []
            st.rerun()
    with col_name:
        cur = st.session_state.get(_NAME_KEY, "")
        text = st.text_input("Name search", value=cur, key="mp_swap_name_input",
                             placeholder="e.g. katsu")
        if text != cur:
            st.session_state[_NAME_KEY] = text
            st.session_state[_SEEN_KEY] = []
            st.rerun()


def _render_action_bar(result):
    col_back, col_reset, col_new = st.columns([1, 1, 2])
    with col_back:
        if st.button("← Back to propose", key="mp_swap_back"):
            _clear_session()
            go("mealplan_propose")
    with col_reset:
        if st.button("↻ Reset filters", key="mp_swap_reset"):
            for k in (_CUISINE_KEY, _PROTEIN_KEY, _NAME_KEY, _SEEN_KEY):
                st.session_state.pop(k, None)
            st.rerun()
    with col_new:
        if st.button("🔄 Give me 5 new options", key="mp_swap_new",
                     use_container_width=True):
            # Add current ids to seen_ids → next render pulls a fresh page.
            seen = set(st.session_state.get(_SEEN_KEY, []))
            for cand in result.candidates:
                rid = cand.recipe.get("id")
                if rid:
                    seen.add(rid)
            st.session_state[_SEEN_KEY] = sorted(seen)
            st.rerun()


# ---------------------------------------------------------------------------
# Candidate card
# ---------------------------------------------------------------------------

def _render_candidate_card(cand, slot_index: int, meals: list[dict], pending: dict, rules: dict, lib: dict):
    recipe = cand.recipe
    rid = recipe.get("id", "")

    with st.container(border=True):
        col_body, col_actions = st.columns([5, 1])
        with col_body:
            # Status chip (rating stars if rated, else cooked-count) leads the
            # chip row, followed by the fit reasons vs. the rest of the lineup.
            others = [lib.get(m.get("recipe_id")) for i, m in enumerate(meals)
                      if i != slot_index and m.get("recipe_id")]
            others = [r for r in others if r]
            chip_items = [_status_chip(recipe)]
            chip_items += _recipe_view.recipe_reasons(recipe, others, rules)
            if cand.source == "spoonacular":
                chip_items.append(("✨ Fresh from Spoonacular", "neutral"))
            st.html(planner_card(
                recipe=recipe,
                label=(recipe.get("cuisines") or [""])[0].title() or "Candidate",
                title=recipe.get("title", "(untitled)"),
                meta=_meta_line(recipe),
                chips_html=reason_chips(chip_items),
                favorite=_recipe_view.is_favorite(recipe, rules),
            ))
            if recipe.get("user_notes"):
                st.caption(f"📝 _{recipe['user_notes'][:120]}_")
            with st.expander(f"Scoring detail · {cand.score:.0f}"):
                for r in cand.reasons:
                    st.caption(f"• {r}")
                for r in (cand.relaxations_applied or []):
                    st.caption(f"⚙ {r}")
        with col_actions:
            if st.button("Preview", key=f"mp_swap_preview_{rid}",
                         use_container_width=True):
                _recipe_view.open_preview(recipe, _recipe_view.compute_scale(recipe, rules))
            if st.button("Pick", type="primary", key=f"mp_swap_pick_{rid}",
                         use_container_width=True):
                _apply_pick(rid, slot_index, meals, pending, source=cand.source)
                return
            if st.button("🚫 Never make", key=f"mp_swap_never_{rid}",
                         use_container_width=True):
                new_rules = mark_never_again(rid, rules)
                save_rules(new_rules)
                log_event(EVT_RECIPE_NEVER_AGAIN, {
                    "recipe_id": rid,
                    "title":     recipe.get("title", ""),
                    "via":       "swap_screen",
                })
                # Remove from seen_ids isn't necessary — it's never_again now,
                # so future evaluate_candidate calls reject it.
                st.rerun()


# ---------------------------------------------------------------------------
# Apply pick
# ---------------------------------------------------------------------------

def _apply_pick(rid: str, slot_index: int, meals: list[dict], pending: dict, source: str):
    cuisine = st.session_state.get(_CUISINE_KEY, _ANY)
    protein = st.session_state.get(_PROTEIN_KEY, _ANY)
    name_search = st.session_state.get(_NAME_KEY, "")
    if name_search:
        added_via = "manual_search"
    elif cuisine != _ANY or protein != _ANY:
        added_via = "swap_filtered"
    else:
        added_via = "swap_unfiltered"

    # Telemetry — capture the swap with old + new + filters so summary
    # can spot persistently-swapped recipes (candidates for rule changes).
    old_meal = meals[slot_index] if slot_index < len(meals) else {}
    old_rid = old_meal.get("recipe_id")
    old_recipe = library.get(old_rid) if old_rid else None
    new_recipe = library.get(rid)
    shown = st.session_state.get("mp_swap_shown") or []
    passed_over = [s for s in shown if s.get("recipe_id") != rid]
    _log.info("SWAP slot %d: picked %r over %d other(s): %s",
              slot_index + 1, (new_recipe or {}).get("title", rid),
              len(passed_over),
              ", ".join(f"{s['title']}({s['score']:.0f})" for s in passed_over))

    log_event(EVT_SLOT_SWAPPED, {
        "slot":           slot_index,
        "old_recipe_id":  old_rid,
        "old_title":      (old_recipe or {}).get("title", ""),
        "new_recipe_id":  rid,
        "new_title":      (new_recipe or {}).get("title", ""),
        "cuisine_filter": None if cuisine == _ANY else cuisine,
        "protein_filter": None if protein == _ANY else protein,
        "name_search":    name_search.strip() if name_search else "",
        "source":         source,
        "added_via":      added_via,
        "alternatives_shown": shown,
        "passed_over":    passed_over,
    })

    meals[slot_index] = {
        "slot":             slot_index,
        "recipe_id":        rid,
        "added_via":        added_via,
        # Reasons/score are not recomputed here; lineup_meta on the propose
        # screen recomputes against current_lineup anyway, so the per-slot
        # rationale for swapped recipes is intentionally light.
        "reasons":          [],
        "relaxations":      [],
        "score":            0,
        "relaxation_level": 0,
    }
    pending["meals"] = meals
    pending["updated_at"] = datetime.now(timezone.utc).isoformat()
    kv_put(KEY_PENDING_LINEUP, pending)

    _clear_session()
    go("mealplan_propose")


def _clear_session():
    for k in (_SLOT_KEY, _CUISINE_KEY, _PROTEIN_KEY, _NAME_KEY, _SEEN_KEY):
        st.session_state.pop(k, None)
