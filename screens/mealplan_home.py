"""
Meal Planner home — landing for the meal-plan tab.

What it shows:
    - Library status (count, favorites, missing pieces)
    - Current week's confirmed plan (if any) with quick link to active view
    - In-progress lineup (if any) with Resume / Discard
    - Primary CTA: "Plan this week" with N input
    - Settings expander: Rules / Bootstrap / Library / Paste / State import
"""

import streamlit as st

from mealplan import library
from mealplan.rules import load_rules
from sc_design import stat_card
from supabase_kv import kv_delete, kv_get

from screens._shared import go

KEY_PENDING_LINEUP = "pending_lineup"
KEY_CURRENT_PLAN = "current_plan"

# Admin links shown in the Settings expander. Filtered against main.SCREENS
# so unfinished phases don't show as dead links.
_SETTINGS_LINKS = (
    ("mealplan_rules",        "⚙ Rules"),
    ("mealplan_bootstrap",    "🌱 Bootstrap library"),
    ("mealplan_library",      "📚 Browse library"),
    ("mealplan_paste_recipe", "📝 Paste a recipe"),
    ("mealplan_state_import", "📥 Import state"),
)


def render():
    st.title("🍳 Meal Planner")
    st.caption("Build a weekly lineup that respects your rules, hand the grocery list "
               "to SmartCart, and capture what worked.")

    summary = library.data_summary()
    rules = load_rules()
    pending = kv_get(KEY_PENDING_LINEUP, None)
    current = kv_get(KEY_CURRENT_PLAN, None)

    _render_stats(summary, rules, current)

    st.divider()

    # The primary action surfaces based on what state we're in.
    if pending and (pending.get("meals") or pending.get("titles")):
        _render_pending_section(pending, rules)
    elif current and current.get("meals"):
        _render_current_plan_section(current)
    else:
        _render_plan_new_section(summary, rules)

    st.divider()
    _render_settings_expander(summary)


# ---------------------------------------------------------------------------
# Sections
# ---------------------------------------------------------------------------

def _render_stats(summary: dict, rules: dict, current: dict | None):
    by_status = summary.get("by_status") or {}
    col_a, col_b, col_c = st.columns(3)
    with col_a:
        st.html(stat_card(
            tone="green", glyph="📚",
            label="Library size",
            value=summary["total"],
            sub=f"{by_status.get('favorite', 0)} favorites · "
                f"{by_status.get('never_again', 0)} excluded",
        ))
    with col_b:
        cw = int(((rules.get("state") or {}).get("current_week")) or 1)
        st.html(stat_card(
            tone="grape", glyph="📅",
            label="Current week",
            value=cw,
            sub="rules-engine week counter",
        ))
    with col_c:
        plan_status = "Ready" if current and current.get("meals") else "—"
        st.html(stat_card(
            tone="sky", glyph="🗓",
            label="This week's plan",
            value=len(current.get("meals", [])) if current else 0,
            sub=f"meals · {plan_status}",
        ))


def _render_plan_new_section(summary: dict, rules: dict):
    st.subheader("Plan this week")
    if summary["total"] < 5:
        st.warning(
            f"Library only has {summary['total']} recipe(s). The planner needs at "
            f"least a handful to make sensible picks. Run **🌱 Bootstrap library** "
            f"first (Settings below)."
        )

    default_n = int(((rules.get("household") or {}).get("meals_per_week_default")) or 5)
    n = int(st.number_input(
        "How many meals do you want to plan?",
        min_value=1, max_value=7, value=default_n, step=1,
        key="mph_plan_n",
    ))

    disabled = summary["total"] == 0
    if st.button(
        f"Plan {n} meal{'s' if n != 1 else ''} →",
        type="primary", use_container_width=True,
        disabled=disabled, key="mph_plan_start",
    ):
        # Stash N for the propose screen; it'll generate the lineup on entry.
        st.session_state.mealplan_propose_n = n
        st.session_state.mealplan_propose_fresh = True  # propose screen sees this
                                                        # → generate, then clear
        go("mealplan_propose")


def _render_pending_section(pending: dict, rules: dict):
    st.subheader("You have a plan in progress")
    meals = pending.get("meals") or []
    titles = pending.get("titles") or []
    if meals:
        st.caption(f"Last touched: {pending.get('updated_at', 'unknown')[:19] if pending.get('updated_at') else ''}")
        st.write(f"**{len(meals)} slot(s) so far:**")
        for slot in meals:
            recipe = library.get(slot.get("recipe_id")) if slot.get("recipe_id") else None
            label = recipe["title"] if recipe else f"(missing recipe `{slot.get('recipe_id')}`)"
            st.write(f"  • {label}")
    elif titles:
        st.caption("Imported from your rules-doc state. Titles only — no recipe ids yet.")
        for t in titles:
            st.write(f"  • {t}")

    col_resume, col_discard = st.columns([2, 1])
    with col_resume:
        if st.button("Resume planning →", type="primary",
                     use_container_width=True, key="mph_resume"):
            go("mealplan_propose")
    with col_discard:
        if st.button("Discard", key="mph_discard", use_container_width=True):
            kv_delete(KEY_PENDING_LINEUP)
            st.rerun()


def _render_current_plan_section(current: dict):
    st.subheader("This week's plan")
    meals = current.get("meals") or []
    st.caption(f"Confirmed {current.get('confirmed_at', '')[:10] if current.get('confirmed_at') else ''} · "
               f"week #{current.get('week_number','?')}")
    for slot in meals:
        recipe = library.get(slot.get("recipe_id"))
        label = recipe["title"] if recipe else f"(missing `{slot.get('recipe_id')}`)"
        st.write(f"  • {label}")

    col_active, col_new = st.columns(2)
    with col_active:
        if st.button("📅 Open this week's plan", type="primary",
                     use_container_width=True, key="mph_open_active"):
            go("mealplan_active")
    with col_new:
        if st.button("🔄 Plan a new week (replaces current)",
                     use_container_width=True, key="mph_replan"):
            go("mealplan_propose")  # propose will see no pending → generate fresh


def _render_settings_expander(summary: dict):
    import main  # router owns the registry
    available = [(sid, label) for sid, label in _SETTINGS_LINKS
                 if sid in main.SCREENS]
    if not available:
        return
    with st.expander("⚙ Settings + admin"):
        st.caption(
            "Configure rules, grow the library, paste a recipe Claude.ai normalised "
            "for you, or import your existing rules-doc state."
        )
        cols = st.columns(min(3, len(available)))
        for i, (sid, label) in enumerate(available):
            with cols[i % len(cols)]:
                if st.button(label, key=f"mph_set_{sid}", use_container_width=True):
                    go(sid)
