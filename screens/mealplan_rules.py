"""
Meal-plan rules editor.

Structured editor for the ``meal_plan_rules`` KV blob. Covers every
section in PRD §17. Pure forms — saves the whole blob via
``mealplan.rules.save_rules()`` which runs server-side validation.

Edit semantics: each widget mutates a working copy held in
``st.session_state.mealplan_rules_draft`` until the user clicks Save.
Reset reloads from KV (discarding unsaved changes).
"""

import streamlit as st

from mealplan import library
from mealplan.event_log import EVT_RULES_CHANGED, log_event, rules_diff
from mealplan.rules import (
    _VALID_PROTEINS,
    _VALID_CARBS,
    _VALID_SPICE,
    default_rules,
    load_rules,
    save_rules,
    validate_rules,
)

from screens._shared import go

_DRAFT_KEY = "mealplan_rules_draft"


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def render():
    st.title("⚙ Meal-Plan Rules")
    st.caption("Family defaults, protein caps, cuisine variety, favorites, exclusions. "
               "All persists to Supabase under `meal_plan_rules`.")

    # Top nav + save bar
    col_back, col_reload, col_defaults, col_save_top = st.columns([1, 1, 1, 2])
    with col_back:
        if st.button("← Home", key="rules_back_top"):
            _discard_draft()
            go("home")
    with col_reload:
        if st.button("↻ Reload from Supabase", key="rules_reload"):
            _discard_draft()
            st.rerun()
    with col_defaults:
        if st.button("🥗 Load dietitian defaults", key="rules_load_defaults"):
            # Replaces the draft with default_rules() — preserves the
            # state.* counters from the existing draft so reloading
            # defaults doesn't lose the current_week / last_used info.
            existing_state = (_get_draft().get("state") or {}).copy()
            fresh = default_rules()
            fresh["state"].update(existing_state)
            st.session_state[_DRAFT_KEY] = fresh
            st.info("Draft replaced with dietitian defaults. Review, then "
                    "**Save Rules** to persist (or **Reload from Supabase** "
                    "to discard).")
            st.rerun()
    with col_save_top:
        if st.button("💾 Save Rules", type="primary", key="rules_save_top",
                     use_container_width=True):
            _save(_get_draft())

    st.divider()

    draft = _get_draft()

    _section_household(draft)
    _section_protein_limits(draft)
    _section_protein_cadences(draft)
    _section_carb_limits(draft)
    _section_cuisine_variety(draft)
    _section_favorites(draft)
    _section_exclusions(draft)
    _section_pair_exclusions(draft)
    _section_state(draft)

    st.divider()
    # Bottom save button — same handler as the top.
    if st.button("💾 Save Rules", type="primary", key="rules_save_bottom",
                 use_container_width=True):
        _save(draft)


# ---------------------------------------------------------------------------
# Draft state
# ---------------------------------------------------------------------------

def _get_draft() -> dict:
    """Lazy-load the working copy from KV (or defaults) on first visit."""
    if _DRAFT_KEY not in st.session_state:
        st.session_state[_DRAFT_KEY] = load_rules()
    return st.session_state[_DRAFT_KEY]


def _discard_draft():
    st.session_state.pop(_DRAFT_KEY, None)


def _save(draft: dict):
    errs = validate_rules(draft)
    if errs:
        st.error("Couldn't save — fix these first:")
        for e in errs:
            st.write(f"• {e}")
        return
    # Snapshot the prior state so the event log records a field-level diff.
    prior = load_rules()
    try:
        save_rules(draft)
    except Exception as e:
        st.error(f"Save failed: {e}")
        return
    diff = rules_diff(prior, draft)
    if diff:
        log_event(EVT_RULES_CHANGED, {"changed_fields": diff})
    st.success("Rules saved." + (f" ({len(diff)} field(s) changed)" if diff else " (no changes)"))
    # Keep the draft in sync with what's now persisted.
    st.session_state[_DRAFT_KEY] = load_rules()


# ---------------------------------------------------------------------------
# Section: Household & Defaults
# ---------------------------------------------------------------------------

def _section_household(draft: dict):
    st.subheader("Family & Defaults")
    h = draft.setdefault("household", {})

    col1, col2 = st.columns(2)
    with col1:
        h["size"] = st.number_input(
            "Household size", min_value=1, max_value=20,
            value=int(h.get("size") or 4), step=1, key="rules_h_size")
        h["meals_per_week_default"] = st.number_input(
            "Default meals / week", min_value=1, max_value=7,
            value=int(h.get("meals_per_week_default") or 5), step=1,
            key="rules_h_mpw")
    with col2:
        spice = h.get("spice", "mild")
        if spice not in _VALID_SPICE:
            spice = "mild"
        h["spice"] = st.selectbox(
            "Spice tolerance", _VALID_SPICE,
            index=_VALID_SPICE.index(spice), key="rules_h_spice")
        appliance_options = ("air_fryer", "oven", "stovetop", "slow_cooker",
                             "instant_pot", "grill")
        appliance = h.get("default_appliance", "air_fryer")
        if appliance not in appliance_options:
            appliance = "air_fryer"
        h["default_appliance"] = st.selectbox(
            "Default appliance", appliance_options,
            index=appliance_options.index(appliance), key="rules_h_app")

    h["buy_dont_make_sauces"] = st.checkbox(
        "Prefer store-bought sauces / dressings (taco seasoning is the exception)",
        value=bool(h.get("buy_dont_make_sauces", True)),
        key="rules_h_sauce")
    st.divider()


# ---------------------------------------------------------------------------
# Section: Protein limits
# ---------------------------------------------------------------------------

def _section_protein_limits(draft: dict):
    st.subheader("Protein limits")
    st.caption("**max_per_week** = soft target (planner penalises but allows). "
               "**absolute_ceiling** = hard cap (planner never exceeds). "
               "Leave a field blank to mean *no limit*.")
    plimits = draft.setdefault("protein_limits", {})
    for p in _VALID_PROTEINS:
        plimits.setdefault(p, {"max_per_week": None, "absolute_ceiling": None})
        entry = plimits[p]
        col_name, col_max, col_ceil = st.columns([2, 2, 2])
        with col_name:
            st.write(f"**{p}**")
        with col_max:
            entry["max_per_week"] = _nullable_int(
                f"max / week — {p}", entry.get("max_per_week"),
                key_prefix=f"rules_pmax_{p}")
        with col_ceil:
            entry["absolute_ceiling"] = _nullable_int(
                f"ceiling — {p}", entry.get("absolute_ceiling"),
                key_prefix=f"rules_pceil_{p}")
    st.divider()


def _nullable_int(label: str, current, *, key_prefix: str) -> int | None:
    """Pair a checkbox with a number_input so the user can express 'no limit'."""
    has_limit = st.checkbox(
        label, value=current is not None, key=f"{key_prefix}_has",
    )
    if not has_limit:
        return None
    return int(st.number_input(
        "value", min_value=0, max_value=99,
        value=int(current) if current is not None else 0, step=1,
        key=f"{key_prefix}_val", label_visibility="collapsed",
    ))


# ---------------------------------------------------------------------------
# Section: Protein cadences (currently shrimp)
# ---------------------------------------------------------------------------

def _section_protein_cadences(draft: dict):
    st.subheader("Protein cadences")
    st.caption("Hard cap on how often a protein can return. Today's only entry is shrimp "
               "(once every 4 weeks). Cadence is enforced — never relaxed.")
    cadences = draft.setdefault("protein_cadences", [])

    to_remove = []
    for i, entry in enumerate(cadences):
        col_p, col_c, col_lu, col_del = st.columns([2, 2, 2, 1])
        with col_p:
            cur = entry.get("protein", "shrimp")
            options = list(_VALID_PROTEINS)
            if cur not in options:
                options.append(cur)
            entry["protein"] = st.selectbox(
                "protein", options, index=options.index(cur),
                key=f"rules_cad_p_{i}", label_visibility="collapsed")
        with col_c:
            entry["cadence_weeks"] = int(st.number_input(
                "cadence (weeks)", min_value=1, max_value=52,
                value=int(entry.get("cadence_weeks") or 4), step=1,
                key=f"rules_cad_c_{i}"))
        with col_lu:
            lu = entry.get("last_used_week")
            entry["last_used_week"] = _nullable_int(
                "last used (week #)", lu, key_prefix=f"rules_cad_lu_{i}")
        with col_del:
            st.write("")  # spacer
            if st.button("✕", key=f"rules_cad_del_{i}"):
                to_remove.append(i)

    for idx in reversed(to_remove):
        cadences.pop(idx)
        st.rerun()

    if st.button("+ Add cadence", key="rules_cad_add"):
        cadences.append({"protein": "shrimp", "cadence_weeks": 4, "last_used_week": None})
        st.rerun()
    st.divider()


# ---------------------------------------------------------------------------
# Section: Carb limits
# ---------------------------------------------------------------------------

def _section_carb_limits(draft: dict):
    st.subheader("Carb limits")
    st.caption("How many times a carb category can appear per week. Blank = no limit.")
    clim = draft.setdefault("carb_limits", {})
    cols = st.columns(3)
    for i, c in enumerate(_VALID_CARBS):
        with cols[i % 3]:
            clim[c] = _nullable_int(
                c.capitalize(), clim.get(c), key_prefix=f"rules_carb_{c}")
    st.divider()


# ---------------------------------------------------------------------------
# Section: Cuisine variety
# ---------------------------------------------------------------------------

_DEFAULT_ROTATION = default_rules()["cuisines"]["rotation_set"]


def _section_cuisine_variety(draft: dict):
    st.subheader("Cuisine variety")
    cs = draft.setdefault("cuisines", {})

    # Show the rotation set as a comma-separated text area so users can
    # easily add a new cuisine without hunting through a dropdown.
    current = cs.get("rotation_set") or _DEFAULT_ROTATION
    text = st.text_area(
        "Rotation set (comma-separated cuisines)",
        value=", ".join(current),
        key="rules_cuis_rot",
        height=80,
    )
    cs["rotation_set"] = [c.strip().lower() for c in text.split(",") if c.strip()]

    cs["must_include_one_of_per_week"] = st.multiselect(
        "Must include at least one of these every week",
        options=cs["rotation_set"],
        default=[c for c in (cs.get("must_include_one_of_per_week") or [])
                 if c in cs["rotation_set"]],
        key="rules_cuis_must",
    )

    cs["forbid_back_to_back_same_cuisine"] = st.checkbox(
        "Forbid back-to-back same cuisine",
        value=bool(cs.get("forbid_back_to_back_same_cuisine", True)),
        key="rules_cuis_btb",
    )
    st.divider()


# ---------------------------------------------------------------------------
# Section: Favorites
# ---------------------------------------------------------------------------

def _section_favorites(draft: dict):
    st.subheader("Favorites")
    st.caption("Recipes that should return on a cadence. **[min, max]** means: planner "
               "starts boosting after `min` weeks, force-includes after `max` weeks.")
    favs = draft.setdefault("favorites", [])

    library_options = _library_recipe_options()

    to_remove = []
    for i, fav in enumerate(favs):
        col_id, col_min, col_max, col_lu, col_del = st.columns([3, 1, 1, 1, 1])
        with col_id:
            fav["recipe_id"] = _recipe_picker(
                fav.get("recipe_id", ""),
                key=f"rules_fav_rid_{i}",
                library_options=library_options,
            )
        cadence = fav.get("cadence_weeks") or [4, 6]
        with col_min:
            cmin = int(st.number_input(
                "min wk", min_value=1, max_value=52, value=int(cadence[0]),
                step=1, key=f"rules_fav_min_{i}"))
        with col_max:
            cmax = int(st.number_input(
                "max wk", min_value=cmin, max_value=52,
                value=max(cmin, int(cadence[1]) if len(cadence) > 1 else cmin),
                step=1, key=f"rules_fav_max_{i}"))
        fav["cadence_weeks"] = [cmin, cmax]
        with col_lu:
            fav["last_used_week"] = _nullable_int(
                "last used", fav.get("last_used_week"),
                key_prefix=f"rules_fav_lu_{i}")
        with col_del:
            st.write("")
            if st.button("✕", key=f"rules_fav_del_{i}"):
                to_remove.append(i)

    for idx in reversed(to_remove):
        favs.pop(idx)
        st.rerun()

    if st.button("+ Add favorite", key="rules_fav_add"):
        favs.append({"recipe_id": "", "cadence_weeks": [4, 6], "last_used_week": None})
        st.rerun()
    st.divider()


# ---------------------------------------------------------------------------
# Section: Exclusions
# ---------------------------------------------------------------------------

def _section_exclusions(draft: dict):
    st.subheader("Exclusions")
    st.caption("Recipes the planner will never surface. Same as marking a recipe "
               "'never again' from cooking mode.")
    excl = draft.setdefault("exclusions", [])
    library_options = _library_recipe_options()

    to_remove = []
    for i, rid in enumerate(excl):
        col_id, col_del = st.columns([6, 1])
        with col_id:
            new_id = _recipe_picker(rid, key=f"rules_excl_{i}",
                                    library_options=library_options)
            excl[i] = new_id
        with col_del:
            if st.button("✕", key=f"rules_excl_del_{i}"):
                to_remove.append(i)

    for idx in reversed(to_remove):
        excl.pop(idx)
        st.rerun()

    if st.button("+ Add exclusion", key="rules_excl_add"):
        excl.append("")
        st.rerun()
    st.divider()


# ---------------------------------------------------------------------------
# Section: Pair exclusions
# ---------------------------------------------------------------------------

def _section_pair_exclusions(draft: dict):
    st.subheader("Pair exclusions")
    st.caption("Recipes that should never appear in the same week.")
    pairs = draft.setdefault("pair_exclusions", [])
    library_options = _library_recipe_options()

    to_remove = []
    for i, pair in enumerate(pairs):
        a = pair[0] if len(pair) > 0 else ""
        b = pair[1] if len(pair) > 1 else ""
        col_a, col_b, col_del = st.columns([3, 3, 1])
        with col_a:
            a = _recipe_picker(a, key=f"rules_pair_a_{i}",
                               library_options=library_options)
        with col_b:
            b = _recipe_picker(b, key=f"rules_pair_b_{i}",
                               library_options=library_options)
        pairs[i] = [a, b]
        with col_del:
            if st.button("✕", key=f"rules_pair_del_{i}"):
                to_remove.append(i)

    for idx in reversed(to_remove):
        pairs.pop(idx)
        st.rerun()

    if st.button("+ Add pair", key="rules_pair_add"):
        pairs.append(["", ""])
        st.rerun()
    st.divider()


# ---------------------------------------------------------------------------
# Section: Current state (read-only + reset)
# ---------------------------------------------------------------------------

def _section_state(draft: dict):
    st.subheader("Current state")
    state = draft.setdefault("state", {})
    col_w, col_sc, col_lp = st.columns(3)
    with col_w:
        st.metric("Current week", state.get("current_week", 1))
    with col_sc:
        st.metric("Shrimp counter", state.get("shrimp_counter", 0))
    with col_lp:
        lp = state.get("last_plan_confirmed_at")
        st.caption("Last plan confirmed")
        st.write(lp[:10] if isinstance(lp, str) and lp else "—")

    confirm_key = "rules_reset_state_confirmed"
    if st.session_state.get(confirm_key):
        st.warning("This zeroes the week counter, clears shrimp counter, "
                   "and forgets favorites' last_used_week. Saved recipes/library untouched.")
        col_no, col_yes = st.columns(2)
        with col_no:
            if st.button("Cancel reset", key="rules_state_reset_no"):
                st.session_state[confirm_key] = False
                st.rerun()
        with col_yes:
            if st.button("Yes, reset", type="primary", key="rules_state_reset_yes"):
                _reset_state(draft)
                st.session_state[confirm_key] = False
                st.success("State reset. Click Save Rules to persist.")
    else:
        if st.button("Reset state…", key="rules_state_reset"):
            st.session_state[confirm_key] = True
            st.rerun()


def _reset_state(draft: dict):
    draft["state"] = {
        "current_week":           1,
        "shrimp_counter":         0,
        "last_plan_confirmed_at": None,
    }
    for fav in draft.get("favorites") or []:
        fav["last_used_week"] = None
    for entry in draft.get("protein_cadences") or []:
        entry["last_used_week"] = None


# ---------------------------------------------------------------------------
# Recipe-picker helper
# ---------------------------------------------------------------------------

def _library_recipe_options() -> list[tuple[str, str]]:
    """Return [(recipe_id, label), ...] for the recipe-picker dropdowns."""
    try:
        recipes = list(library.get_all().values())
    except Exception:
        return []
    recipes.sort(key=lambda r: (r.get("title") or "").lower())
    return [(r.get("id", ""), f"{r.get('title','(untitled)')}  [{r.get('id','')}]")
            for r in recipes if r.get("id")]


def _recipe_picker(current: str, *, key: str,
                   library_options: list[tuple[str, str]]) -> str:
    """
    Two-mode picker:
      - If library has entries, render a selectbox + free-text override
      - Else fall back to plain text input
    Returns the selected recipe_id (or the typed override).
    """
    if not library_options:
        return st.text_input(
            "recipe id", value=current or "", key=key,
            placeholder="e.g. lib_smash_burgers",
            label_visibility="collapsed")

    ids = [rid for rid, _ in library_options]
    labels = [lab for _, lab in library_options]
    sentinel = "__custom__"
    options = ids + [sentinel]
    display = labels + ["✏ Type a custom id…"]

    if current in ids:
        idx = ids.index(current)
    else:
        idx = len(options) - 1  # default to custom mode if id isn't in library

    chosen = st.selectbox(
        "recipe", options, index=idx,
        format_func=lambda v: display[options.index(v)] if v in options else v,
        key=key, label_visibility="collapsed")

    if chosen == sentinel:
        return st.text_input(
            "custom id", value=current if current not in ids else "",
            key=f"{key}_custom", placeholder="e.g. lib_smash_burgers",
            label_visibility="collapsed")
    return chosen
