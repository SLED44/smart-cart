"""
Active plan screen — this week's confirmed lineup.

Shows N meals from ``current_plan`` as tappable cards. Tapping opens the
cooking view (Phase 7 — currently stubbed). "Generate grocery list" button
hands off to SmartCart's preview screen (Phase 8 — currently stubbed).
"""

import streamlit as st

from mealplan import library
from supabase_kv import kv_get

from screens._shared import go

KEY_CURRENT_PLAN = "current_plan"


def render():
    plan = kv_get(KEY_CURRENT_PLAN, None)
    if not plan or not plan.get("meals"):
        st.title("📅 No active plan")
        st.caption("You haven't confirmed a plan yet. Build one from the meal-planner home.")
        if st.button("← Back to meal-planner home", key="mp_active_back_empty"):
            go("mealplan_home")
        return

    st.title("📅 This week's plan")
    st.caption(
        f"Week #{plan.get('week_number','?')} · "
        f"confirmed {plan.get('confirmed_at','')[:10] if plan.get('confirmed_at') else ''}"
    )

    col_back, col_replan, col_grocery = st.columns([1, 2, 2])
    with col_back:
        if st.button("← Home", key="mp_active_back"):
            go("mealplan_home")
    with col_replan:
        if st.button("🔄 Plan new (replaces this)",
                     use_container_width=True, key="mp_active_replan"):
            # Discards confirmed plan effectively by routing to a fresh propose flow.
            # The pending lineup gets a new session.
            st.session_state.mealplan_propose_fresh = True
            go("mealplan_propose")
    with col_grocery:
        if st.button("🛒 Generate grocery list →",
                     type="primary", use_container_width=True,
                     disabled=True, key="mp_active_grocery"):
            pass
        st.caption("(Phase 8)")

    st.divider()

    meals = plan.get("meals") or []
    for i, slot in enumerate(meals):
        _render_meal_card(i, slot)


def _render_meal_card(i: int, slot: dict):
    rid = slot.get("recipe_id")
    recipe = library.get(rid) if rid else None

    with st.container(border=True):
        col_img, col_body, col_act = st.columns([1, 4, 1])
        with col_img:
            if recipe and recipe.get("image_url"):
                st.image(recipe["image_url"], width=140)
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
                if slot.get("added_via"):
                    st.caption(f"added via: {slot['added_via']}")
                if recipe.get("times_cooked"):
                    st.caption(f"cooked {recipe['times_cooked']} time(s)")
            else:
                st.markdown(f"### {i+1}. _(missing recipe `{rid}`)_")
                st.caption("Recipe was deleted from the library after this plan was confirmed.")
        with col_act:
            if recipe and st.button("Open", key=f"mp_active_open_{i}",
                                    use_container_width=True):
                st.session_state.mealplan_cook_recipe_id = rid
                go("mealplan_cook")
