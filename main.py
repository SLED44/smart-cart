"""
main.py
-------
SmartCart — Streamlit UI

All screens, navigation, and session state management.
No business logic lives here — this file only calls the backend modules.

Run with:
    streamlit run main.py --server.address 0.0.0.0 --server.port 8501

Screens:
    login           Password gate
    home            Paste list, add staples, start session
    preview         Review normalised list before matching
    sale_scan       Pre-review sale alternatives (skipped if none)
    review          Item-by-item product review queue
    summary         Session complete — cart posted, open City Market
    preferences     Manage saved product preferences
    staples         Manage weekly staple items
    store_setup     First-run store location finder
"""

import json
import os
import time

import streamlit as st
from dotenv import load_dotenv

# Local dev: .env file → environment.
# Streamlit Cloud: st.secrets is the source of truth; mirror it into os.environ
# so every backend module can keep reading os.getenv(...) unchanged.
load_dotenv(override=True)
try:
    for _k, _v in st.secrets.items():
        if isinstance(_v, str) and _k not in os.environ:
            os.environ[_k] = _v
except Exception:
    # No secrets.toml locally — that's expected; .env covers local dev.
    pass

# ---------------------------------------------------------------------------
# Page config — must be the very first Streamlit call
# ---------------------------------------------------------------------------

st.set_page_config(
    page_title="SmartCart",
    page_icon="🛒",
    layout="wide",
    initial_sidebar_state="collapsed",
)

# ---------------------------------------------------------------------------
# Lazy imports — keeps startup fast; modules imported only when needed
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# Session state initialisation
# ---------------------------------------------------------------------------

def _init_state():
    defaults = {
        "authenticated":    False,
        "screen":           "login",
        "raw_list":         "",
        "parsed_result":    None,   # Output of list_parser.parse_grocery_list()
        "staples_added":    False,
        "combined_items":   [],     # Parsed items + any added staples
        "scan_result":      None,   # Output of sale_scanner.scan_for_sale_alternatives()
        "matched_items":    [],     # Output of product_matcher.match_items()
        "review_index":     0,      # Current position in review queue
        "confirmed_items":  [],     # Items user confirmed for cart
        "skipped_items":    [],     # Items user skipped
        "not_found_items":  [],     # Items with no Kroger match
        "new_prefs_count":  0,      # New preferences saved this session
        "cart_result":      None,   # Output of cart_manager.post_to_cart()
        "review_history":       [],     # Stack for Back button support
        "sale_switches":        [],     # Item keys switched on Sale Scan screen
        "auto_confirmed_items": [],     # Items auto-confirmed (preferred + in stock + no sale alt)
        "item_filter_selections": {},   # Checkbox state for item filter screen
        "oauth_state":          None,   # CSRF token for Kroger OAuth round-trip
        "oauth_code_verifier":  None,   # PKCE verifier kept until callback
        "kroger_auth_msg":      None,   # Banner to show after auth completes
    }
    for key, value in defaults.items():
        if key not in st.session_state:
            st.session_state[key] = value

_init_state()

# ---------------------------------------------------------------------------
# Module imports — done eagerly. Cold start is fast enough on Streamlit Cloud
# and the previous lazy-import indirection was a no-op anyway (the same
# modules were also imported eagerly at the bottom of the file).
# ---------------------------------------------------------------------------

import requests  # noqa: E402

import cart_manager  # noqa: E402
import kroger_auth  # noqa: E402
import list_parser  # noqa: E402
import preference_store  # noqa: E402
import product_matcher  # noqa: E402
import sale_scanner  # noqa: E402
from supabase_kv import kv_delete, kv_get, kv_put  # noqa: E402

KEY_LOCATION_ID = "kroger_location_id"


def _pending_oauth_key(state: str) -> str:
    """Supabase key for an in-flight OAuth round-trip, keyed by CSRF state."""
    return f"oauth_pending:{state}"


def _get_location_id() -> str:
    """Resolve the store location ID. KV wins over .env so the value
    persists across container restarts on Streamlit Cloud."""
    return (kv_get(KEY_LOCATION_ID, "") or os.getenv("KROGER_LOCATION_ID", "")).strip()


def _save_location_id(location_id: str) -> None:
    """Persist location_id to Supabase and reflect it in the live process env
    so downstream modules (product_matcher, sale_scanner) read it via os.getenv."""
    kv_put(KEY_LOCATION_ID, location_id)
    os.environ["KROGER_LOCATION_ID"] = location_id


# ---------------------------------------------------------------------------
# Navigation helper
# ---------------------------------------------------------------------------

def _go(screen: str):
    st.session_state.screen = screen
    st.rerun()

# ---------------------------------------------------------------------------
# Minimal CSS — utilitarian only
# ---------------------------------------------------------------------------

st.markdown("""
<style>
    .match-badge {
        display: inline-block;
        padding: 2px 10px;
        border-radius: 12px;
        font-size: 0.78em;
        font-weight: 600;
        margin-bottom: 6px;
    }
    .badge-preferred    { background:#d4edda; color:#155724; }
    .badge-oos          { background:#f8d7da; color:#721c24; }
    .badge-best         { background:#d1ecf1; color:#0c5460; }
    .badge-needs-pick   { background:#fff3cd; color:#856404; }
    .badge-not-found    { background:#e2e3e5; color:#383d41; }
    .badge-sale         { background:#fff3cd; color:#856404; }
    .sale-banner {
        background:#fff3cd; border:1px solid #ffc107;
        border-radius:6px; padding:8px 12px; margin:8px 0;
        font-size:0.9em;
    }
    .oos-banner {
        background:#f8d7da; border:1px solid #f5c6cb;
        border-radius:6px; padding:8px 12px; margin:8px 0;
        font-size:0.9em;
    }
    .product-card {
        border:1px solid #dee2e6; border-radius:8px;
        padding:16px; margin-bottom:8px;
        background:#ffffff;
    }
    .footer-bar {
        position:fixed; bottom:0; left:0; right:0;
        background:#f8f9fa; border-top:1px solid #dee2e6;
        padding:8px 24px; font-size:0.85em; color:#6c757d;
    }
</style>
""", unsafe_allow_html=True)

# ---------------------------------------------------------------------------
# Badge helper
# ---------------------------------------------------------------------------

_BADGE_CLASSES = {
    "Preferred Match":  "badge-preferred",
    "Preferred OOS":    "badge-oos",
    "Best Match":       "badge-best",
    "Needs Your Pick":  "badge-needs-pick",
    "Not Found":        "badge-not-found",
    "On Sale Alt":      "badge-sale",
}

def _badge(match_type: str) -> str:
    cls = _BADGE_CLASSES.get(match_type, "badge-best")
    return f'<span class="match-badge {cls}">{match_type}</span>'

# ---------------------------------------------------------------------------
# Screen: Login
# ---------------------------------------------------------------------------

def screen_login():
    st.title("🛒 SmartCart")
    st.caption("AI-Powered Grocery → Kroger Cart")
    st.divider()

    col1, col2, col3 = st.columns([1, 1.2, 1])
    with col2:
        st.subheader("Household Login")
        with st.form("login_form", clear_on_submit=False):
            password = st.text_input("Password", type="password", key="login_input")
            submitted = st.form_submit_button("Sign In", use_container_width=True, type="primary")
            if submitted:
                app_password = os.getenv("APP_PASSWORD", "")
                if not app_password:
                    st.error("APP_PASSWORD is not set in your .env file / Streamlit secrets.")
                elif password == app_password:
                    st.session_state.authenticated = True
                    _go("home")
                else:
                    st.error("Incorrect password.")

# ---------------------------------------------------------------------------
# Screen: Home
# ---------------------------------------------------------------------------

def screen_home():
    st.title("🛒 SmartCart")

    # Surface any post-OAuth banner once
    if st.session_state.kroger_auth_msg:
        st.success(st.session_state.kroger_auth_msg)
        st.session_state.kroger_auth_msg = None

    # Nav bar
    col_nav1, col_nav2, col_nav3 = st.columns([1, 1, 6])
    with col_nav1:
        if st.button("⚙ Preferences"):
            _go("preferences")
    with col_nav2:
        if st.button("📋 Staples"):
            _go("staples")

    st.divider()

    # Kroger authorization check
    kroger_status = kroger_auth.token_status()
    if kroger_status["status"] == "not_authorized":
        st.warning("⚠ Kroger account not connected. Connect to enable matching and cart posting.")
        if st.button("🔗 Connect Kroger", type="primary"):
            _go("connect_kroger")
        return

    # Check for store setup
    location_id = _get_location_id()
    if not location_id or location_id == "your_store_location_id_here":
        st.warning("⚠ Store location not set. Complete store setup before starting a session.")
        if st.button("🔍 Find My Store", type="primary"):
            _go("store_setup")
        return

    # Stats
    summary = preference_store.data_summary()
    col_a, col_b, col_c = st.columns(3)
    col_a.metric("Saved Preferences", summary["preference_count"])
    col_b.metric("Staples on File", summary["staple_count"])
    col_c.metric("Sessions Run", summary["session_count"])

    st.divider()
    st.subheader("Paste Your Grocery List")
    st.caption("Any format works — bullet list, numbered, prose, categorised sections, or mixed.")

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
                grouped_staples.setdefault(s.get("category","Other"), []).append(s)

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
    if st.button("Parse List →", type="primary", use_container_width=True):
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
        items = list(parsed["items"])

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
        _go("item_filter")

# ---------------------------------------------------------------------------
# Screen: Normalisation Preview
# ---------------------------------------------------------------------------

def screen_preview():
    st.title("Review Your List")
    st.caption("Check that everything was parsed correctly before we search Kroger.")
    st.divider()

    parsed = st.session_state.parsed_result
    if not parsed:
        _go("home")
        return

    items = list(parsed["items"])  # copy

    # Add staples if requested
    if st.session_state.staples_added:
        staples = preference_store.get_all_staples()
        selections = st.session_state.get("staple_selections", {})
        existing_keys = {i["item_key"] for i in items}
        for staple in staples:
            key = staple["item_key"]
            # Only add if selected (default True if not in selections dict)
            if not selections.get(key, True):
                continue
            if key not in existing_keys:
                # Use session quantity if set
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

    # Display grouped by category
    grouped = list_parser.group_items_by_category(items)
    for category, cat_items in grouped.items():
        st.write(f"**{category}**")
        for item in cat_items:
            qty = list_parser.format_quantity(item["quantity"], item["unit"])
            notes = f" *({item['notes']})*" if item["notes"] and item["notes"] != "staple" else ""
            pref  = " ★" if item["has_preference"] else ""
            staple_tag = " 📌" if item.get("notes") == "staple" else ""
            st.write(f"  • {qty} {item['item_name']}{notes}{pref}{staple_tag}")

    st.caption("★ = saved preference exists   📌 = staple")
    st.divider()

    col1, col2 = st.columns(2)
    with col1:
        if st.button("← Back", use_container_width=True):
            _go("home")
    with col2:
        if st.button("Looks Good — Find Products →", type="primary", use_container_width=True):
            _go("item_filter")

# ---------------------------------------------------------------------------
# Screen: Item Filter (pre-match checklist)
# ---------------------------------------------------------------------------

def screen_item_filter():
    """
    Shows every parsed item as a checklist before matching begins.
    User unchecks items they already have or don't need this run.
    Only checked items proceed to the matching pipeline.
    """
    items = st.session_state.combined_items
    if not items:
        _go("home")
        return

    st.title("What do you need this run?")
    st.caption("Uncheck anything you already have at home. Only checked items will be matched and added to your cart.")
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

    # Render checkboxes grouped by category
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
        st.write("")  # spacing between categories

    st.session_state.item_filter_selections = selections

    # Count checked items
    checked_count = sum(1 for item in items if selections.get(item["item_key"], True))

    st.divider()
    col_back, col_continue = st.columns([1, 2])
    with col_back:
        if st.button("← Back", use_container_width=True):
            _go("home")
    with col_continue:
        btn_label = f"Continue with {checked_count} item{'s' if checked_count != 1 else ''} →"
        if st.button(btn_label, type="primary", use_container_width=True, disabled=checked_count == 0):
            # Filter combined_items down to only checked items
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

    # Sale scan (runs in background — best effort)
    with st.spinner("Scanning for sale alternatives..."):
        try:
            scan_result = sale_scanner.scan_for_sale_alternatives(items)
            st.session_state.scan_result = scan_result
        except kroger_auth.NeedsAuthorization:
            _go("connect_kroger")
            return
        except Exception as e:
            st.session_state.scan_result = {"sale_alerts": [], "scanned_count": 0, "alert_count": 0}
            st.warning(f"Sale scan skipped: {e}")

    # Product matching
    with st.spinner(f"Matching {len(items)} items against Kroger catalog..."):
        try:
            matched = product_matcher.match_items(items)
            st.session_state.matched_items = matched
        except kroger_auth.NeedsAuthorization:
            _go("connect_kroger")
            return
        except RuntimeError as e:
            st.error(f"Product matching failed: {e}")
            return

    # Reset review state
    st.session_state.review_index        = 0
    st.session_state.confirmed_items     = []
    st.session_state.skipped_items       = []
    st.session_state.not_found_items     = []
    st.session_state.new_prefs_count     = 0
    st.session_state.review_history      = []
    st.session_state.sale_switches       = []
    st.session_state.auto_confirmed_items = []

    # Go to sale scan screen if alerts found, else straight to review
    if st.session_state.scan_result.get("alert_count", 0) > 0:
        _go("sale_scan")
    else:
        _split_auto_confirmed()
        _go("review")

# ---------------------------------------------------------------------------
# Screen: Sale Scan
# ---------------------------------------------------------------------------

def screen_sale_scan():
    scan = st.session_state.scan_result
    alerts = scan.get("sale_alerts", [])

    st.title("🏷 Sale Scan")
    st.write(
        f"We found **{len(alerts)} item(s)** on sale that are comparable "
        f"to your preferred products."
    )
    st.divider()

    switches = list(st.session_state.sale_switches)

    for alert in alerts:
        item_name  = alert["item_name"]
        preferred  = alert["preferred_product"]
        sale_prod  = alert["sale_product"]
        savings    = alert["savings_amount"]
        savings_pct= alert["savings_pct"]
        item_key   = alert["item_key"]

        st.subheader(item_name)
        col_pref, col_sale = st.columns(2)

        with col_pref:
            st.markdown("**Your Usual**")
            pref_price = preferred.get("price")
            price_str  = f"${pref_price:.2f}" if pref_price else "Price unknown"
            st.write(f"**{preferred.get('brand','')}** {preferred.get('product_name','')}")
            st.write(f"{preferred.get('size','')} · {price_str}")

        with col_sale:
            st.markdown("**🏷 On Sale This Week**")
            sale_price = sale_prod.get("promo_price") or sale_prod.get("price")
            sale_str   = f"${sale_price:.2f}" if sale_price else "Price unknown"
            st.write(f"**{sale_prod.get('brand','')}** {sale_prod.get('product_name','')}")
            st.write(f"{sale_prod.get('size','')} · {sale_str}")
            st.write(f"💰 Save **${savings:.2f}** ({savings_pct:.1f}% less)")

        is_switched = item_key in switches
        col_a, col_b = st.columns(2)
        with col_a:
            if st.button(
                f"{'✓ Switched for this run' if is_switched else 'Switch to sale item (this run)'}",
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
            if st.button(f"Always prefer sale item", key=f"perm_{item_key}"):
                # Save as new permanent preference
                preference_store.save_preference(
                    item_key,
                    {
                        "kroger_upc":   sale_prod.get("upc",""),
                        "product_name": sale_prod.get("product_name",""),
                        "brand":        sale_prod.get("brand",""),
                        "size":         sale_prod.get("size",""),
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
            # Apply no switches
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
    _split_auto_confirmed()
    _go("review")


def _split_auto_confirmed():
    """
    Before entering the review queue, pull out items that can be
    auto-confirmed — no human decision needed.

    Auto-confirm criteria (all three must be true):
        1. match_type == "Preferred Match"  (has a saved preference, in stock)
        2. item_key is NOT in the sale scan alert set
           (a sale alternative exists → send to review so user can see it)

    Auto-confirmed items are stored in session state and added to the
    cart alongside manually confirmed items at the end of the session.
    The review queue (matched_items) is updated to contain only the
    items that genuinely need human attention.
    """
    matched = st.session_state.matched_items
    scan_result = st.session_state.scan_result or {}

    # Build set of item_keys that have a sale alert
    sale_alert_keys = {
        alert["item_key"]
        for alert in scan_result.get("sale_alerts", [])
    }

    auto_confirmed = []
    needs_review   = []

    for item in matched:
        if (
            item.get("match_type") == "Preferred Match"
            and item.get("item_key") not in sale_alert_keys
        ):
            auto_confirmed.append(item)
        else:
            needs_review.append(item)

    st.session_state.auto_confirmed_items = auto_confirmed
    st.session_state.matched_items        = needs_review

# ---------------------------------------------------------------------------
# Screen: Item-by-Item Review
# ---------------------------------------------------------------------------

def screen_review():
    matched = st.session_state.matched_items
    idx     = st.session_state.review_index

    if not matched or idx >= len(matched):
        # All items reviewed — go to cart post
        _post_cart_and_go_summary()
        return

    item       = matched[idx]
    total      = len(matched)
    confirmed  = len(st.session_state.confirmed_items)
    skipped    = len(st.session_state.skipped_items)
    remaining  = total - idx

    # Progress
    st.progress(idx / total)
    st.caption(f"Item {idx + 1} of {total}")

    item_name  = item["item_name"]
    match_type = item.get("match_type", "Best Match")
    primary    = item.get("primary")
    alts       = item.get("alternatives", [])
    reason     = item.get("match_reason", "")

    # Current selection (can be swapped to an alt)
    if f"current_primary_{idx}" not in st.session_state:
        st.session_state[f"current_primary_{idx}"] = primary
    current    = st.session_state[f"current_primary_{idx}"]
    current_mt = st.session_state.get(f"current_mt_{idx}", match_type)

    st.subheader(item_name)
    st.markdown(_badge(current_mt), unsafe_allow_html=True)

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

    # Sale callout during review (PRD RV-13)
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

    # Primary product card
    if current:
        _render_product_card(current, is_primary=True, item=item)
    else:
        st.warning("No matching product found for this item.")

    # Quantity control
    # For weight-based items, pre-fill using suggested_quantity if possible
    qty_key        = f"qty_{idx}"
    qty_suggested  = f"qty_suggested_{idx}"   # flag: have we set the smart default yet?

    if qty_key not in st.session_state:
        base_qty = float(item.get("quantity", 1))
        if current and item_notes:
            smart = product_matcher.suggested_quantity(item_notes, current)
            if smart is not None:
                base_qty = float(smart)
                st.session_state[qty_suggested] = True
        st.session_state[qty_key] = max(1.0, base_qty)

    # If user swaps to a different product, recalculate suggestion
    if current and item_notes and not st.session_state.get(f"qty_locked_{idx}"):
        smart = product_matcher.suggested_quantity(item_notes, current)
        if smart is not None and not st.session_state.get(f"qty_user_edited_{idx}"):
            st.session_state[qty_key] = max(1.0, float(smart))

    col_qty, col_hint = st.columns([1, 2])
    with col_qty:
        prev_qty = st.session_state[qty_key]
        new_qty  = st.number_input(
            "Quantity",
            min_value=1.0,
            step=1.0,
            value=st.session_state[qty_key],
            key=f"qty_input_{idx}",
        )
        # Track if user manually changed the quantity
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
            _render_alt_card(alt, ai, idx, item)

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
                st.session_state[search_key]   = search_term
                st.session_state[results_key]  = results
                st.rerun()

        search_results = st.session_state.get(results_key, [])
        if search_results:
            st.write(f"**{len(search_results)} result(s):**")
            for si, result in enumerate(search_results):
                _render_alt_card(result, f"s{si}", idx, item)
        elif st.session_state.get(search_key):
            st.caption("No results found. Try different keywords.")

    st.divider()

    # Action buttons
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

    # Footer bar
    st.markdown(
        f'<div class="footer-bar">'
        f'{confirmed} confirmed · {skipped} skipped · {remaining} remaining'
        f'</div>',
        unsafe_allow_html=True,
    )


def _render_product_card(product: dict, is_primary: bool = False, item: dict = None):
    """Render a product detail card."""
    brand    = product.get("brand", "")
    name     = product.get("product_name", "")
    size     = product.get("size", "")
    price    = product.get("price")
    promo    = product.get("promo_price")
    on_sale  = product.get("on_sale", False)
    in_stock = product.get("in_stock", True)
    oos_flag = product.get("_oos_preferred", False)

    col_img, col_info = st.columns([1, 4])
    with col_img:
        image_url = product.get("image_url")
        if image_url:
            st.image(image_url, width=80)
        else:
            st.write("📦")

    with col_info:
        st.write(f"**{brand} {name}**")
        st.write(f"{size}")

        if oos_flag:
            st.markdown("🚫 *Out of Stock*", unsafe_allow_html=False)
        elif not in_stock:
            st.markdown("🚫 *Out of Stock*", unsafe_allow_html=False)

        if on_sale and promo:
            st.write(f"~~${price:.2f}~~ **${promo:.2f}** 🏷 On Sale")
        elif price:
            st.write(f"${price:.2f}")
        else:
            st.write("Price unavailable")

        # Cost per oz
        cpp = product_matcher.cost_per_oz(product)
        if cpp:
            st.caption(f"${cpp:.3f} / oz")


def _render_alt_card(alt: dict, alt_index: int, review_index: int, item: dict):
    """Render an alternative product with a Swap button."""
    brand    = alt.get("brand", "")
    name     = alt.get("product_name", "")
    size     = alt.get("size", "")
    price    = alt.get("price")
    promo    = alt.get("promo_price")
    on_sale  = alt.get("on_sale", False)
    oos_flag = alt.get("_oos_preferred", False)

    price_str = f"~~${price:.2f}~~ **${promo:.2f}** 🏷" if (on_sale and promo) else (f"${price:.2f}" if price else "")
    oos_str   = " 🚫 *Out of Stock*" if oos_flag else ""
    cpp       = product_matcher.cost_per_oz(alt)
    cpp_str   = f" · ${cpp:.3f}/oz" if cpp else ""

    col_info, col_btn = st.columns([5, 1])
    with col_info:
        st.write(f"**{brand} {name}** · {size} · {price_str}{oos_str}")
        if cpp_str:
            st.caption(cpp_str)
    with col_btn:
        if st.button("Swap", key=f"swap_{review_index}_{alt_index}"):
            st.session_state[f"current_primary_{review_index}"] = alt
            st.session_state[f"current_mt_{review_index}"]      = "Best Match"
            st.session_state[f"save_pref_{review_index}"]       = False
            st.rerun()


def _kroger_search_for_review(query: str) -> list:
    """
    Run a live Kroger product search from the review screen.
    Returns a list of normalised product dicts (same format as product_matcher).
    Returns empty list on any error so the UI degrades gracefully.
    """
    try:
        token       = kroger_auth.get_valid_token()
        location_id = os.getenv("KROGER_LOCATION_ID", "").strip()
        results     = product_matcher.search_kroger_for_review(query, location_id, token)
        return results
    except Exception as e:
        st.warning(f"Search failed: {e}")
        return []


def _confirm_current(item: dict, idx: int, current: dict, quantity: float):
    """Confirm current product, optionally save preference, advance queue."""
    confirmed_item = {**item, "primary": current, "quantity": quantity}
    st.session_state.confirmed_items.append(confirmed_item)

    # Save preference if checkbox was checked
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

    # Save to history for Back button
    st.session_state.review_history.append({
        "idx":       idx,
        "action":    "confirmed",
        "item":      confirmed_item,
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
        # Remove from confirmed
        confirmed = st.session_state.confirmed_items
        if confirmed and confirmed[-1]["item_key"] == last["item"]["item_key"]:
            confirmed.pop()
            st.session_state.confirmed_items = confirmed
    elif last["action"] == "skipped":
        skipped = st.session_state.skipped_items
        nf      = st.session_state.not_found_items
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
    auto_confirmed     = st.session_state.auto_confirmed_items

    # Combine both lists for the cart post
    all_confirmed = manually_confirmed + auto_confirmed

    with st.spinner(f"Adding {len(all_confirmed)} items to your Kroger cart..."):
        result = cart_manager.post_to_cart(all_confirmed)
        st.session_state.cart_result = result

    # Log session
    cart_manager.log_completed_session(
        result,
        new_preferences_count=st.session_state.new_prefs_count,
        skipped_items=st.session_state.skipped_items,
        not_found_items=st.session_state.not_found_items,
    )

    _go("summary")

# ---------------------------------------------------------------------------
# Screen: Session Summary
# ---------------------------------------------------------------------------

def screen_summary():
    result = st.session_state.cart_result
    if not result:
        _go("home")
        return

    st.title("✅ Shopping Run Complete")
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

    # Auto-confirmed items (collapsible)
    if auto_confirmed:
        st.divider()
        with st.expander(f"⚡ Auto-confirmed ({len(auto_confirmed)} item{'s' if len(auto_confirmed) != 1 else ''}) — added without review"):
            st.caption("These items had a saved preference, were in stock, and had no sale alternative — so they were added automatically.")
            for item in auto_confirmed:
                primary = item.get("primary", {})
                brand   = primary.get("brand", "")
                name    = primary.get("product_name", "")
                size    = primary.get("size", "")
                price   = primary.get("promo_price") or primary.get("price")
                price_str = f" · ${price:.2f}" if price else ""
                qty     = max(1, round(item.get("quantity", 1)))
                qty_str = f" ×{qty}" if qty > 1 else ""
                st.write(f"• **{item['item_name']}** → {brand} {name} {size}{price_str}{qty_str}")

    # Failed items
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
                # Merge retry result into current result
                result["succeeded"].extend(retry_result["succeeded"])
                result["failed"]    = retry_result["failed"]
                result["success_count"] += retry_result["success_count"]
                result["failure_count"]  = retry_result["failure_count"]
                st.session_state.cart_result = result
                st.rerun()

    # Skipped items
    if st.session_state.skipped_items:
        with st.expander(f"Skipped items ({len(st.session_state.skipped_items)})"):
            for item in st.session_state.skipped_items:
                st.write(f"• {item['item_name']}")

    # Not found items
    if st.session_state.not_found_items:
        with st.expander(f"Not found on Kroger ({len(st.session_state.not_found_items)})"):
            for item in st.session_state.not_found_items:
                st.write(f"• {item['item_name']} — add manually in City Market app")

    st.divider()
    if st.button("← Start New Shopping Run", use_container_width=True):
        # Reset session state for a fresh run
        for key in ["raw_list", "parsed_result", "staples_added", "combined_items",
                    "scan_result", "matched_items", "review_index", "confirmed_items",
                    "skipped_items", "not_found_items", "new_prefs_count",
                    "cart_result", "review_history", "sale_switches",
                    "auto_confirmed_items", "item_filter_selections"]:
            if key in st.session_state:
                del st.session_state[key]
        _init_state()
        _go("home")

# ---------------------------------------------------------------------------
# Screen: Preferences
# ---------------------------------------------------------------------------

def screen_preferences():
    st.title("⚙ Preferences")
    st.caption("Your saved product preferences. These drive auto-matching on future runs.")

    col_nav, _ = st.columns([1, 5])
    with col_nav:
        if st.button("← Back to Home"):
            _go("home")

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

    # Search filter
    search = st.text_input("Search preferences", placeholder="Type to filter...")

    filtered = {
        k: v for k, v in prefs.items()
        if not search or search.lower() in k.lower() or search.lower() in v.get("product_name","").lower()
    }

    st.write(f"**{len(filtered)} preference(s)**")
    st.divider()

    for item_key, pref in filtered.items():
        col_info, col_del = st.columns([6, 1])
        with col_info:
            saved_at = pref.get("saved_at","")[:10] if pref.get("saved_at") else ""
            source   = pref.get("source","review")
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
            _go("staples")

# ---------------------------------------------------------------------------
# Screen: Staples
# ---------------------------------------------------------------------------

def screen_staples():
    st.title("📋 Staples")
    st.caption("Items you buy on most shopping runs. Triggered manually per session.")

    col_nav, _ = st.columns([1, 5])
    with col_nav:
        if st.button("← Back to Home"):
            _go("home")

    st.divider()
    st.subheader("Add a Staple")

    with st.form("add_staple_form", clear_on_submit=True):
        col_name, col_qty, col_cat = st.columns([3, 1, 2])
        with col_name:
            display_name = st.text_input("Item name", placeholder="e.g. Whole Milk")
        with col_qty:
            default_qty  = st.number_input("Qty", min_value=1, value=1, step=1)
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
                st.write(f"×{staple.get('default_quantity',1)}")
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
            _go("preferences")

# ---------------------------------------------------------------------------
# Screen: Store Setup
# ---------------------------------------------------------------------------

def screen_store_setup():
    st.title("🔍 Find Your Store")
    st.write(
        "SmartCart needs your City Market store's location ID to search "
        "the right product catalog and check local prices."
    )
    st.divider()

    search_term = st.text_input(
        "Enter your zip code or city",
        placeholder="e.g. 80487 or Steamboat Springs CO"
    )

    if st.button("Search", type="primary") and search_term.strip():
        with st.spinner("Searching Kroger locations..."):
            try:
                locations = _search_kroger_locations(search_term.strip())
                st.session_state["found_locations"] = locations
                st.session_state["location_search_done"] = True
            except RuntimeError as e:
                st.error(f"Location search failed: {e}")

    locations = st.session_state.get("found_locations", [])
    if st.session_state.get("location_search_done") and not locations:
        st.warning("No City Market locations found. Try a different zip code.")
    if locations:
        st.write(f"**{len(locations)} location(s) found:**")
        st.divider()
        for loc in locations:
            col_info, col_select = st.columns([5, 1])
            with col_info:
                name    = loc.get("name", "Unknown")
                address = loc.get("address", {})
                addr_str = (
                    address.get('addressLine1','') + ' ' +
                    address.get('city','') + ' ' +
                    address.get('state','') + ' ' +
                    address.get('zipCode','')
                ).strip()
                hours = loc.get("hours", "")
                st.write(f"**{name}**")
                st.write(addr_str)
                if hours:
                    st.caption(hours)
            with col_select:
                loc_id = loc.get("locationId","")
                if st.button("Select", key=f"sel_loc_{loc_id}"):
                    _save_location_id(loc_id)
                    st.success(f"Store set to {name}.")
                    st.info(f"Your location ID is: **{loc_id}** (saved to Supabase).")
                    st.session_state["selected_location_id"] = loc_id
            st.divider()

    selected = st.session_state.get("selected_location_id")
    if selected:
        if st.button("← Go to Home"):
            _go("home")

    elif st.button("← Back"):
        _go("home")


def _search_kroger_locations(query: str) -> list:
    """Search Kroger Locations API for stores near a zip or city."""
    token = kroger_auth.get_client_credentials_token()
    params = {
        "filter.zipCode.near": query,
        "filter.limit":        10,
    }
    headers = {
        "Authorization": f"Bearer {token}",
        "Accept":        "application/json",
    }
    response = requests.get(
        "https://api.kroger.com/v1/locations",
        params=params,
        headers=headers,
        timeout=15,
    )
    if not response.ok:
        raise RuntimeError(f"Location API error {response.status_code}: {response.text[:100]}")
    return response.json().get("data", [])


# ---------------------------------------------------------------------------
# OAuth callback handler
# ---------------------------------------------------------------------------

def _handle_oauth_callback():
    """
    If Kroger redirected back to us with ?code=...&state=..., finish the
    OAuth exchange now (before any screen renders), then clear the query
    params and continue. Safe no-op when there is no code in the URL.

    The state -> code_verifier mapping lives in Supabase because Streamlit
    session_state does not survive the external redirect (Kroger -> here is
    a fresh WebSocket connection, so a fresh session_state).
    """
    params = st.query_params
    code = params.get("code")
    state = params.get("state")
    if not code or not state:
        return

    pending = kv_get(_pending_oauth_key(state), None)

    # Always clear params so a reload doesn't replay the exchange.
    st.query_params.clear()

    if not pending or "code_verifier" not in pending:
        st.error(
            "Kroger redirected back, but no in-progress authorization was found. "
            "Try clicking Connect Kroger again."
        )
        return

    try:
        kroger_auth.exchange_code_for_tokens(code, pending["code_verifier"])
        kv_delete(_pending_oauth_key(state))
        st.session_state.kroger_auth_msg = "✓ Kroger connected. You're ready to shop."
        if st.session_state.authenticated:
            _go("home")
    except RuntimeError as e:
        st.error(f"Kroger authorization failed: {e}")


# ---------------------------------------------------------------------------
# Screen: Connect Kroger
# ---------------------------------------------------------------------------

def screen_connect_kroger():
    st.title("🔗 Connect Kroger")
    st.write(
        "SmartCart needs read access to Kroger products and write access to "
        "your cart. No payment, order history, or personal data is requested."
    )
    st.divider()

    status = kroger_auth.token_status()
    if status["status"] == "valid":
        st.success(status["message"])
        if st.button("← Back to Home"):
            _go("home")
        if st.button("Disconnect (re-auth required)"):
            kroger_auth.clear_stored_tokens()
            st.rerun()
        return

    # Generate a fresh authorization URL on every render of this screen and
    # persist the PKCE verifier to Supabase keyed by `state`. We need durable
    # storage here because the user will leave the Streamlit session entirely
    # when they navigate to Kroger; session_state would be gone on return.
    auth = kroger_auth.build_authorization_url()
    kv_put(_pending_oauth_key(auth["state"]), {"code_verifier": auth["code_verifier"]})

    st.info(
        "Click the button to authorize. You'll be redirected to Kroger and "
        "land back here automatically once you grant access."
    )
    st.link_button("Authorize with Kroger →", auth["url"], type="primary")

    if st.button("← Back"):
        _go("home")


# ---------------------------------------------------------------------------
# Router
# ---------------------------------------------------------------------------

def main():
    # Finish any in-flight Kroger OAuth round-trip before rendering anything.
    _handle_oauth_callback()

    if not st.session_state.authenticated:
        screen_login()
        return

    screen = st.session_state.screen

    if screen == "home":
        screen_home()
    elif screen == "preview":
        screen_preview()
    elif screen == "item_filter":
        screen_item_filter()
    elif screen == "sale_scan":
        screen_sale_scan()
    elif screen == "review":
        screen_review()
    elif screen == "summary":
        screen_summary()
    elif screen == "preferences":
        screen_preferences()
    elif screen == "staples":
        screen_staples()
    elif screen == "store_setup":
        screen_store_setup()
    elif screen == "connect_kroger":
        screen_connect_kroger()
    else:
        _go("home")


main()