"""Item filter screen — checklist before matching kicks off."""

import streamlit as st

import kroger_auth
import list_parser
import product_matcher
import sale_scanner

from screens._shared import go, split_auto_confirmed


def render():
    """
    Shows every parsed item as a checklist before matching begins.
    User unchecks items they already have or don't need this run.
    Only checked items proceed to the matching pipeline.
    """
    items = st.session_state.combined_items
    if not items:
        go("home")
        return

    st.title("Anything we don't need this run?")
    st.caption("Already got it at home? Uncheck it. We'll only grab what you're missing.")
    st.divider()

    # Surface any parse warnings here since we skipped the preview screen
    parsed = st.session_state.parsed_result
    if parsed and parsed.get("parse_warnings"):
        with st.expander(f"⚠ {len(parsed['parse_warnings'])} parsing note(s)", expanded=False):
            for w in parsed["parse_warnings"]:
                st.caption(f"• {w}")

    # Initialise selections: default all to True (checked) on first visit
    selections = st.session_state.item_filter_selections
    for item in items:
        if item["item_key"] not in selections:
            selections[item["item_key"]] = True

    # Select All / Deselect All
    col_all, col_none, col_spacer = st.columns([1, 1, 4])
    with col_all:
        if st.button("✓ Select All"):
            for item in items:
                selections[item["item_key"]] = True
            st.session_state.item_filter_selections = selections
            st.rerun()
    with col_none:
        if st.button("✗ Deselect All"):
            for item in items:
                selections[item["item_key"]] = False
            st.session_state.item_filter_selections = selections
            st.rerun()

    st.divider()

    grouped = list_parser.group_items_by_category(items)
    for category, cat_items in grouped.items():
        st.write(f"**{category}**")
        for item in cat_items:
            key = item["item_key"]
            qty = list_parser.format_quantity(item["quantity"], item["unit"])
            label = f"{qty} {item['item_name']}"
            if item.get("notes") and item["notes"] != "staple":
                label += f"  *({item['notes']})*"
            if item.get("notes") == "staple":
                label += "  📌"
            checked = st.checkbox(
                label,
                value=selections.get(key, True),
                key=f"filter_cb_{key}",
            )
            selections[key] = checked
        st.write("")

    st.session_state.item_filter_selections = selections

    checked_count = sum(1 for item in items if selections.get(item["item_key"], True))

    st.divider()
    col_back, col_continue = st.columns([1, 2])
    with col_back:
        if st.button("← Back", use_container_width=True):
            go("home")
    with col_continue:
        btn_label = f"Hunt Down {checked_count} Item{'s' if checked_count != 1 else ''} →"
        if st.button(btn_label, type="primary", use_container_width=True, disabled=checked_count == 0):
            st.session_state.combined_items = [
                item for item in items
                if selections.get(item["item_key"], True)
            ]
            _run_matching_pipeline()

    if checked_count == 0:
        st.warning("No items selected. Check at least one item to continue.")


def _run_matching_pipeline():
    """Run sale scan then product matching, then navigate to next screen."""
    items = st.session_state.combined_items

    with st.spinner("🏷 Scoping for deals…"):
        try:
            scan_result = sale_scanner.scan_for_sale_alternatives(items)
            st.session_state.scan_result = scan_result
        except kroger_auth.NeedsAuthorization:
            go("connect_kroger")
            return
        except Exception as e:
            st.session_state.scan_result = {"sale_alerts": [], "scanned_count": 0, "alert_count": 0}
            st.warning(f"Sale scan skipped: {e}")

    with st.spinner(f"🔎 Hunting down {len(items)} items… 5 workers searching the Kroger catalog in parallel."):
        try:
            matched = product_matcher.match_items(items)
            st.session_state.matched_items = matched
        except kroger_auth.NeedsAuthorization:
            go("connect_kroger")
            return
        except RuntimeError as e:
            st.error(f"Product matching failed: {e}")
            return

    st.session_state.review_index = 0
    st.session_state.confirmed_items = []
    st.session_state.skipped_items = []
    st.session_state.not_found_items = []
    st.session_state.new_prefs_count = 0
    st.session_state.review_history = []
    st.session_state.sale_switches = []
    st.session_state.auto_confirmed_items = []

    if st.session_state.scan_result.get("alert_count", 0) > 0:
        go("sale_scan")
    else:
        split_auto_confirmed()
        go("review")
