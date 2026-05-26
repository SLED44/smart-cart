"""Preview screen — review normalised list before matching.

Items can originate from three sources:
    1. parsed_result["items"] — output of list_parser (typed-in list) OR
       the meal-plan aggregator (via mealplan.grocery.aggregate_grocery_list)
    2. staples (when staples_added is True)
    3. session_state.manual_items — ad-hoc adds from this screen's
       '+ Add item' row or '+ Paste more items' expander

All three are merged into combined_items before handoff to item_filter.
"""

import streamlit as st

import list_parser
import preference_store

from screens._shared import go

KEY_MANUAL_ITEMS = "manual_items"

_CATEGORIES = (
    "Produce", "Meat", "Dairy", "Frozen", "Bakery",
    "Pantry", "Beverages", "Household", "Personal Care", "Other",
)


def render():
    st.title("Review Your List")
    st.caption("Check that everything was parsed correctly before we search Kroger.")
    st.divider()

    parsed = st.session_state.parsed_result
    if not parsed:
        go("home")
        return

    items = list(parsed["items"])  # copy

    # --- Add staples if requested -----------------------------------------
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

    # --- Merge in any manual additions from this screen ------------------
    manual = st.session_state.get(KEY_MANUAL_ITEMS, []) or []
    if manual:
        existing_keys = {i["item_key"] for i in items}
        for m in manual:
            if m["item_key"] in existing_keys:
                continue  # duplicate guard — don't double-add
            items.append(m)

    st.session_state.combined_items = items

    if parsed.get("parse_warnings"):
        with st.expander(f"⚠ {len(parsed['parse_warnings'])} parsing note(s)", expanded=False):
            for w in parsed["parse_warnings"]:
                st.caption(f"• {w}")

    # If the list came from a meal-plan hand-off, surface that.
    if any((i.get("source") == "meal_plan") for i in items):
        st.info(
            "📋 Items below were aggregated from your meal plan. Edit quantities, "
            "deselect what's already in your pantry, or add ad-hoc items at the bottom."
        )

    st.write(f"**{len(items)} items** ready to match:")

    grouped = list_parser.group_items_by_category(items)
    for category, cat_items in grouped.items():
        st.write(f"**{category}**")
        for item in cat_items:
            qty = list_parser.format_quantity(item["quantity"], item["unit"])
            notes_raw = item.get("notes") or ""
            notes = f" *({notes_raw})*" if notes_raw and notes_raw != "staple" else ""
            pref = " ★" if item.get("has_preference") else ""
            staple_tag = " 📌" if notes_raw == "staple" else ""
            mp_tag = " 🍳" if item.get("source") == "meal_plan" else ""
            manual_tag = " ✏" if item.get("source") == "manual" else ""
            st.write(
                f"  • {qty} {item['item_name']}{notes}{pref}"
                f"{staple_tag}{mp_tag}{manual_tag}"
            )

    st.caption("★ saved preference   📌 staple   🍳 from meal plan   ✏ added by hand")
    st.divider()

    # --- Ad-hoc add affordances (PRD §13.2 + memory: paste-a-list) -------
    _render_add_item_row()
    _render_paste_list_expander()

    st.divider()
    col1, col2 = st.columns(2)
    with col1:
        if st.button("← Back", use_container_width=True):
            go("home")
    with col2:
        if st.button("Looks Good — Find Products →", type="primary",
                     use_container_width=True):
            go("item_filter")


# ---------------------------------------------------------------------------
# Single-item add (PRD §13.2)
# ---------------------------------------------------------------------------

def _render_add_item_row():
    with st.expander("➕ Add a single item"):
        st.caption(
            "No Claude parse — pick the category yourself. Useful for "
            "'oh, also wine' moments."
        )
        with st.form("add_item_form", clear_on_submit=True):
            col_name, col_qty, col_unit, col_cat = st.columns([3, 1, 1, 2])
            with col_name:
                name = st.text_input("Item", placeholder="e.g. red wine")
            with col_qty:
                qty = st.number_input("Qty", min_value=0.0, value=1.0, step=1.0,
                                      format="%.2f")
            with col_unit:
                unit = st.text_input("Unit", placeholder="bottle", value="")
            with col_cat:
                cat = st.selectbox("Category", _CATEGORIES,
                                   index=_CATEGORIES.index("Pantry"))
            submitted = st.form_submit_button("Add", type="primary",
                                              use_container_width=True)
            if submitted and name.strip():
                _append_manual_items([_make_manual_item(name, qty, unit, cat)])
                st.success(f"Added {name}.")
                st.rerun()


# ---------------------------------------------------------------------------
# Paste-a-list (memory note: diverges from PRD §13.2 single-item-only design)
# ---------------------------------------------------------------------------

def _render_paste_list_expander():
    with st.expander("📋 Paste more items (Claude-parsed)"):
        st.caption(
            "Drop in a list of additional items — bullets, prose, mixed. "
            "Claude parses and categorises like the home-screen entry."
        )
        text = st.text_area(
            "Additional items",
            value=st.session_state.get("preview_paste_raw", ""),
            placeholder="• 1 lb butter\n• 2 lemons\n• a baguette",
            height=150,
            key="preview_paste_input",
            label_visibility="collapsed",
        )
        st.session_state.preview_paste_raw = text

        if st.button("Parse + add", key="preview_paste_btn"):
            if not text.strip():
                st.error("Paste some text first.")
                return
            with st.spinner("Parsing with Claude…"):
                try:
                    parsed = list_parser.parse_grocery_list(text)
                except (ValueError, RuntimeError) as e:
                    st.error(f"Parse failed: {e}")
                    return
            new_items = parsed.get("items") or []
            for it in new_items:
                it["source"] = "manual"
            _append_manual_items(new_items)
            st.session_state.preview_paste_raw = ""
            st.success(f"Added {len(new_items)} item(s).")
            st.rerun()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_manual_item(name: str, qty: float, unit: str, category: str) -> dict:
    key = preference_store.normalise_item_key(name)
    try:
        has_pref = preference_store.get_preference(key) is not None
    except Exception:
        has_pref = False
    return {
        "item_name":      name.strip(),
        "item_key":       key,
        "quantity":       float(qty) if qty > 0 else 1.0,
        "unit":           (unit or "").strip(),
        "category":       category,
        "notes":          "",
        "has_preference": has_pref,
        "source":         "manual",
    }


def _append_manual_items(new_items: list[dict]):
    existing = st.session_state.get(KEY_MANUAL_ITEMS, []) or []
    existing_keys = {i["item_key"] for i in existing}
    for it in new_items:
        if not it.get("item_key"):
            it["item_key"] = preference_store.normalise_item_key(it.get("item_name", ""))
        if it["item_key"] in existing_keys:
            continue
        existing.append(it)
        existing_keys.add(it["item_key"])
    st.session_state[KEY_MANUAL_ITEMS] = existing
