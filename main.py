"""
main.py
-------
SmartCart — Streamlit router.

Boots Streamlit, bridges secrets into the process env, loads the design-
system CSS, initialises session state, handles the OAuth callback, then
dispatches to a screen module from the `screens` package.

Run with:
    streamlit run main.py --server.address 0.0.0.0 --server.port 8501

Screen modules live under screens/. Each exposes a `render()` function.
"""

import os

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
# Design-system CSS — full palette + primitives loaded from style.css.
# ---------------------------------------------------------------------------

with open("style.css") as _css:
    # The HTML parser closes <style> at the first literal </style> it sees,
    # even inside a CSS /* */ comment. style.css's leading USAGE comment
    # contains a literal </style>, which prematurely terminates the tag and
    # dumps the rest of the file to the page as text. Escape it so the HTML
    # parser doesn't see a close tag (CSS parser, inside a comment, ignores).
    _css_text = _css.read().replace("</style>", "<\\/style>")
    st.html(f"<style>{_css_text}</style>")

# ---------------------------------------------------------------------------
# Session state initialisation
# ---------------------------------------------------------------------------


def _init_state():
    defaults = {
        "authenticated":    False,
        "screen":           "login",
        "raw_list":         "",
        "parsed_result":    None,
        "staples_added":    False,
        "combined_items":   [],
        "scan_result":      None,
        "matched_items":    [],
        "review_index":     0,
        "confirmed_items":  [],
        "skipped_items":    [],
        "not_found_items":  [],
        "new_prefs_count":  0,
        "cart_result":      None,
        "review_history":       [],
        "sale_switches":        [],
        "auto_confirmed_items": [],
        "item_filter_selections": {},
        "oauth_state":          None,
        "oauth_code_verifier":  None,
        "kroger_auth_msg":      None,
    }
    for key, value in defaults.items():
        if key not in st.session_state:
            st.session_state[key] = value


_init_state()

# ---------------------------------------------------------------------------
# Imports — screens are imported after CSS + session init so any module-load
# side effects see a fully-bootstrapped environment.
# ---------------------------------------------------------------------------

import kroger_auth  # noqa: E402
from supabase_kv import kv_delete, kv_get  # noqa: E402

from screens import (  # noqa: E402
    connect_kroger,
    home,
    item_filter,
    login,
    mealplan_bootstrap,
    mealplan_rules,
    mealplan_state_import,
    preferences,
    preview,
    review,
    sale_scan,
    staples,
    store_setup,
    summary,
)
from screens._shared import go, pending_oauth_key  # noqa: E402


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

    pending = kv_get(pending_oauth_key(state), None)

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
        kv_delete(pending_oauth_key(state))
        st.session_state.kroger_auth_msg = "✓ Kroger connected. You're ready to shop."
        if st.session_state.authenticated:
            go("home")
    except RuntimeError as e:
        st.error(f"Kroger authorization failed: {e}")


# ---------------------------------------------------------------------------
# Router
# ---------------------------------------------------------------------------

SCREENS = {
    "home":                  home.render,
    "preview":               preview.render,
    "item_filter":           item_filter.render,
    "sale_scan":             sale_scan.render,
    "review":                review.render,
    "summary":               summary.render,
    "preferences":           preferences.render,
    "staples":               staples.render,
    "store_setup":           store_setup.render,
    "connect_kroger":        connect_kroger.render,
    # Meal Planner — Phase 3+. Additional screens land in subsequent phases.
    "mealplan_rules":        mealplan_rules.render,
    "mealplan_state_import": mealplan_state_import.render,
    "mealplan_bootstrap":    mealplan_bootstrap.render,
}


def main():
    # Finish any in-flight Kroger OAuth round-trip before rendering anything.
    _handle_oauth_callback()

    if not st.session_state.authenticated:
        login.render()
        return

    screen = st.session_state.screen
    render_fn = SCREENS.get(screen)
    if render_fn is None:
        go("home")
        return
    render_fn()


# Gate the entry point so other modules (e.g. screens/home.py reading
# SCREENS for the admin nav) can `import main` without triggering a full
# re-render. Streamlit runs this file as the script, so __name__ is
# "__main__" in the real flow and main() still fires.
if __name__ == "__main__":
    main()
