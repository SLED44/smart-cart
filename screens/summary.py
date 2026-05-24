"""Summary screen — cart posted, open City Market to check out."""

import streamlit as st

import cart_manager

from screens._shared import go


def render():
    result = st.session_state.cart_result
    if not result:
        go("home")
        return

    st.title("✅ Your cart's loaded.")
    st.caption("Take it from here, City Market.")
    st.divider()

    auto_confirmed = st.session_state.auto_confirmed_items

    col1, col2, col3, col4 = st.columns(4)
    col1.metric("Added to Cart",   result["success_count"])
    col2.metric("Skipped",         len(st.session_state.skipped_items))
    col3.metric("Not Found",       len(st.session_state.not_found_items))
    col4.metric("New Preferences", st.session_state.new_prefs_count)

    if result["estimated_total"] > 0:
        st.write(f"**Estimated cart total:** ${result['estimated_total']:.2f}")

    st.divider()
    st.subheader("Open Kroger to Complete Checkout")
    st.write("Your items have been added. Select a pickup time and confirm payment in City Market.")

    st.link_button("🛒 Open City Market Cart →", cart_manager.CITY_MARKET_CART_URL, use_container_width=True)

    if auto_confirmed:
        st.divider()
        with st.expander(f"⚡ Auto-confirmed ({len(auto_confirmed)} item{'s' if len(auto_confirmed) != 1 else ''}) — added without review"):
            st.caption("These items had a saved preference, were in stock, and had no sale alternative — so they were added automatically.")
            for item in auto_confirmed:
                primary = item.get("primary", {})
                brand = primary.get("brand", "")
                name = primary.get("product_name", "")
                size = primary.get("size", "")
                price = primary.get("promo_price") or primary.get("price")
                price_str = f" · ${price:.2f}" if price else ""
                qty = max(1, round(item.get("quantity", 1)))
                qty_str = f" ×{qty}" if qty > 1 else ""
                st.write(f"• **{item['item_name']}** → {brand} {name} {size}{price_str}{qty_str}")

    if result["failure_count"] > 0:
        st.divider()
        st.subheader(f"⚠ {result['failure_count']} Item(s) Failed to Add")
        for item in result["failed"]:
            primary = item.get("primary", {})
            st.write(
                f"• **{item['item_name']}** — "
                f"{primary.get('brand','')} {primary.get('product_name','')} "
                f"({item.get('cart_error','Unknown error')})"
            )
        if st.button("Retry Failed Items"):
            with st.spinner("Retrying..."):
                retry_result = cart_manager.retry_failed_items(result["failed"])
                result["succeeded"].extend(retry_result["succeeded"])
                result["failed"] = retry_result["failed"]
                result["success_count"] += retry_result["success_count"]
                result["failure_count"] = retry_result["failure_count"]
                st.session_state.cart_result = result
                st.rerun()

    if st.session_state.skipped_items:
        with st.expander(f"Skipped items ({len(st.session_state.skipped_items)})"):
            for item in st.session_state.skipped_items:
                st.write(f"• {item['item_name']}")

    if st.session_state.not_found_items:
        with st.expander(f"Not found on Kroger ({len(st.session_state.not_found_items)})"):
            for item in st.session_state.not_found_items:
                st.write(f"• {item['item_name']} — add manually in City Market app")

    st.divider()
    if st.button("← Start New Shopping Run", use_container_width=True):
        # Reset session state for a fresh run. Defer the re-init to main's _init_state
        # by importing it lazily — avoids a circular import at module load.
        from main import _init_state
        for key in ["raw_list", "parsed_result", "staples_added", "combined_items",
                    "scan_result", "matched_items", "review_index", "confirmed_items",
                    "skipped_items", "not_found_items", "new_prefs_count",
                    "cart_result", "review_history", "sale_switches",
                    "auto_confirmed_items", "item_filter_selections"]:
            if key in st.session_state:
                del st.session_state[key]
        _init_state()
        go("home")
