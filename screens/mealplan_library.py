"""
Recipe library browser — search, filter, and manage saved recipes.

Per PRD §18:
    - Top bar: name search + cuisine + protein + status filters
    - Card grid (3 per row at tablet horizontal)
    - Per-card: image, title, tags, last-cooked, times-cooked, status badge
    - Per-card admin: Toggle favorite, Toggle never-again, Edit notes, Delete
"""

import streamlit as st

from mealplan import library
from mealplan.event_log import EVT_RECIPE_NEVER_AGAIN, log_event
from mealplan.rules import _VALID_PROTEINS, load_rules, save_rules

from screens._shared import go

DEFAULT_FAV_CADENCE = [4, 6]
_CARDS_PER_ROW = 3

_STATUS_BADGES = {
    library.STATUS_FAVORITE:    "⭐ favorite",
    library.STATUS_NEVER_AGAIN: "🚫 never_again",
    library.STATUS_ACTIVE:      "",
}


def render():
    st.title("📚 Recipe library")

    summary = library.data_summary()
    all_recipes = library.get_all() or {}

    # --- Top bar ----------------------------------------------------------
    col_back, col_paste, col_bootstrap = st.columns([1, 2, 2])
    with col_back:
        if st.button("← Home", key="lib_back"):
            go("mealplan_home")
    with col_paste:
        if st.button("📝 Paste a recipe", use_container_width=True, key="lib_paste"):
            go("mealplan_paste_recipe")
    with col_bootstrap:
        if st.button("🌱 Bootstrap more", use_container_width=True, key="lib_boot"):
            go("mealplan_bootstrap")

    st.caption(
        f"**{summary['total']}** recipes · "
        f"{summary['by_status'].get('favorite', 0)} favorites · "
        f"{summary['by_status'].get('never_again', 0)} excluded"
    )

    st.divider()

    # --- Filters ----------------------------------------------------------
    cuisines_avail = sorted(summary.get("by_cuisine", {}).keys())
    proteins_avail = sorted(summary.get("by_protein", {}).keys()) \
        or list(_VALID_PROTEINS)

    col_q, col_cui, col_pro, col_st = st.columns([2, 2, 2, 2])
    with col_q:
        name_search = st.text_input("Search title", key="lib_q",
                                    placeholder="e.g. salmon")
    with col_cui:
        cuisines = st.multiselect("Cuisines", cuisines_avail, key="lib_cui")
    with col_pro:
        proteins = st.multiselect("Proteins", proteins_avail, key="lib_pro")
    with col_st:
        statuses = st.multiselect(
            "Status",
            [library.STATUS_ACTIVE, library.STATUS_FAVORITE, library.STATUS_NEVER_AGAIN],
            default=[library.STATUS_ACTIVE, library.STATUS_FAVORITE],
            key="lib_status",
        )

    matches = library.filter(
        cuisine=cuisines or None,
        protein=proteins or None,
        status=statuses or None,
        name_search=name_search or None,
    )
    matches.sort(key=lambda r: ((r.get("title") or "").lower()))

    st.divider()
    st.caption(f"**{len(matches)}** match(es)")

    if not matches:
        st.info("Nothing matches. Loosen filters, paste a recipe, or run bootstrap.")
        return

    # --- Card grid --------------------------------------------------------
    rules = load_rules()
    for row_start in range(0, len(matches), _CARDS_PER_ROW):
        row = matches[row_start:row_start + _CARDS_PER_ROW]
        cols = st.columns(_CARDS_PER_ROW)
        for i, recipe in enumerate(row):
            with cols[i]:
                _render_card(recipe, rules)


# ---------------------------------------------------------------------------
# Card
# ---------------------------------------------------------------------------

def _render_card(recipe: dict, rules: dict):
    rid = recipe.get("id", "")
    with st.container(border=True):
        if recipe.get("image_url"):
            st.image(recipe["image_url"], use_container_width=True)
        st.markdown(f"**{recipe.get('title','(untitled)')}**")
        badge = _STATUS_BADGES.get(recipe.get("status", library.STATUS_ACTIVE), "")
        if badge:
            st.caption(badge)
        bits = []
        if recipe.get("cuisines"):
            bits.append(", ".join(recipe["cuisines"]))
        if recipe.get("proteins"):
            bits.append("· " + "/".join(recipe["proteins"]))
        if recipe.get("ready_in_minutes"):
            bits.append(f"· {recipe['ready_in_minutes']} min")
        if bits:
            st.caption(" ".join(bits))
        meta_bits = []
        if recipe.get("times_cooked"):
            meta_bits.append(f"cooked ×{recipe['times_cooked']}")
        if recipe.get("last_cooked_at"):
            meta_bits.append(f"last {recipe['last_cooked_at'][:10]}")
        if meta_bits:
            st.caption(" · ".join(meta_bits))
        st.caption(f"`{rid}`")

        with st.expander("Manage"):
            _render_admin_controls(recipe, rules)


def _render_admin_controls(recipe: dict, rules: dict):
    rid = recipe["id"]
    status = recipe.get("status", library.STATUS_ACTIVE)

    # Notes editor — inline so it's always visible
    notes = st.text_area(
        "Notes",
        value=recipe.get("user_notes", ""),
        key=f"lib_notes_{rid}",
        height=80,
        placeholder="One-line learnings, swaps, kid feedback…",
    )
    if notes != (recipe.get("user_notes") or ""):
        if st.button("Save notes", key=f"lib_notes_save_{rid}",
                     use_container_width=True):
            recipe["user_notes"] = notes
            library.save(recipe)
            st.success("Notes saved.")
            st.rerun()

    col_fav, col_never = st.columns(2)
    fav_ids = {f.get("recipe_id") for f in (rules.get("favorites") or [])}
    is_fav = rid in fav_ids or status == library.STATUS_FAVORITE
    with col_fav:
        if is_fav:
            if st.button("☆ Unfavorite", key=f"lib_unfav_{rid}",
                         use_container_width=True):
                _unfavorite(rid, rules)
                st.rerun()
        else:
            if st.button("⭐ Mark favorite", key=f"lib_fav_{rid}",
                         use_container_width=True):
                _favorite(rid, rules)
                st.rerun()
    with col_never:
        if status == library.STATUS_NEVER_AGAIN:
            if st.button("↺ Restore", key=f"lib_restore_{rid}",
                         use_container_width=True):
                library.set_status(rid, library.STATUS_ACTIVE)
                excl = [x for x in (rules.get("exclusions") or []) if x != rid]
                rules["exclusions"] = excl
                save_rules(rules)
                st.rerun()
        else:
            if st.button("🚫 Never again", key=f"lib_never_{rid}",
                         use_container_width=True):
                library.set_status(rid, library.STATUS_NEVER_AGAIN)
                excl = set(rules.get("exclusions") or [])
                excl.add(rid)
                rules["exclusions"] = sorted(excl)
                save_rules(rules)
                log_event(EVT_RECIPE_NEVER_AGAIN, {
                    "recipe_id": rid,
                    "title":     recipe.get("title", ""),
                    "via":       "library_browser",
                })
                st.rerun()

    confirm_key = f"lib_del_confirm_{rid}"
    if st.session_state.get(confirm_key):
        st.warning("Delete forever?")
        c_no, c_yes = st.columns(2)
        with c_no:
            if st.button("Cancel", key=f"lib_del_no_{rid}",
                         use_container_width=True):
                st.session_state[confirm_key] = False
                st.rerun()
        with c_yes:
            if st.button("Delete", type="primary", key=f"lib_del_yes_{rid}",
                         use_container_width=True):
                library.delete(rid)
                # Also scrub from favorites + exclusions.
                rules["favorites"] = [f for f in (rules.get("favorites") or [])
                                      if f.get("recipe_id") != rid]
                rules["exclusions"] = [x for x in (rules.get("exclusions") or []) if x != rid]
                save_rules(rules)
                st.session_state[confirm_key] = False
                st.rerun()
    else:
        if st.button("🗑 Delete from library", key=f"lib_del_{rid}",
                     use_container_width=True):
            st.session_state[confirm_key] = True
            st.rerun()


def _favorite(rid: str, rules: dict):
    library.set_status(rid, library.STATUS_FAVORITE)
    existing = {f.get("recipe_id") for f in (rules.get("favorites") or [])}
    if rid not in existing:
        rules.setdefault("favorites", []).append({
            "recipe_id":      rid,
            "cadence_weeks":  list(DEFAULT_FAV_CADENCE),
            "last_used_week": None,
        })
    save_rules(rules)


def _unfavorite(rid: str, rules: dict):
    library.set_status(rid, library.STATUS_ACTIVE)
    rules["favorites"] = [f for f in (rules.get("favorites") or [])
                          if f.get("recipe_id") != rid]
    save_rules(rules)
