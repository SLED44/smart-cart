"""Preview screen — review normalised list before matching."""

import streamlit as st

import list_parser
import preference_store

from screens._shared import go


def render():
    st.title("Review Your List")
    st.caption("Check that everything was parsed correctly before we search Kroger.")
    st.divider()

    parsed = st.session_state.parsed_result
    if not parsed:
        go("home")
        return

    items = list(parsed["items"])  # copy

    # Add staples if requested
    if st.session_state.staples_added:
        staples = preference_store.get_all_staples()
        selections = st.session_state.get("staple_selections", {})
        existing_keys = {i["item_key"] for i in items}
        for staple in staples:
            key = staple["item_key"]
            if not selections.get(key, True):
                continue
            if key not in existing_keys:
                qty = float(staple.get("session_quantity") or staple.get("default_quantity", 1))
                items.append({
                    "item_name":      staple["display_name"],
                    "item_key":       key,
                    "quantity":       qty,
                    "unit":           "",
                    "category":       staple.get("category", "Other"),
                    "notes":          "staple",
                    "has_preference": staple.get("preferred_upc") is not None,
                })

    st.session_state.combined_items = items

    if parsed.get("parse_warnings"):
        with st.expander(f"⚠ {len(parsed['parse_warnings'])} parsing note(s)", expanded=False):
            for w in parsed["parse_warnings"]:
                st.caption(f"• {w}")

    st.write(f"**{len(items)} items** ready to match:")

    grouped = list_parser.group_items_by_category(items)
    for category, cat_items in grouped.items():
        st.write(f"**{category}**")
        for item in cat_items:
            qty = list_parser.format_quantity(item["quantity"], item["unit"])
            notes = f" *({item['notes']})*" if item["notes"] and item["notes"] != "staple" else ""
            pref = " ★" if item["has_preference"] else ""
            staple_tag = " 📌" if item.get("notes") == "staple" else ""
            st.write(f"  • {qty} {item['item_name']}{notes}{pref}{staple_tag}")

    st.caption("★ = saved preference exists   📌 = staple")
    st.divider()

    col1, col2 = st.columns(2)
    with col1:
        if st.button("← Back", use_container_width=True):
            go("home")
    with col2:
        if st.button("Looks Good — Find Products →", type="primary", use_container_width=True):
            go("item_filter")
