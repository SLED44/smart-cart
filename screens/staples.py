"""Staples screen — manage weekly recurring items."""

import streamlit as st

import preference_store

from screens._shared import go


def render():
    st.title("📋 Staples")
    st.caption("Items you buy on most shopping runs. Triggered manually per session.")

    col_nav, _ = st.columns([1, 5])
    with col_nav:
        if st.button("← Back to Home"):
            go("home")

    st.divider()
    st.subheader("Add a Staple")

    with st.form("add_staple_form", clear_on_submit=True):
        col_name, col_qty, col_cat = st.columns([3, 1, 2])
        with col_name:
            display_name = st.text_input("Item name", placeholder="e.g. Whole Milk")
        with col_qty:
            default_qty = st.number_input("Qty", min_value=1, value=1, step=1)
        with col_cat:
            category = st.selectbox(
                "Category",
                ["Dairy", "Produce", "Meat", "Frozen", "Pantry",
                 "Beverages", "Bakery", "Household", "Personal Care", "Other"]
            )
        submitted = st.form_submit_button("Add Staple", type="primary")
        if submitted and display_name.strip():
            preference_store.save_staple({
                "display_name":    display_name.strip(),
                "default_quantity": int(default_qty),
                "category":        category,
            })
            st.success(f"Added {display_name} to staples.")
            st.rerun()

    st.divider()
    st.subheader("Your Staples")

    staples = preference_store.get_all_staples()
    if not staples:
        st.info("No staples yet. Add items above that you buy every week.")
        return

    grouped = {}
    for s in staples:
        cat = s.get("category", "Other")
        grouped.setdefault(cat, []).append(s)

    for cat, items in grouped.items():
        st.write(f"**{cat}**")
        for staple in items:
            col_name, col_qty, col_pref, col_del = st.columns([3, 1, 2, 1])
            with col_name:
                st.write(staple["display_name"])
            with col_qty:
                st.write(f"×{staple.get('default_quantity', 1)}")
            with col_pref:
                upc = staple.get("preferred_upc")
                if upc:
                    pref = preference_store.get_preference(staple["item_key"])
                    if pref:
                        st.caption(f"★ {pref.get('brand','')} {pref.get('size','')}")
                    else:
                        st.caption("★ Preference linked")
                else:
                    st.caption("No preference yet")
            with col_del:
                if st.button("✕", key=f"del_staple_{staple['item_key']}"):
                    preference_store.delete_staple(staple["item_key"])
                    st.rerun()
        st.divider()

    col_prefs, _ = st.columns([2, 4])
    with col_prefs:
        if st.button("⚙ Go to Preferences →"):
            go("preferences")
