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
            auto_confirmed.append(item)
        else:
            needs_review.append(item)

    st.session_state.auto_confirmed_items = auto_confirmed
    st.session_state.matched_items = needs_review
