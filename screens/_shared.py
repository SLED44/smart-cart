"""
screens/_shared.py
------------------
Helpers shared across screen modules. Kept private (underscore prefix) so
they don't appear in the public `screens` package surface.
"""

import os

import streamlit as st

from supabase_kv import kv_get, kv_put

KEY_LOCATION_ID = "kroger_location_id"


# ---------------------------------------------------------------------------
# Top-level tab nav (🛍 Grocery / 🍳 Meal Planner)
# ---------------------------------------------------------------------------
# Diverges from PRD §13.4 (which had a single home with a 'Plan meals' card).
# Two distinct landing experiences scale better as the meal-plan feature grows
# past 5+ screens.

TAB_GROCERY = "grocery"
TAB_MEALPLAN = "mealplan"

# Which tab each screen belongs to. Login/connect_kroger are tab-less
# (special-cased in render_tab_bar).
SCREEN_TAB = {
    "home":                  TAB_GROCERY,
    "preview":               TAB_GROCERY,
    "item_filter":           TAB_GROCERY,
    "sale_scan":             TAB_GROCERY,
    "review":                TAB_GROCERY,
    "summary":               TAB_GROCERY,
    "preferences":           TAB_GROCERY,
    "staples":               TAB_GROCERY,
    "store_setup":           TAB_GROCERY,
    "connect_kroger":        TAB_GROCERY,
    "mealplan_home":         TAB_MEALPLAN,
    "mealplan_propose":      TAB_MEALPLAN,
    "mealplan_swap":         TAB_MEALPLAN,
    "mealplan_active":       TAB_MEALPLAN,
    "mealplan_cook":         TAB_MEALPLAN,
    "mealplan_rules":        TAB_MEALPLAN,
    "mealplan_library":      TAB_MEALPLAN,
    "mealplan_bootstrap":    TAB_MEALPLAN,
    "mealplan_paste_recipe": TAB_MEALPLAN,
    "mealplan_state_import": TAB_MEALPLAN,
}

# Where each tab's "home" lives.
TAB_HOME = {
    TAB_GROCERY:  "home",
    TAB_MEALPLAN: "mealplan_home",
}


def current_tab() -> str:
    """Resolve the active tab from the current screen."""
    return SCREEN_TAB.get(st.session_state.get("screen", "home"), TAB_GROCERY)


def render_tab_bar():
    """
    Render the top-level tab bar. Call from main.py before dispatching to
    a screen. Skips login (you're not yet authed) and the OAuth callback
    handler — anything not in SCREEN_TAB renders the bar pointing at the
    grocery default.
    """
    screen = st.session_state.get("screen", "home")
    if screen == "login":
        return
    cur = current_tab()
    col_groc, col_meal = st.columns(2)
    with col_groc:
        if st.button(
            "🛍  Grocery",
            type="primary" if cur == TAB_GROCERY else "secondary",
            use_container_width=True,
            key="tabbar_grocery",
        ):
            if cur != TAB_GROCERY:
                go(TAB_HOME[TAB_GROCERY])
    with col_meal:
        if st.button(
            "🍳  Meal Planner",
            type="primary" if cur == TAB_MEALPLAN else "secondary",
            use_container_width=True,
            key="tabbar_mealplan",
        ):
            if cur != TAB_MEALPLAN:
                go(TAB_HOME[TAB_MEALPLAN])


def go(screen: str):
    """Navigate to a screen and rerun."""
    st.session_state.screen = screen
    st.rerun()


def pending_oauth_key(state: str) -> str:
    """Supabase key for an in-flight OAuth round-trip, keyed by CSRF state."""
    return f"oauth_pending:{state}"


def get_location_id() -> str:
    """Resolve the store location ID. KV wins over .env so the value
    persists across container restarts on Streamlit Cloud."""
    return (kv_get(KEY_LOCATION_ID, "") or os.getenv("KROGER_LOCATION_ID", "")).strip()


def save_location_id(location_id: str) -> None:
    """Persist location_id to Supabase and reflect it in the live process env
    so downstream modules (product_matcher, sale_scanner) read it via os.getenv."""
    kv_put(KEY_LOCATION_ID, location_id)
    os.environ["KROGER_LOCATION_ID"] = location_id


# Per-item review-screen state is keyed by queue index (current_primary_3,
# qty_3, save_pref_3, ...). These survive any fixed-key reset list, so a
# second run in the same browser session would inherit the previous run's
# selections — including a swapped product silently going to the cart.
_REVIEW_KEY_PREFIXES = (
    "current_primary_", "current_mt_", "qty_", "save_pref_",
    "manual_search_", "swap_",
)


def clear_review_widget_state():
    """Delete all per-index review-screen keys. Call whenever a run starts
    or resets. ('staple_qty_' does not match the 'qty_' prefix — safe.)"""
    for k in list(st.session_state.keys()):
        if isinstance(k, str) and k.startswith(_REVIEW_KEY_PREFIXES):
            del st.session_state[k]


def split_auto_confirmed():
    """
    Before entering the review queue, pull out items that can be
    auto-confirmed — no human decision needed.

    Auto-confirm criteria (all must be true):
        1. match_type == "Preferred Match"  (has a saved preference, in stock)
        2. item_key is NOT in the sale scan alert set
           (a sale alternative exists → send to review so user can see it)
    """
    matched = st.session_state.matched_items
    scan_result = st.session_state.scan_result or {}

    sale_alert_keys = {
        alert["item_key"]
        for alert in scan_result.get("sale_alerts", [])
    }

    auto_confirmed = []
    needs_review = []

    for item in matched:
        if (
            item.get("match_type") == "Preferred Match"
            and item.get("item_key") not in sale_alert_keys
        ):
            # Weight-coverage math normally happens on the review screen,
            # which auto-confirmed items never reach. Apply it here so
            # "2 lbs ground beef" against a 1-lb preferred pack buys 2,
            # not 1.
            notes = item.get("notes", "")
            primary = item.get("primary")
            if notes and primary:
                import product_matcher
                smart = product_matcher.suggested_quantity(notes, primary)
                if smart is not None:
                    item = {**item, "quantity": float(smart)}
            auto_confirmed.append(item)
        else:
            needs_review.append(item)

    st.session_state.auto_confirmed_items = auto_confirmed
    st.session_state.matched_items = needs_review
