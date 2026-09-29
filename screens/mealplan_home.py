"""
Meal Planner home — landing for the meal-plan tab.

What it shows:
    - Library status (count, favorites, missing pieces)
    - Current week's confirmed plan (if any) with quick link to active view
    - In-progress lineup (if any) with Resume / Discard
    - Primary CTA: "Plan this week" with N input
    - Settings expander: Rules / Bootstrap / Library / Paste / State import
"""

import streamlit as st

import auth
from mealplan import library
from mealplan.protein_match import parse_request
from mealplan.rules import load_rules
from sc_design import (
    hero_tile_card,
    plan_hero_header,
    plan_hero_note,
    stat_card,
)
from supabase_kv import kv_delete, kv_get

from screens import _recipe_view
from screens._shared import go

KEY_PENDING_LINEUP = "pending_lineup"
KEY_CURRENT_PLAN = "current_plan"

# Admin links shown in the Settings expander. Filtered against main.SCREENS
# so unfinished phases don't show as dead links.
_SETTINGS_LINKS = (
    ("mealplan_rules",        "⚙ Rules"),
    ("mealplan_bootstrap",    "🌱 Bootstrap library"),
    ("mealplan_library",      "📚 Browse library"),
    ("mealplan_paste_recipe", "📝 Paste a recipe"),
    ("mealplan_state_import", "📥 Import state"),
)


def render():
    # The cook screen returns here when you opened a meal from this page, so
    # its "Made it" / notes-saved flash has to land here too.
    flash = st.session_state.pop("mealplan_cook_flash", None)
    if flash:
        st.success(flash)

    st.title("🍳 Meal Planner")
    st.caption("Build a weekly lineup that respects your rules, hand the grocery list "
               "to SmartCart, and capture what worked.")

    summary = library.data_summary()
    rules = load_rules()
    pending = kv_get(KEY_PENDING_LINEUP, None)
    current = kv_get(KEY_CURRENT_PLAN, None)

    # Hero first: the primary action for the current state leads the page so
    # the thing you most want (open / resume / plan) is never below the fold.
    if pending and (pending.get("meals") or pending.get("titles")):
        _render_pending_section(pending, rules)
    elif current and current.get("meals"):
        _render_current_plan_section(current)
    else:
        _render_plan_new_section(summary, rules)

    # Stats demoted to a secondary strip beneath the hero.
    st.divider()
    _render_stats(summary, rules, current)
    _render_settings_expander(summary)


# ---------------------------------------------------------------------------
# Sections
# ---------------------------------------------------------------------------

def _render_stats(summary: dict, rules: dict, current: dict | None):
    by_status = summary.get("by_status") or {}
    col_a, col_b, col_c = st.columns(3)
    with col_a:
        st.html(stat_card(
            tone="green", glyph="📚",
            label="Library size",
            value=summary["total"],
            sub=f"{by_status.get('favorite', 0)} favorites · "
                f"{by_status.get('never_again', 0)} excluded",
        ))
    with col_b:
        cw = int(((rules.get("state") or {}).get("current_week")) or 1)
        st.html(stat_card(
            tone="grape", glyph="📅",
            label="Current week",
            value=cw,
            sub="rules-engine week counter",
        ))
    with col_c:
        plan_status = "Ready" if current and current.get("meals") else "—"
        st.html(stat_card(
            tone="sky", glyph="🗓",
            label="This week's plan",
            value=len(current.get("meals", [])) if current else 0,
            sub=f"meals · {plan_status}",
        ))


def _render_plan_new_section(summary: dict, rules: dict):
    st.subheader("Plan this week")
    if summary["total"] < 5:
        st.warning(
            f"Library only has {summary['total']} recipe(s). The planner needs at "
            f"least a handful to make sensible picks. Run **🌱 Bootstrap library** "
            f"first (Settings below)."
        )

    default_n = int(((rules.get("household") or {}).get("meals_per_week_default")) or 5)
    n = int(st.number_input(
        "How many meals do you want to plan?",
        min_value=1, max_value=7, value=default_n, step=1,
        key="mph_plan_n",
    ))

    sc_default = bool((rules.get("household") or {}).get("slow_cooker_default", True))
    include_sc = st.checkbox(
        "🍲 Include a slow-cooker meal this plan",
        value=sc_default, key="mph_plan_sc",
        help="When on, the planner works one hands-off slow-cooker dinner into the week.",
    )

    protein_req = _protein_input("mph_plan_protein")

    disabled = summary["total"] == 0
    if st.button(
        f"Plan {n} meal{'s' if n != 1 else ''} →",
        type="primary", use_container_width=True,
        disabled=disabled, key="mph_plan_start",
    ):
        # Stash N + the slow-cooker choice for the propose screen.
        st.session_state.mealplan_propose_n = n
        st.session_state.mealplan_propose_include_sc = include_sc
        st.session_state.mealplan_propose_protein = protein_req
        st.session_state.mealplan_propose_fresh = True  # propose screen sees this
                                                        # → generate, then clear
        go("mealplan_propose")


def _protein_input(key: str) -> dict | None:
    """Optional "use up this protein" field. Returns the parsed request
    (protein_match.parse_request) or None when blank."""
    text = st.text_input(
        "🎯 Protein to use up (optional)", key=key,
        placeholder="e.g. 1.5 lb pork shoulder",
        help="One meal in the plan will use this cut. Replacing that meal "
             "shows only other recipes for the same cut.",
    )
    return parse_request(text)


def _resolve_recipes(slots: list[dict], lib: dict) -> list[tuple[str | None, dict | None]]:
    """Resolve plan slots to (recipe_id, recipe) pairs for the hero tiles,
    indexing a single library snapshot (avoids one KV round-trip per slot).
    A recipe deleted since the plan was confirmed resolves to None — the tile
    still renders, it just isn't openable."""
    out = []
    for slot in slots:
        rid = slot.get("recipe_id")
        out.append((rid, lib.get(rid) if rid else None))
    return out


# Four tiles per row keeps titles readable at 7 meals; a fixed column count
# also stops a 2-meal week from stretching two tiles across the whole card.
_TILES_PER_ROW = 4


def _tile_label(title: str) -> str:
    """Button labels don't wrap gracefully past a couple of lines."""
    title = title.strip() or "(untitled)"
    return title if len(title) <= 42 else title[:41].rstrip() + "…"


def _render_meal_tiles(entries, *, key_prefix: str, on_open,
                       help_text: str, blocked_help: str):
    """Hero meal tiles — art, title and meta, each whole tile one click target.

    The tiles started as a single st.html grid, which looked like a row of
    cards but swallowed every click. Streamlit can't put markup inside a
    button, so each tile is a keyed container holding the card markup plus a
    button; style.css stretches that button across the container as an
    invisible hit layer (`.st-key-mph_card_on_*`) and the container draws the
    card's border and hover state. Clicking anywhere on the card opens it.

    If that CSS ever fails to load, the button simply renders under the card
    with the recipe's title on it — still a working way in, just uglier.

    A tile with no library recipe behind it (deleted since the plan was
    confirmed, or an imported title with nothing attached yet) still renders,
    keyed `..._off_...` so it gets neither the hit layer nor the hover state.
    """
    for start in range(0, len(entries), _TILES_PER_ROW):
        cols = st.columns(_TILES_PER_ROW)
        for offset, (rid, recipe) in enumerate(entries[start:start + _TILES_PER_ROW]):
            i = start + offset
            openable = bool(recipe and recipe.get("id"))
            state = "on" if openable else "off"
            with cols[offset]:
                with st.container(key=f"mph_card_{state}_{key_prefix}_{i}"):
                    st.html(hero_tile_card(recipe or {},
                                           fallback_title=f"(missing {rid})"))
                    # No help= tooltip: it wraps the button in extra spans
                    # that stop the hit layer from filling the card. The
                    # label doubles as the accessible name.
                    if st.button(
                        _tile_label((recipe or {}).get("title") or f"(missing {rid})"),
                        key=f"mph_tile_{key_prefix}_{i}",
                        use_container_width=True,
                        disabled=not openable,
                    ):
                        on_open(rid, recipe)


def _render_pending_section(pending: dict, rules: dict):
    meals = pending.get("meals") or []
    titles = pending.get("titles") or []
    if meals:
        entries = _resolve_recipes(meals, library.get_all())
        touched = pending.get("updated_at", "")
        meta_right = (f"{len(meals)} slot(s) · {touched[:10]}"
                      if touched else f"{len(meals)} slot(s)")
    else:
        # Imported titles have no library entry yet — nothing to open.
        entries = [(None, {"title": t}) for t in titles]
        meta_right = "imported titles"

    with st.container(border=True):
        st.html(plan_hero_header(
            tone="amber",
            heading="Plan in progress",
            pill_text="In progress",
            meta_right=meta_right,
        ))
        if entries:
            # Nothing is confirmed yet, so a tile previews the recipe rather
            # than dropping you into cooking mode.
            _render_meal_tiles(
                entries, key_prefix="pending",
                on_open=lambda rid, recipe: _preview_meal(recipe, rules),
                help_text="Preview this recipe",
                blocked_help="Not in your library yet — resume planning to fill this slot",
            )
        else:
            st.html(plan_hero_note("No slots yet — resume to start filling them."))

    col_resume, col_discard = st.columns([2, 1])
    with col_resume:
        if st.button("Resume planning →", type="primary",
                     use_container_width=True, key="mph_resume"):
            go("mealplan_propose")
    with col_discard:
        if st.button("Discard", key="mph_discard", use_container_width=True):
            kv_delete(KEY_PENDING_LINEUP)
            st.rerun()


def _render_current_plan_section(current: dict):
    meals = current.get("meals") or []
    entries = _resolve_recipes(meals, library.get_all())
    confirmed = current.get("confirmed_at", "")
    with st.container(border=True):
        st.html(plan_hero_header(
            tone="green",
            heading="This week's plan",
            pill_text="Confirmed",
            meta_right=f"Week #{current.get('week_number','?')}"
                       + (f" · {confirmed[:10]}" if confirmed else ""),
        ))
        _render_meal_tiles(
            entries, key_prefix="current",
            on_open=_open_meal,
            help_text="Open this meal in cooking mode",
            blocked_help="This recipe was deleted from the library",
        )

    # Size the next week up front, so you don't generate 5 when you need 2.
    col_n, col_sc = st.columns([1, 2])
    with col_n:
        replan_n = int(st.number_input(
            "Meals next week", min_value=1, max_value=7,
            value=len(meals) or 5, step=1, key="mph_replan_n"))
    with col_sc:
        replan_sc = st.checkbox(
            "🍲 Include a slow-cooker meal",
            value=bool(current.get("include_slow_cooker", True)),
            key="mph_replan_sc")
    replan_protein = _protein_input("mph_replan_protein")

    col_active, col_new = st.columns([2, 1])
    with col_active:
        if st.button("📅 Open this week's plan", type="primary",
                     use_container_width=True, key="mph_open_active"):
            go("mealplan_active")
    with col_new:
        if st.button("🔄 Plan a new week",
                     use_container_width=True, key="mph_replan"):
            # propose only generates when this flag is set; without it (and with
            # no pending lineup) it dead-ends on "No plan in progress".
            st.session_state.mealplan_propose_n = replan_n
            st.session_state.mealplan_propose_include_sc = replan_sc
            st.session_state.mealplan_propose_protein = replan_protein
            st.session_state.mealplan_propose_fresh = True
            go("mealplan_propose")


def _open_meal(rid: str, recipe: dict):
    """Straight from the home screen into cooking mode for one meal."""
    st.session_state.mealplan_cook_recipe_id = rid
    # So the cook screen's back button returns where you came from.
    st.session_state.mealplan_cook_return = "mealplan_home"
    go("mealplan_cook")


def _preview_meal(recipe: dict, rules: dict):
    """Modal recipe preview — used for not-yet-confirmed (pending) tiles."""
    _recipe_view.open_preview(recipe, _recipe_view.compute_scale(recipe, rules))


def _render_settings_expander(summary: dict):
    import main  # router owns the registry
    available = [(sid, label) for sid, label in _SETTINGS_LINKS
                 if sid in main.SCREENS]
    with st.expander("⚙ Settings + admin"):
        st.caption(
            "Configure rules, grow the library, paste a recipe Claude.ai normalised "
            "for you, or import your existing rules-doc state."
        )
        if available:
            cols = st.columns(min(3, len(available)))
            for i, (sid, label) in enumerate(available):
                with cols[i % len(cols)]:
                    if st.button(label, key=f"mph_set_{sid}", use_container_width=True):
                        go(sid)

        # Login now persists across reloads via a cookie, so there has to be
        # a way to end it — on a shared or borrowed device especially.
        st.divider()
        st.caption("Signed in on this device. Sign out to clear it.")
        if st.button("🚪 Sign out", key="mph_sign_out"):
            auth.end_session()
            go("login")
