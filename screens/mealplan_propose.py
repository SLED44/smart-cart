"""
Propose screen — view, keep, replace, or reroll a proposed lineup.

State machine:
    - Entry: if mealplan_propose_fresh in session_state → generate a fresh
      lineup of N meals, save to pending_lineup KV, clear the flag.
    - Otherwise: load pending_lineup from KV.
    - Per slot: Keep / Replace (→ mealplan_swap). Whole-plan: "Give me N
      new options" rerolls. "Confirm plan" persists current_plan, appends
      to meal_plan_history, bumps rules state, clears pending_lineup,
      routes to active screen.
"""

from datetime import datetime, timezone

import streamlit as st

from mealplan import library
from mealplan.planner import (
    NoCandidatesError,
    SlotResult,
    generate_lineup,
    lineup_meta,
    regenerate_lineup,
)
from mealplan.rules import bump_state_after_confirm, load_rules, save_rules
from supabase_kv import kv_delete, kv_get, kv_put

from screens._shared import go

KEY_PENDING_LINEUP = "pending_lineup"
KEY_CURRENT_PLAN = "current_plan"
KEY_HISTORY = "meal_plan_history"
HISTORY_CAP = 26  # 6 months of weekly plans


def render():
    st.title("🍳 Plan this week")

    rules = load_rules()
    pending = _load_or_generate(rules)
    if pending is None:
        return  # _load_or_generate already wrote a message + go("mealplan_home")

    n = int(pending.get("n") or len(pending.get("meals") or []))
    meals = pending.get("meals") or []

    # Top action bar
    col_back, col_reroll, col_confirm = st.columns([1, 2, 2])
    with col_back:
        if st.button("← Back", key="mp_propose_back"):
            go("mealplan_home")
    with col_reroll:
        if st.button(f"🔄 Give me {n} new options",
                     use_container_width=True, key="mp_propose_reroll"):
            _reroll(n, rules, meals)
    with col_confirm:
        all_filled = all(m.get("recipe_id") for m in meals)
        if st.button("✓ Confirm plan", type="primary",
                     disabled=not all_filled, use_container_width=True,
                     key="mp_propose_confirm"):
            _confirm(meals, rules)
            return

    st.caption(f"{sum(1 for m in meals if m.get('recipe_id'))} / {n} slots filled")

    # "Why these picks?" — recompute lineup_meta from current recipes
    _render_why_panel(meals, rules)

    st.divider()

    # Slot cards
    for slot in meals:
        _render_slot_card(slot)


# ---------------------------------------------------------------------------
# Generation / loading
# ---------------------------------------------------------------------------

def _load_or_generate(rules: dict) -> dict | None:
    """Return the pending lineup dict, generating if entered from home fresh."""
    if st.session_state.pop("mealplan_propose_fresh", False):
        n = int(st.session_state.pop("mealplan_propose_n", None)
                or (rules.get("household") or {}).get("meals_per_week_default") or 5)
        pending = _generate_pending(n, rules)
        if pending is None:
            return None
        kv_put(KEY_PENDING_LINEUP, pending)
        return pending

    pending = kv_get(KEY_PENDING_LINEUP, None)
    if not pending or not pending.get("meals"):
        # Nothing in flight — bounce home.
        st.warning("No plan in progress. Start one from the meal-planner home.")
        if st.button("← Back to home", key="mp_propose_no_pending"):
            go("mealplan_home")
        return None
    return pending


def _generate_pending(n: int, rules: dict, exclude_ids: set[str] | None = None) -> dict | None:
    """Run the planner and wrap the result in a pending_lineup dict."""
    pool = library.all_active()
    if not pool:
        st.error("Library is empty. Run **🌱 Bootstrap library** first (home → Settings).")
        if st.button("← Back to home", key="mp_propose_empty"):
            go("mealplan_home")
        return None

    history = kv_get(KEY_HISTORY, []) or []
    try:
        result = generate_lineup(n, rules, pool, history=history, exclude_ids=exclude_ids)
    except NoCandidatesError as e:
        st.error(
            f"Couldn't fill slot {e.slot_index + 1} — hard rules wiped the pool. "
            f"Loosen exclusions or grow the library."
        )
        partial = e.partial
        st.caption(f"Got as far as {len(partial)} of {n} slots before failing.")
        if st.button("← Back to home", key="mp_propose_nocand"):
            go("mealplan_home")
        return None

    return {
        "n":          n,
        "meals":      [_slot_to_dict(i, s) for i, s in enumerate(result.slots)],
        "updated_at": _now_iso(),
    }


def _slot_to_dict(i: int, s: SlotResult) -> dict:
    return {
        "slot":             i,
        "recipe_id":        s.recipe.get("id"),
        "added_via":        s.added_via,
        "score":            s.score,
        "relaxation_level": s.relaxation_level,
        "reasons":          list(s.reasons),
        "relaxations":      list(s.relaxations_applied),
    }


def _reroll(n: int, rules: dict, current_meals: list[dict]):
    prior_ids = {m.get("recipe_id") for m in current_meals if m.get("recipe_id")}
    with st.spinner("Rerolling lineup…"):
        new_pending = _generate_pending(n, rules, exclude_ids=prior_ids)
    if new_pending is not None:
        kv_put(KEY_PENDING_LINEUP, new_pending)
    st.rerun()


# ---------------------------------------------------------------------------
# Per-slot card
# ---------------------------------------------------------------------------

def _render_slot_card(slot: dict):
    rid = slot.get("recipe_id")
    recipe = library.get(rid) if rid else None

    with st.container(border=True):
        col_img, col_body, col_actions = st.columns([1, 4, 1])

        if recipe and recipe.get("image_url"):
            with col_img:
                st.image(recipe["image_url"], width=140)
        elif not recipe:
            with col_img:
                st.write("📭")

        with col_body:
            if recipe:
                st.markdown(f"### Slot {slot.get('slot', 0) + 1}: {recipe.get('title','(untitled)')}")
                meta_bits = []
                if recipe.get("cuisines"):
                    meta_bits.append(", ".join(recipe["cuisines"]))
                if recipe.get("proteins"):
                    meta_bits.append("· " + "/".join(recipe["proteins"]))
                if recipe.get("ready_in_minutes"):
                    meta_bits.append(f"· {recipe['ready_in_minutes']} min")
                if meta_bits:
                    st.caption(" ".join(meta_bits))
                if recipe.get("last_cooked_at"):
                    st.caption(f"last cooked: {recipe['last_cooked_at'][:10]}")
                else:
                    st.caption("never cooked yet")
                if slot.get("added_via") == "favorite":
                    st.caption("⭐ favorite — auto-included")
                if slot.get("reasons"):
                    with st.expander("Why this one?"):
                        for r in slot["reasons"]:
                            st.caption(f"• {r}")
                        for r in (slot.get("relaxations") or []):
                            st.caption(f"⚙ {r}")
            else:
                st.markdown(f"### Slot {slot.get('slot', 0) + 1}: _(empty)_")
                st.caption("Pick something via Replace →")

        with col_actions:
            if st.button("Replace", key=f"mp_propose_replace_{slot['slot']}"):
                st.session_state.mealplan_swap_slot_index = slot["slot"]
                go("mealplan_swap")


# ---------------------------------------------------------------------------
# Why these picks? panel
# ---------------------------------------------------------------------------

def _render_why_panel(meals: list[dict], rules: dict):
    # Reconstruct a LineupResult-like object from the persisted slots so we
    # can call lineup_meta. We only need recipes + relaxation_level; the
    # other fields don't influence the meta aggregates.
    from mealplan.planner import LineupResult, SlotResult

    recipes = []
    slots: list[SlotResult] = []
    for m in meals:
        r = library.get(m.get("recipe_id")) if m.get("recipe_id") else None
        if not r:
            continue
        recipes.append(r)
        slots.append(SlotResult(
            recipe=r, score=float(m.get("score", 0)),
            relaxation_level=int(m.get("relaxation_level", 0)),
            reasons=list(m.get("reasons") or []),
            relaxations_applied=list(m.get("relaxations") or []),
            added_via=m.get("added_via", "proposal"),
        ))
    if not slots:
        return
    result = LineupResult(slots=slots)
    meta = lineup_meta(result, rules)

    with st.expander("Why these picks?"):
        col_p, col_cu, col_c = st.columns(3)
        with col_p:
            st.markdown("**Proteins**")
            for k, v in meta["protein_counts"].items():
                st.caption(f"• {k}: ×{v}")
        with col_cu:
            st.markdown("**Cuisines**")
            for k, v in meta["cuisine_counts"].items():
                st.caption(f"• {k}: ×{v}")
        with col_c:
            st.markdown("**Carbs**")
            for k, v in meta["carb_counts"].items():
                st.caption(f"• {k}: ×{v}")

        if meta["must_include_missing"]:
            st.warning(f"Missing must-include cuisine: {', '.join(meta['must_include_missing'])}")
        if meta["must_include_covered"]:
            st.caption(f"Covered must-include: {', '.join(meta['must_include_covered'])}")

        sh = meta.get("shrimp_status")
        if sh:
            ws = sh.get("weeks_since")
            cd = sh.get("cadence")
            if ws is None:
                st.caption(f"🦐 shrimp cadence: never used (cadence {cd}wk)")
            else:
                st.caption(f"🦐 shrimp cadence: {ws}wk since last (cadence {cd}wk)")

        favs = meta.get("favorites_status") or []
        if favs:
            st.markdown("**Favorites**")
            for f in favs:
                gap = f.get("gap")
                marker = ""
                if f.get("force"):
                    marker = " 🔴 FORCE"
                elif f.get("due"):
                    marker = " 🟡 due"
                if gap is None:
                    st.caption(f"• {f['recipe_id']}: never used{marker}")
                else:
                    st.caption(f"• {f['recipe_id']}: {gap}wk since last{marker}")

        if meta.get("relaxation_summary") and meta["relaxation_summary"] != "no relaxation needed":
            st.warning(f"⚙ Relaxation: {meta['relaxation_summary']}")


# ---------------------------------------------------------------------------
# Confirm
# ---------------------------------------------------------------------------

def _confirm(meals: list[dict], rules: dict):
    recipes = []
    for m in meals:
        r = library.get(m.get("recipe_id")) if m.get("recipe_id") else None
        if r:
            recipes.append(r)
    if len(recipes) != len(meals):
        st.error("Some slots reference missing recipes. Fix and retry.")
        return

    week_number = int(((rules.get("state") or {}).get("current_week")) or 1)
    confirmed_at = _now_iso()

    plan = {
        "week_number":              week_number,
        "confirmed_at":             confirmed_at,
        "meals": [
            {"recipe_id": m["recipe_id"], "added_via": m.get("added_via", "proposal")}
            for m in meals
        ],
        "grocery_list_generated_at": None,
    }
    kv_put(KEY_CURRENT_PLAN, plan)

    # Append to history, cap.
    history = kv_get(KEY_HISTORY, []) or []
    history.append(plan)
    history = history[-HISTORY_CAP:]
    kv_put(KEY_HISTORY, history)

    # Bump rules state + save.
    new_rules = bump_state_after_confirm(rules, recipes)
    save_rules(new_rules)

    kv_delete(KEY_PENDING_LINEUP)

    st.success(f"Plan confirmed for week #{week_number}. Opening this week's plan…")
    go("mealplan_active")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()
