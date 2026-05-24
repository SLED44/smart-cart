"""Review screen — item-by-item product review queue."""

import os

import streamlit as st

import cart_manager
import kroger_auth
import preference_store
import product_matcher
from sc_design import match_badge, product_card

from screens._shared import go


def render():
    matched = st.session_state.matched_items
    idx = st.session_state.review_index

    if not matched or idx >= len(matched):
        _post_cart_and_go_summary()
        return

    item = matched[idx]
    total = len(matched)
    confirmed = len(st.session_state.confirmed_items)
    skipped = len(st.session_state.skipped_items)
    remaining = total - idx

    st.progress(idx / total)
    st.caption(f"Item {idx + 1} of {total}")

    item_name = item["item_name"]
    match_type = item.get("match_type", "Best Match")
    primary = item.get("primary")
    alts = item.get("alternatives", [])

    # Current selection (can be swapped to an alt)
    if f"current_primary_{idx}" not in st.session_state:
        st.session_state[f"current_primary_{idx}"] = primary
    current = st.session_state[f"current_primary_{idx}"]
    current_mt = st.session_state.get(f"current_mt_{idx}", match_type)

    st.subheader(item_name)
    st.html(match_badge(current_mt))

    # Show original requested quantity for butcher/weight items
    item_notes = item.get("notes", "")
    if item_notes and any(w in item_notes.lower() for w in ["lb", "oz", "kg", "g"]):
        st.caption(f"📋 You asked for: **{item_notes}**")

    # OOS banner
    if match_type == "Preferred OOS":
        st.markdown(
            f'<div class="oos-banner">⚠ Your preferred product is out of stock. '
            f'Showing next best match.</div>',
            unsafe_allow_html=True,
        )

    # Sale callout during review
    scan_alerts = {
        a["item_key"]: a
        for a in (st.session_state.scan_result or {}).get("sale_alerts", [])
    }
    if item["item_key"] in scan_alerts and match_type not in ("On Sale Alt",):
        alert = scan_alerts[item["item_key"]]
        st.markdown(
            f'<div class="sale-banner">🏷 On Sale This Week: '
            f'<strong>{alert["sale_product"]["brand"]}</strong> is '
            f'{alert["savings_pct"]:.1f}% cheaper '
            f'(${alert["savings_amount"]:.2f} less). See alternatives below.</div>',
            unsafe_allow_html=True,
        )

    if current:
        _render_product_card(current)
    else:
        st.warning("No matching product found for this item.")

    # Quantity control
    qty_key = f"qty_{idx}"
    qty_suggested = f"qty_suggested_{idx}"

    if qty_key not in st.session_state:
        base_qty = float(item.get("quantity", 1))
        if current and item_notes:
            smart = product_matcher.suggested_quantity(item_notes, current)
            if smart is not None:
                base_qty = float(smart)
                st.session_state[qty_suggested] = True
        st.session_state[qty_key] = max(1.0, base_qty)

    if current and item_notes and not st.session_state.get(f"qty_locked_{idx}"):
        smart = product_matcher.suggested_quantity(item_notes, current)
        if smart is not None and not st.session_state.get(f"qty_user_edited_{idx}"):
            st.session_state[qty_key] = max(1.0, float(smart))

    col_qty, col_hint = st.columns([1, 2])
    with col_qty:
        prev_qty = st.session_state[qty_key]
        new_qty = st.number_input(
            "Quantity",
            min_value=1.0,
            step=1.0,
            value=st.session_state[qty_key],
            key=f"qty_input_{idx}",
        )
        if new_qty != prev_qty:
            st.session_state[f"qty_user_edited_{idx}"] = True
        st.session_state[qty_key] = new_qty
    with col_hint:
        if item_notes and current:
            smart = product_matcher.suggested_quantity(item_notes, current)
            if smart is not None:
                product_oz = product_matcher.parse_size_to_oz(current.get("size", ""))
                if product_oz:
                    st.caption(
                        f"Suggested: **{smart}** × {current.get('size','')} "
                        f"to cover {item_notes}"
                    )

    # Save as preferred toggle
    save_pref_key = f"save_pref_{idx}"
    if save_pref_key not in st.session_state:
        st.session_state[save_pref_key] = False

    if current and match_type != "Preferred Match":
        st.session_state[save_pref_key] = st.checkbox(
            f"Save as my preferred choice for {item_name}",
            value=st.session_state[save_pref_key],
            key=f"save_pref_cb_{idx}",
        )

    # Alternatives
    if alts:
        st.write("**Alternatives:**")
        for ai, alt in enumerate(alts[:3]):
            _render_alt_card(alt, ai, idx)

    # Manual search
    with st.expander("🔍 Search for a different product"):
        search_key = f"manual_search_term_{idx}"
        results_key = f"manual_search_results_{idx}"

        search_term = st.text_input(
            "Search Kroger",
            value=st.session_state.get(search_key, ""),
            placeholder=f"e.g. {item_name}",
            key=f"manual_search_input_{idx}",
        )

        if st.button("Search", key=f"manual_search_btn_{idx}"):
            if search_term.strip():
                with st.spinner("Searching Kroger..."):
                    results = _kroger_search_for_review(search_term.strip())
                st.session_state[search_key] = search_term
                st.session_state[results_key] = results
                st.rerun()

        search_results = st.session_state.get(results_key, [])
        if search_results:
            st.write(f"**{len(search_results)} result(s):**")
            for si, result in enumerate(search_results):
                _render_alt_card(result, f"s{si}", idx)
        elif st.session_state.get(search_key):
            st.caption("No results found. Try different keywords.")

    st.divider()

    col_back, col_skip, col_add = st.columns([1, 1, 2])
    with col_back:
        if idx > 0:
            if st.button("← Back"):
                _go_back_one()
                return
    with col_skip:
        if st.button("Skip"):
            _skip_current(item, idx)
            return
    with col_add:
        if current:
            if st.button("Add to Cart →", type="primary", use_container_width=True):
                _confirm_current(item, idx, current, new_qty)
                return
        else:
            if st.button("Skip (Not Found)", use_container_width=True):
                _skip_current(item, idx)
                return

    st.markdown(
        f'<div class="footer-bar">'
        f'{confirmed} confirmed · {skipped} skipped · {remaining} remaining'
        f'</div>',
        unsafe_allow_html=True,
    )


def _render_product_card(product: dict):
    """Render the primary product as a design-system card."""
    st.html(product_card(product))

    cpp = product_matcher.cost_per_oz(product)
    if cpp:
        st.caption(f"${cpp:.3f} / oz")


def _render_alt_card(alt: dict, alt_index, review_index: int):
    """Render an alternative product with a Swap button."""
    brand = alt.get("brand", "")
    name = alt.get("product_name", "")
    size = alt.get("size", "")
    price = alt.get("price")
    promo = alt.get("promo_price")
    on_sale = alt.get("on_sale", False)
    oos_flag = alt.get("_oos_preferred", False)

    price_str = f"~~${price:.2f}~~ **${promo:.2f}** 🏷" if (on_sale and promo) else (f"${price:.2f}" if price else "")
    oos_str = " 🚫 *Out of Stock*" if oos_flag else ""
    cpp = product_matcher.cost_per_oz(alt)
    cpp_str = f" · ${cpp:.3f}/oz" if cpp else ""

    col_info, col_btn = st.columns([5, 1])
    with col_info:
        st.write(f"**{brand} {name}** · {size} · {price_str}{oos_str}")
        if cpp_str:
            st.caption(cpp_str)
    with col_btn:
        if st.button("Swap", key=f"swap_{review_index}_{alt_index}"):
            st.session_state[f"current_primary_{review_index}"] = alt
            st.session_state[f"current_mt_{review_index}"] = "Best Match"
            st.session_state[f"save_pref_{review_index}"] = False
            st.rerun()


def _kroger_search_for_review(query: str) -> list:
    """
    Run a live Kroger product search from the review screen.
    Returns a list of normalised product dicts. Empty on any error.
    """
    try:
        token = kroger_auth.get_valid_token()
        location_id = os.getenv("KROGER_LOCATION_ID", "").strip()
        return product_matcher.search_kroger_for_review(query, location_id, token)
    except Exception as e:
        st.warning(f"Search failed: {e}")
        return []


def _confirm_current(item: dict, idx: int, current: dict, quantity: float):
    """Confirm current product, optionally save preference, advance queue."""
    confirmed_item = {**item, "primary": current, "quantity": quantity}
    st.session_state.confirmed_items.append(confirmed_item)

    if st.session_state.get(f"save_pref_{idx}", False):
        preference_store.save_preference(
            item["item_key"],
            {
                "kroger_upc":   current.get("upc", ""),
                "product_name": current.get("product_name", ""),
                "brand":        current.get("brand", ""),
                "size":         current.get("size", ""),
                "price":        current.get("promo_price") or current.get("price"),
                "category":     current.get("category", ""),
            },
            source="review",
        )
        st.session_state.new_prefs_count += 1

    st.session_state.review_history.append({
        "idx":    idx,
        "action": "confirmed",
        "item":   confirmed_item,
    })

    st.session_state.review_index = idx + 1
    st.rerun()


def _skip_current(item: dict, idx: int):
    """Skip current item, advance queue."""
    skipped_item = {**item}
    if item.get("match_type") == "Not Found":
        st.session_state.not_found_items.append(skipped_item)
    else:
        st.session_state.skipped_items.append(skipped_item)

    st.session_state.review_history.append({
        "idx":    idx,
        "action": "skipped",
        "item":   skipped_item,
    })

    st.session_state.review_index = idx + 1
    st.rerun()


def _go_back_one():
    """Undo the last review action and go back one item."""
    history = st.session_state.review_history
    if not history:
        return

    last = history.pop()
    st.session_state.review_history = history

    if last["action"] == "confirmed":
        confirmed = st.session_state.confirmed_items
        if confirmed and confirmed[-1]["item_key"] == last["item"]["item_key"]:
            confirmed.pop()
            st.session_state.confirmed_items = confirmed
    elif last["action"] == "skipped":
        skipped = st.session_state.skipped_items
        nf = st.session_state.not_found_items
        if skipped and skipped[-1]["item_key"] == last["item"]["item_key"]:
            skipped.pop()
            st.session_state.skipped_items = skipped
        elif nf and nf[-1]["item_key"] == last["item"]["item_key"]:
            nf.pop()
            st.session_state.not_found_items = nf

    st.session_state.review_index = last["idx"]
    st.rerun()


def _post_cart_and_go_summary():
    """Post confirmed items to Kroger cart, then go to summary screen."""
    manually_confirmed = st.session_state.confirmed_items
    auto_confirmed = st.session_state.auto_confirmed_items

    all_confirmed = manually_confirmed + auto_confirmed

    with st.spinner(f"Adding {len(all_confirmed)} items to your Kroger cart..."):
        result = cart_manager.post_to_cart(all_confirmed)
        st.session_state.cart_result = result

    cart_manager.log_completed_session(
        result,
        new_preferences_count=st.session_state.new_prefs_count,
        skipped_items=st.session_state.skipped_items,
        not_found_items=st.session_state.not_found_items,
    )

    go("summary")
