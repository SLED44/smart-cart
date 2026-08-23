"""Login screen — household APP_PASSWORD gate."""

import os

import streamlit as st

import auth
from screens._shared import DEFAULT_SCREEN, go


def render():
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
                    # Signs in *and* drops the signed cookie, so a refresh or
                    # an OAuth round-trip doesn't ask again.
                    auth.start_session()
                    # Land on the meal planner — it's the daily-use entry point.
                    go(DEFAULT_SCREEN)
                else:
                    st.error("Incorrect password.")
