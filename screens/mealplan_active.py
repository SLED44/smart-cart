"""
Active plan screen — this week's confirmed lineup.

Shows N meals from ``current_plan`` as tappable cards. Tapping opens the
cooking view (Phase 7 — currently stubbed). "Generate grocery list" runs
the aggregator and hands off to SmartCart's preview screen (Phase 8).
"""

from datetime import datetime, timezone

import streamlit as st

from mealplan import grocery, library
from mealplan.event_log import EVT_GROCERY_GENERATED, log_event
from mealplan.rules import load_rules
from supabase_kv import kv_get, kv_put

from screens._shared import clear_review_widget_state, go
from screens import _recipe_view
from applog import get_logger, log_items

_log = get_logger(__name__)

KEY_CURRENT_PLAN = "current_plan"


def render():
    plan = kv_get(KEY_CURRENT_PLAN, None)
    if not plan or not plan.get("meals"):
        st.title("📅 No active plan")
        st.caption("You haven't confirmed a plan yet. Build one from the meal-planner home.")
        if st.button("← Back to meal-planner home", key="mp_active_back_empty"):
            go("mealplan_home")
        return

    # Flash set by the cook screen (Made it / notes saved / never again) —
    # it navigates here immediately, so this is where the message must show.
    flash = st.session_state.pop("mealplan_cook_flash", None)
    if flash:
        st.success(flash)

    st.title("📅 This week's plan")
    st.caption(
        f"Week #{plan.get('week_number','?')} · "
        f"confirmed {plan.get('confirmed_at','')[:10] if plan.get('confirmed_at') else ''}"
        + (f" · grocery list generated {plan['grocery_list_generated_at'][:10]}"
           if plan.get('grocery_list_generated_at') else "")
    )

    col_back, col_replan, col_grocery = st.columns([1, 2, 2])
    with col_back:
        if st.button("← Home", key="mp_active_back"):
            go("mealplan_home")
    with col_replan:
        if st.button("🔄 Plan new (replaces this)",
                     use_container_width=True, key="mp_active_replan"):
            st.session_state.mealplan_propose_fresh = True
            go("mealplan_propose")
    with col_grocery:
        if st.button("🛒 Generate grocery list →",
                     type="primary", use_container_width=True,
                     key="mp_active_grocery"):
            _grocery_overlay(plan)

    st.divider()

    meals = plan.get("meals") or []
    for i, slot in enumerate(meals):
        _render_meal_card(i, slot)


@st.dialog("🛒 Grocery list")
def _grocery_overlay(plan: dict):
    """Preview the merged grocery list before handing it to SmartCart
    (handoff §6 grocery overlay). Two-column item list; ingredients used in
    more than one recipe get an "N recipes" chip. Footer: Close + Hand off."""
    rules = load_rules()
    household_size = int(((rules.get("household") or {}).get("size")) or 4)
    recipe_ids = [m.get("recipe_id") for m in (plan.get("meals") or [])
                  if m.get("recipe_id")]
    if not recipe_ids:
        st.error("No recipes in this plan. Nothing to aggregate.")
        return

    with st.spinner("Merging ingredients across this week's meals…"):
        items = grocery.aggregate_grocery_list(recipe_ids, household_size)

    st.caption(f"{len(items)} item{'s' if len(items) != 1 else ''}, merged across "
               f"{len(recipe_ids)} meal{'s' if len(recipe_ids) != 1 else ''}. "
               f"You can still add staples and tweak quantities on the next screen.")

    # Two-column item list. Each line: bullet + name (+ qty/unit) + an "N
    # recipes" chip when the ingredient came from more than one meal.
    half = (len(items) + 1) // 2
    col_l, col_r = st.columns(2)
    for col, chunk in ((col_l, items[:half]), (col_r, items[half:])):
        with col:
            for it in chunk:
                st.html(_grocery_line(it))

    st.divider()
    col_close, col_go = st.columns([1, 2])
    with col_close:
        if st.button("Close", key="mp_groc_close", use_container_width=True):
            st.rerun()  # dismiss the dialog without navigating
    with col_go:
        if st.button("Hand off to SmartCart →", type="primary",
                     use_container_width=True, key="mp_groc_handoff"):
            _hand_off_to_smartcart(plan, items=items)


def _grocery_line(it: dict) -> str:
    """One grocery-overlay row: bullet + qty/unit + name, plus a pastel
    'N recipes' chip when the ingredient is shared across meals."""
    import html as _html
    qty = it.get("quantity")
    if isinstance(qty, float) and qty == int(qty):
        qty = int(qty)
    unit = (it.get("unit") or "").strip()
    if unit in ("count", "serving", "servings"):
        unit = ""
    amount = " ".join(str(p) for p in (qty, unit) if p not in (None, "", 0))
    name = _html.escape(it.get("item_name", ""))
    label = f"{amount} {name}".strip()

    sources = [s for s in (it.get("notes") or "").split(", ") if s]
    chip = ""
    if len(sources) > 1:
        chip = (f'<span style="margin-left:8px; font-size:11.5px; font-weight:600; '
                f'color:#2f6f9e; background:#dceaf5; border-radius:999px; '
                f'padding:1px 8px; white-space:nowrap;">{len(sources)} recipes</span>')
    return (f'<div style="padding:4px 0; font-size:14px; line-height:1.4;">'
            f'<span style="color:#2e9e54;">•</span> {label}{chip}</div>')


def _hand_off_to_smartcart(plan: dict, items: list[dict] | None = None):
    """Populate parsed_result from the aggregated grocery items and route to
    the main grocery page. Accepts pre-aggregated `items` (from the grocery
    overlay) to avoid a second aggregation pass; aggregates itself otherwise."""
    rules = load_rules()
    household_size = int(((rules.get("household") or {}).get("size")) or 4)

    recipe_ids = [m.get("recipe_id") for m in (plan.get("meals") or [])
                  if m.get("recipe_id")]
    if not recipe_ids:
        st.error("No recipes in this plan. Nothing to aggregate.")
        return

    if items is None:
        with st.spinner("Aggregating ingredients…"):
            items = grocery.aggregate_grocery_list(recipe_ids, household_size)

    _log.info("handoff: %d recipe(s), household=%d -> %d aggregated item(s)",
              len(recipe_ids), household_size, len(items))
    log_items(_log, "handoff.aggregated", items)

    # Load the aggregated list onto the main grocery page (home) as editable
    # text, where the user can add pantry / staple items before parsing.
    # Routing through home → list_parser → item_filter keeps a single grocery
    # entry point and runs the list through the standard, tested pipeline. The
    # old direct-to-preview hand-off fed pre-structured items straight into the
    # matcher, which is where the unit artifacts (counts shown as lb/bunch) and
    # the recipe-title-next-to-each-ingredient clutter came from.
    st.session_state.raw_list = _items_to_text(items)
    st.session_state.meal_plan_handoff = True

    # Reset any leftover session state from a previous SmartCart run.
    st.session_state.parsed_result = None
    st.session_state.combined_items = []
    st.session_state.staples_added = False
    st.session_state.manual_items = []
    st.session_state.staple_selections = {}
    for k in ("scan_result", "matched_items", "review_index",
              "confirmed_items", "skipped_items", "not_found_items",
              "new_prefs_count", "cart_result", "review_history",
              "sale_switches", "auto_confirmed_items",
              "item_filter_selections"):
        st.session_state.pop(k, None)
    clear_review_widget_state()

    # Stamp plan so the home screen / metric knows when grocery was generated.
    plan["grocery_list_generated_at"] = datetime.now(timezone.utc).isoformat()
    kv_put(KEY_CURRENT_PLAN, plan)

    log_event(EVT_GROCERY_GENERATED, {
        "plan_week_number": plan.get("week_number"),
        "recipe_count":     len(recipe_ids),
        "item_count":       len(items),
        "household_size":   household_size,
    }, week=plan.get("week_number"))

    # Land on the main grocery page with the list loaded.
    go("home")


# Recipe ingredients counted in sub-units of a whole-sold item. Recipes store
# e.g. garlic as unit "count" (the number is cloves), so the plain count would
# read as whole heads — "12 garlic" → 12 bulbs. Re-attach the portion word so
# the list parser collapses it to one item ("12 cloves garlic" → 1 head).
_COUNT_PORTION = {"garlic": "cloves"}


def _items_to_text(items: list[dict]) -> str:
    """Render aggregated grocery items as one editable line each, e.g.
    '2.5 lb chicken wings' or '8 celery'. Count/serving/blank units are
    dropped so each line reads naturally for the list parser. The recipe
    title (carried in 'notes') is intentionally omitted — it is not part of
    the shopping line.

    The aggregator keeps same-ingredient/different-unit rows separate
    (PRD §12.4); merge them here so the parser sees one line per ingredient.
    Same unit → sum quantities; mixed units → emit the bare name (the shopper
    buys one sensible package either way, and 'tbsp + cup' sums are garbage)."""
    merged: dict[str, dict] = {}
    for it in items:
        name = (it.get("item_name") or "").strip()
        if not name:
            continue
        qty = it.get("quantity")
        unit = (it.get("unit") or "").strip().lower()
        if unit in ("count", "serving", "servings"):
            # Re-attach a portion word for sub-unit-counted items (garlic →
            # cloves); otherwise drop the placeholder unit.
            unit = _COUNT_PORTION.get(name.lower(), "")
        key = name.lower()
        if key not in merged:
            merged[key] = {"name": name, "qty": qty, "unit": unit}
        else:
            slot = merged[key]
            if slot["unit"] == unit and slot["qty"] is not None and qty is not None:
                slot["qty"] += qty
            else:
                slot["qty"], slot["unit"] = None, ""

    lines: list[str] = []
    for slot in merged.values():
        qty = slot["qty"]
        if isinstance(qty, float) and qty == int(qty):
            qty = int(qty)
        prefix = " ".join(str(p) for p in (qty, slot["unit"]) if p not in (None, "", 0))
        lines.append(f"{prefix} {slot['name']}".strip())
    return "\n".join(lines)


def _render_meal_card(i: int, slot: dict):
    rid = slot.get("recipe_id")
    recipe = library.get(rid) if rid else None

    with st.container(border=True):
        col_img, col_body, col_act = st.columns([1, 4, 1])
        with col_img:
            if recipe:
                _recipe_view.render_thumb(recipe, size=140)
            else:
                st.caption("🖼")
        with col_body:
            if recipe:
                st.markdown(f"### {i+1}. {recipe.get('title','(untitled)')}")
                meta = []
                if recipe.get("cuisines"):
                    meta.append(", ".join(recipe["cuisines"]))
                if recipe.get("proteins"):
                    meta.append("· " + "/".join(recipe["proteins"]))
                if recipe.get("ready_in_minutes"):
                    meta.append(f"· {recipe['ready_in_minutes']} min")
                if meta:
                    st.caption(" ".join(meta))
                # Note line: rating · cooked count · how it got here.
                note = []
                if recipe.get("rating"):
                    note.append(_recipe_view.star_str(recipe["rating"]))
                if recipe.get("times_cooked"):
                    note.append(f"Cooked {recipe['times_cooked']}×")
                if slot.get("added_via"):
                    note.append(f"added via {slot['added_via']}")
                if note:
                    st.caption(" · ".join(note))
            else:
                st.markdown(f"### {i+1}. _(missing recipe `{rid}`)_")
                st.caption("Recipe was deleted from the library after this plan was confirmed.")
        with col_act:
            if recipe and st.button("Open", key=f"mp_active_open_{i}",
                                    use_container_width=True):
                st.session_state.mealplan_cook_recipe_id = rid
                go("mealplan_cook")
