"""Connect Kroger screen — OAuth authorize entry point."""

import streamlit as st

import kroger_auth
from supabase_kv import kv_put

from screens._shared import go, pending_oauth_key


def render():
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
            go("home")
        if st.button("Disconnect (re-auth required)"):
            kroger_auth.clear_stored_tokens()
            st.rerun()
        return

    # Generate a fresh authorization URL on every render of this screen and
    # persist the PKCE verifier to Supabase keyed by `state`. Session_state
    # would be lost during the external redirect, so durable storage matters.
    auth = kroger_auth.build_authorization_url()
    kv_put(pending_oauth_key(auth["state"]), {"code_verifier": auth["code_verifier"]})

    st.info(
        "Click the button to authorize. You'll be redirected to Kroger and "
        "land back here automatically once you grant access."
    )
    st.link_button("Authorize with Kroger →", auth["url"], type="primary")

    if st.button("← Back"):
        go("home")
