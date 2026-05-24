"""Preferences screen — manage saved product preferences."""

import json
import time

import streamlit as st

import preference_store

from screens._shared import go


def render():
    st.title("⚙ Preferences")
    st.caption("Your saved product preferences. These drive auto-matching on future runs.")

    col_nav, _ = st.columns([1, 5])
    with col_nav:
        if st.button("← Back to Home"):
            go("home")

    # Backup / restore
    with st.expander("💾 Backup & Restore"):
        st.caption(
            "Download a snapshot of all preferences, staples, and recent session "
            "log entries. Keep it somewhere safe in case you want to roll back."
        )
        snapshot = preference_store.export_data()
        st.download_button(
            "Download snapshot (.json)",
            data=json.dumps(snapshot, indent=2),
            file_name=f"smartcart_backup_{time.strftime('%Y%m%d_%H%M%S')}.json",
            mime="application/json",
        )
        uploaded = st.file_uploader("Restore from snapshot", type=["json"], key="restore_upload")
        if uploaded is not None:
            try:
                data = json.loads(uploaded.read().decode("utf-8"))
                counts = preference_store.import_data(data)
                st.success(
                    f"Restored {counts['preferences']} preferences, "
                    f"{counts['staples']} staples, "
                    f"{counts['session_log']} session log entries."
                )
            except (json.JSONDecodeError, KeyError) as e:
                st.error(f"Couldn't read snapshot: {e}")

    st.divider()

    prefs = preference_store.get_all_preferences()

    if not prefs:
        st.info("No preferences saved yet. They'll appear here after your first shopping run.")
        return

    search = st.text_input("Search preferences", placeholder="Type to filter...")

    filtered = {
        k: v for k, v in prefs.items()
        if not search or search.lower() in k.lower() or search.lower() in v.get("product_name", "").lower()
    }

    st.write(f"**{len(filtered)} preference(s)**")
    st.divider()

    for item_key, pref in filtered.items():
        col_info, col_del = st.columns([6, 1])
        with col_info:
            saved_at = pref.get("saved_at", "")[:10] if pref.get("saved_at") else ""
            source = pref.get("source", "review")
            st.write(
                f"**{item_key}** → "
                f"{pref.get('brand','')} {pref.get('product_name','')} "
                f"· {pref.get('size','')} "
                f"· {'${:.2f}'.format(pref['price']) if pref.get('price') else ''}"
            )
            st.caption(f"Saved {saved_at} via {source}")
        with col_del:
            if st.button("Delete", key=f"del_pref_{item_key}"):
                preference_store.delete_preference(item_key)
                st.success(f"Deleted preference for {item_key}.")
                st.rerun()
        st.divider()

    col_staples, _ = st.columns([2, 4])
    with col_staples:
        if st.button("📋 Go to Staples →"):
            go("staples")
