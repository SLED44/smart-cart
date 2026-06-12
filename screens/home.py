"""Home screen — stats, nav, paste grocery list, optional staples toggle."""

import streamlit as st

import kroger_auth
import list_parser
import preference_store
from sc_design import stat_card

from screens._shared import get_location_id, go
from applog import get_logger, log_items

_log = get_logger(__name__)


def render():
    st.title("🛒 SmartCart")

    # Surface any post-OAuth banner once
    if st.session_state.kroger_auth_msg:
        st.success(st.session_state.kroger_auth_msg)
        st.session_state.kroger_auth_msg = None

    # Nav bar
    col_nav1, col_nav2, col_nav3 = st.columns([1, 1, 6])
    with col_nav1:
        if st.button("⚙ Preferences"):
            go("preferences")
    with col_nav2:
        if st.button("📋 Staples"):
            go("staples")

    st.divider()

    # Kroger authorization check
    kroger_status = kroger_auth.token_status()
    if kroger_status["status"] == "not_authorized":
        st.warning("⚠ Kroger account not connected. Connect to enable matching and cart posting.")
        if st.button("🔗 Connect Kroger", type="primary"):
            go("connect_kroger")
        return

    # Check for store setup
    location_id = get_location_id()
    if not location_id or location_id == "your_store_location_id_here":
        st.warning("⚠ Store location not set. Complete store setup before starting a session.")
        if st.button("🔍 Find My Store", type="primary"):
            go("store_setup")
        return

    # Stats
    summary = preference_store.data_summary()
    col_a, col_b, col_c = st.columns(3)
    with col_a:
        st.html(stat_card(
            tone="green", glyph="★",
            label="Saved preferences",
            value=summary["preference_count"],
            sub="brands you trust",
        ))
    with col_b:
        st.html(stat_card(
            tone="grape", glyph="📌",
            label="Staples on file",
            value=summary["staple_count"],
            sub="auto-included",
        ))
    with col_c:
        st.html(stat_card(
            tone="sky", glyph="🛒",
            label="Runs this year",
            value=summary["session_count"],
            sub="≈ once a week",
        ))

    st.divider()
    st.subheader("What are we cooking this week? 🍳")
    st.caption("Paste your list — bullets, prose, categorised, mixed. We'll figure it out.")

    if st.session_state.get("meal_plan_handoff"):
        st.success("📅 Loaded from your meal plan — add any pantry staples "
                   "below, then **Sort This Out For Me** to continue.")

    raw_list = st.text_area(
        "Grocery list",
        value=st.session_state.raw_list,
        height=220,
        placeholder="Examples:\n• 2 lbs chicken breast\n• 1 gallon whole milk\n• bananas\n\nOr paste directly from your Claude chat.",
        label_visibility="collapsed",
    )
    st.session_state.raw_list = raw_list

    # Staples option
    st.divider()
    add_staples = False
    if summary["staple_count"] == 0:
        st.info("No staples saved yet. Add them in the Staples screen.")
    else:
        with st.expander(f"📌 Add staples to this run ({summary['staple_count']} on file)"):
            staples = preference_store.get_all_staples()
            if "staple_selections" not in st.session_state:
                st.session_state.staple_selections = {}

            col_all, col_none = st.columns([1, 1])
            with col_all:
                if st.button("Select All", key="staples_all"):
                    for s in staples:
                        st.session_state.staple_selections[s["item_key"]] = True
                    st.rerun()
            with col_none:
                if st.button("Select None", key="staples_none"):
                    for s in staples:
                        st.session_state.staple_selections[s["item_key"]] = False
                    st.rerun()

            st.divider()
            grouped_staples = {}
            for s in staples:
                grouped_staples.setdefault(s.get("category", "Other"), []).append(s)

            for cat, items in grouped_staples.items():
                st.write(f"**{cat}**")
                for s in items:
                    key = s["item_key"]
                    default = st.session_state.staple_selections.get(key, True)
                    col_cb, col_qty = st.columns([3, 1])
                    with col_cb:
                        checked = st.checkbox(
                            s["display_name"],
                            value=default,
                            key=f"staple_cb_{key}"
                        )
                        st.session_state.staple_selections[key] = checked
                    with col_qty:
                        qty_val = st.number_input(
                            "qty",
                            min_value=1,
                            value=int(s.get("default_quantity", 1)),
                            step=1,
                            key=f"staple_qty_{key}",
                            label_visibility="collapsed"
                        )
                        s["session_quantity"] = qty_val

            selected_count = sum(1 for v in st.session_state.staple_selections.values() if v)
            st.session_state.staples_added = selected_count > 0
            add_staples = st.session_state.staples_added

    st.divider()
    if st.button("Sort This Out For Me →", type="primary", use_container_width=True):
        if not raw_list.strip() and not (add_staples and summary["staple_count"] > 0):
            st.error("Please paste a grocery list or add staples before continuing.")
            return

        with st.spinner("Parsing your list with Claude..."):
            try:
                if raw_list.strip():
                    parsed = list_parser.parse_grocery_list(raw_list)
                    st.session_state.parsed_result = parsed
                else:
                    # Staples only — create empty parse result
                    st.session_state.parsed_result = {
                        "items": [], "raw_text": "", "item_count": 0, "parse_warnings": []
                    }
            except (ValueError, RuntimeError) as e:
                st.error(f"Parsing failed: {e}")
                return

        # Combine parsed items + staples into combined_items before item filter
        parsed = st.session_state.parsed_result
        # Collapse duplicate item_keys (same ingredient parsed from two lines,
        # e.g. a meal-plan list with "3 tbsp olive oil" + "0.5 cup olive oil").
        # Duplicate keys crash item_filter's checkboxes (filter_cb_<key>).
        items = list_parser.validate_parsed_items(list(parsed["items"]))

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
        log_items(_log, "home.combined(+staples)", items)
        # Meal-plan load has been consumed; don't show the banner after parse.
        st.session_state.pop("meal_plan_handoff", None)
        go("item_filter")
