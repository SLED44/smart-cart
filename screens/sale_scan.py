"""Sale scan screen — surface on-sale alternatives before review."""

import streamlit as st

import preference_store
import sale_scanner
from sc_design import savings_card

from screens._shared import go, split_auto_confirmed


def render():
    scan = st.session_state.scan_result
    alerts = scan.get("sale_alerts", [])

    st.title("🏷 We snagged a deal.")
    st.divider()

    switches = list(st.session_state.sale_switches)

    for alert in alerts:
        item_key = alert["item_key"]
        item_name = alert["item_name"]
        sale_prod = alert["sale_product"]
        sale_price = sale_prod.get("promo_price") or sale_prod.get("price")

        st.html(savings_card(alert))

        is_switched = item_key in switches
        col_a, col_b = st.columns(2)
        with col_a:
            if st.button(
                "✓ Snagged for this run" if is_switched else "Snag the Deal",
                key=f"switch_{item_key}",
                type="primary" if not is_switched else "secondary",
            ):
                if is_switched:
                    switches.remove(item_key)
                else:
                    switches.append(item_key)
                st.session_state.sale_switches = switches
                st.rerun()
        with col_b:
            if st.button("Always grab the sale one", key=f"perm_{item_key}"):
                preference_store.save_preference(
                    item_key,
                    {
                        "kroger_upc":   sale_prod.get("upc", ""),
                        "product_name": sale_prod.get("product_name", ""),
                        "brand":        sale_prod.get("brand", ""),
                        "size":         sale_prod.get("size", ""),
                        "price":        sale_price,
                    },
                    source="manual",
                )
                if item_key not in switches:
                    switches.append(item_key)
                st.session_state.sale_switches = switches
                st.success(f"Saved as new preference for {item_name}.")
                st.rerun()

        st.divider()

    col1, col2 = st.columns(2)
    with col1:
        if st.button("No thanks, use my preferences", use_container_width=True):
            st.session_state.sale_switches = []
            _apply_switches_and_go_review()
    with col2:
        label = f"Continue with {len(switches)} switch(es) →" if switches else "Continue →"
        if st.button(label, type="primary", use_container_width=True):
            _apply_switches_and_go_review()


def _apply_switches_and_go_review():
    """Apply sale scan switches to matched items then go to review."""
    if st.session_state.sale_switches:
        st.session_state.matched_items = sale_scanner.apply_sale_switches(
            st.session_state.matched_items,
            st.session_state.sale_switches,
            st.session_state.scan_result,
        )
    split_auto_confirmed()
    go("review")
