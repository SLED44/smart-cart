"""Store setup screen — first-run Kroger location finder."""

import requests
import streamlit as st

import kroger_auth

from screens._shared import go, save_location_id


def render():
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
                name = loc.get("name", "Unknown")
                address = loc.get("address", {})
                addr_str = (
                    address.get('addressLine1', '') + ' ' +
                    address.get('city', '') + ' ' +
                    address.get('state', '') + ' ' +
                    address.get('zipCode', '')
                ).strip()
                hours = loc.get("hours", "")
                st.write(f"**{name}**")
                st.write(addr_str)
                if hours:
                    st.caption(hours)
            with col_select:
                loc_id = loc.get("locationId", "")
                if st.button("Select", key=f"sel_loc_{loc_id}"):
                    save_location_id(loc_id)
                    st.success(f"Store set to {name}.")
                    st.info(f"Your location ID is: **{loc_id}** (saved to Supabase).")
                    st.session_state["selected_location_id"] = loc_id
            st.divider()

    selected = st.session_state.get("selected_location_id")
    if selected:
        if st.button("← Go to Home"):
            go("home")

    elif st.button("← Back"):
        go("home")


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
