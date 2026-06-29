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
from mealplan.event_log import (
    EVT_PLAN_CONFIRMED,
    EVT_PLAN_PROPOSED,
    EVT_PLAN_REGENERATED,
    log_event,
)
from mealplan.planner import (
    NoCandidatesError,
    SlotResult,
    extend_lineup,
    generate_lineup,
    lineup_meta,
    regenerate_lineup,
)
from mealplan.rules import (
    bump_state_after_confirm,
    load_rules,
    save_rules,
    with_equipment_target,
)
from supabase_kv import kv_delete, kv_get, kv_put

from screens._shared import go
from screens import _recipe_view
from sc_design import reason_chips, planner_card


def _meta_line(recipe: dict) -> str:
    """'Greek · chicken · 35 min' meta line for a planner card."""
    bits = []
    if recipe.get("cuisines"):
        bits.append(", ".join(c.title() for c in recipe["cuisines"]))
    if recipe.get("proteins"):
        bits.append("/".join(recipe["proteins"]))
    if recipe.get("ready_in_minutes"):
        bits.append(f"{recipe['ready_in_minutes']} min")
    return " · ".join(bits)

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

    # Slow-cooker is a per-plan option, not a hard rule. `eff_rules` applies the
    # plan's choice to generation/meta; the original `rules` is kept for the
    # state bump on confirm (so we never persist the per-plan override).
    include_sc = bool(pending.get("include_slow_cooker", _sc_default(rules)))
    eff_rules = with_equipment_target(rules, "slow_cooker", include_sc)

    # Top action bar
    col_back, col_reroll, col_confirm = st.columns([1, 2, 2])
    with col_back:
        if st.button("← Back", key="mp_propose_back"):
            go("mealplan_home")
    with col_reroll:
        if st.button(f"🔄 Give me {n} new options",
                     use_container_width=True, key="mp_propose_reroll"):
            _reroll(n, eff_rules, meals, include_sc)
    with col_confirm:
        all_filled = all(m.get("recipe_id") for m in meals)
        if st.button("✓ Confirm plan", type="primary",
                     disabled=not all_filled, use_container_width=True,
                     key="mp_propose_confirm"):
            _confirm(meals, rules, include_sc)
            return

    # Plan controls — adjust the size and the slow-cooker option along the way.
    # Resizing keeps existing picks; toggling slow-cooker is structural so it
    # regenerates the lineup under the new setting.
    col_n, col_sc = st.columns([1, 2])
    with col_n:
        new_n = int(st.number_input(
            "Meals this week", min_value=1, max_value=7, value=n, step=1,
            key="mp_propose_n_adjust",
        ))
    with col_sc:
        new_sc = st.checkbox("🍲 Include a slow-cooker meal",
                             value=include_sc, key="mp_propose_sc")
    st.caption(f"{sum(1 for m in meals if m.get('recipe_id'))} / {len(meals)} slots filled")

    if new_sc != include_sc:
        regen = _generate_pending(len(meals) or n,
                                  with_equipment_target(rules, "slow_cooker", new_sc),
                                  include_sc=new_sc)
        if regen is not None:
            regen["include_slow_cooker"] = new_sc
            kv_put(KEY_PENDING_LINEUP, regen)
        st.rerun()
    if new_n != len(meals):
        _resize_pending(pending, new_n, eff_rules, include_sc=include_sc)
        st.rerun()

    # Fetch the library once per render and index into it — library.get() is a
    # full KV round-trip, and this screen resolves the same recipes several
    # times (why-panel + reason chips + each slot card).
    lib = library.get_all()

    # "Why these picks?" — recompute lineup_meta from current recipes
    _render_why_panel(meals, eff_rules, lib)

    st.divider()

    # Slot cards — resolve the other recipes in the lineup for reason chips.
    lineup_recipes = [lib.get(m.get("recipe_id")) for m in meals if m.get("recipe_id")]
    lineup_recipes = [r for r in lineup_recipes if r]
    for slot in meals:
        _render_slot_card(slot, lineup_recipes, rules, lib)


# ---------------------------------------------------------------------------
# Generation / loading
# ---------------------------------------------------------------------------

def _sc_default(rules: dict) -> bool:
    """Default state of the per-plan slow-cooker toggle (household preference)."""
    return bool((rules.get("household") or {}).get("slow_cooker_default", True))


def _load_or_generate(rules: dict) -> dict | None:
    """Return the pending lineup dict, generating if entered from home fresh."""
    if st.session_state.pop("mealplan_propose_fresh", False):
        # Reset the per-plan controls so they re-seed for the new plan rather
        # than carrying values from a previous one.
        st.session_state.pop("mp_propose_n_adjust", None)
        st.session_state.pop("mp_propose_sc", None)
        n = int(st.session_state.pop("mealplan_propose_n", None)
                or (rules.get("household") or {}).get("meals_per_week_default") or 5)
        include_sc = bool(st.session_state.pop("mealplan_propose_include_sc",
                                               _sc_default(rules)))
        pending = _generate_pending(n, with_equipment_target(rules, "slow_cooker", include_sc),
                                    include_sc=include_sc)
        if pending is None:
            return None
        pending["include_slow_cooker"] = include_sc
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


def _resize_pending(pending: dict, new_n: int, rules: dict, include_sc: bool = True) -> None:
    """Grow or shrink the pending lineup to ``new_n`` slots in place, keeping
    the picks already made. Growing pulls additional fitting recipes; shrinking
    trims from the end. Persists to KV."""
    meals = pending.get("meals") or []
    cur = len(meals)
    if new_n < cur:
        meals = meals[:new_n]
    elif new_n > cur:
        existing = [library.get(m["recipe_id"]) for m in meals if m.get("recipe_id")]
        existing = [r for r in existing if r]
        history = kv_get(KEY_HISTORY, []) or []
        extra = extend_lineup(existing, new_n - cur, rules, _active_pool(include_sc),
                              history=history)
        meals = meals + [_slot_to_dict(cur + i, s) for i, s in enumerate(extra)]
    pending["n"] = len(meals)
    pending["meals"] = meals
    pending["updated_at"] = _now_iso()
    kv_put(KEY_PENDING_LINEUP, pending)


def _active_pool(include_sc: bool) -> list[dict]:
    """Active recipes for planning. When the slow-cooker option is off, drop
    slow-cooker recipes so 'don't include one' actually means none appear (not
    just 'no bonus'). Toggle ON keeps the full pool + the +score bias."""
    pool = library.all_active()
    if include_sc:
        return pool
    return [r for r in pool
            if "slow_cooker" not in {(e or "").lower() for e in (r.get("equipment") or [])}]


def _generate_pending(n: int, rules: dict, exclude_ids: set[str] | None = None,
                      include_sc: bool = True) -> dict | None:
    """Run the planner and wrap the result in a pending_lineup dict."""
    pool = _active_pool(include_sc)
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

    pending = {
        "n":          n,
        "meals":      [_slot_to_dict(i, s) for i, s in enumerate(result.slots)],
        "updated_at": _now_iso(),
    }
    # Telemetry — record the original proposal before user touches it.
    log_event(EVT_PLAN_PROPOSED, {
        "n":            n,
        "is_regenerate": bool(exclude_ids),
        "meals": [
            {"recipe_id": s.recipe.get("id"),
             "title":     s.recipe.get("title"),
             "score":     s.score,
             "added_via": s.added_via,
             "relaxation_level": s.relaxation_level,
             "reasons":   s.reasons,
             "cuisines":  s.recipe.get("cuisines") or [],
             "proteins":  s.recipe.get("proteins") or [],
             "carbs":     s.recipe.get("carbs") or []}
            for s in result.slots
        ],
    })
    return pending


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


def _reroll(n: int, rules: dict, current_meals: list[dict], include_sc: bool = True):
    prior_ids = {m.get("recipe_id") for m in current_meals if m.get("recipe_id")}
    with st.spinner("Rerolling lineup…"):
        new_pending = _generate_pending(n, rules, exclude_ids=prior_ids, include_sc=include_sc)
    if new_pending is not None:
        # Telemetry — separate from plan_proposed so summary can count
        # rerolls distinctly.
        log_event(EVT_PLAN_REGENERATED, {
            "prior_recipe_ids": sorted(rid for rid in prior_ids if rid),
            "new_recipe_ids":   [m["recipe_id"] for m in new_pending["meals"]
                                 if m.get("recipe_id")],
        })
        new_pending["include_slow_cooker"] = include_sc
        kv_put(KEY_PENDING_LINEUP, new_pending)
    st.rerun()


# ---------------------------------------------------------------------------
# Per-slot card
# ---------------------------------------------------------------------------

def _render_slot_card(slot: dict, lineup_recipes: list[dict], rules: dict, lib: dict):
    rid = slot.get("recipe_id")
    recipe = lib.get(rid) if rid else None

    slot_label = f"Slot {slot.get('slot', 0) + 1}"

    with st.container(border=True):
        col_body, col_actions = st.columns([5, 1])

        with col_body:
            if recipe:
                # Friendly reason chips (favorite / loved / new / balances…),
                # computed against the rest of the lineup.
                others = [r for r in lineup_recipes if r.get("id") != rid]
                chips = _recipe_view.recipe_reasons(recipe, others, rules)
                st.html(planner_card(
                    recipe=recipe,
                    label=slot_label,
                    title=recipe.get("title", "(untitled)"),
                    meta=_meta_line(recipe),
                    chips_html=reason_chips(chips) if chips else "",
                    favorite=_recipe_view.is_favorite(recipe, rules),
                ))
                # Raw scoring detail stays available but tucked away.
                if slot.get("reasons") or slot.get("relaxations"):
                    with st.expander("Scoring detail"):
                        for r in slot.get("reasons") or []:
                            st.caption(f"• {r}")
                        for r in (slot.get("relaxations") or []):
                            st.caption(f"⚙ {r}")
            else:
                st.html(planner_card(
                    recipe={}, label=slot_label, title="(empty)",
                    meta="Pick something via Replace →",
                ))

        with col_actions:
            if recipe and st.button("Preview", key=f"mp_propose_preview_{slot['slot']}",
                                    use_container_width=True):
                _recipe_view.open_preview(recipe, _recipe_view.compute_scale(recipe, rules))
            if st.button("Replace ⇄", key=f"mp_propose_replace_{slot['slot']}",
                         use_container_width=True):
                st.session_state.mealplan_swap_slot_index = slot["slot"]
                go("mealplan_swap")


# ---------------------------------------------------------------------------
# Why these picks? panel
# ---------------------------------------------------------------------------

def _render_why_panel(meals: list[dict], rules: dict, lib: dict):
    # Reconstruct a LineupResult-like object from the persisted slots so we
    # can call lineup_meta. We only need recipes + relaxation_level; the
    # other fields don't influence the meta aggregates.
    from mealplan.planner import LineupResult, SlotResult

    recipes = []
    slots: list[SlotResult] = []
    for m in meals:
        r = lib.get(m.get("recipe_id")) if m.get("recipe_id") else None
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

        for e in meta.get("equipment_covered", []):
            st.caption(f"🍲 {e.replace('_', ' ')} night included")
        for e in meta.get("equipment_missing", []):
            st.warning(f"No {e.replace('_', ' ')} meal in this plan")

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

def _confirm(meals: list[dict], rules: dict, include_sc: bool = True):
    lib = library.get_all()  # one fetch; reused for recipes + telemetry titles
    recipes = []
    for m in meals:
        r = lib.get(m.get("recipe_id")) if m.get("recipe_id") else None
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
        "include_slow_cooker":       include_sc,
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

    # Telemetry — record the confirmed final lineup. summarize_for_dietitian
    # correlates this with the most-recent plan_proposed by week.
    log_event(EVT_PLAN_CONFIRMED, {
        "week_number": week_number,
        "meals": [
            {"recipe_id": m["recipe_id"],
             "title":     (lib.get(m["recipe_id"]) or {}).get("title", ""),
             "added_via": m.get("added_via", "proposal")}
            for m in meals
        ],
    }, week=week_number)

    st.success(f"Plan confirmed for week #{week_number}. Opening this week's plan…")
    go("mealplan_active")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()
