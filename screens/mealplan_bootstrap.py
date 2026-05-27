"""
Meal-plan bootstrap screen — one-shot Spoonacular pull (~115 points).

State machine via ``st.session_state.mealplan_bootstrap_stage``:
    config         → form + estimate + Start
    favorites_pick → 3 candidates per favorite, user picks or skips
    running        → cuisine sweep + protein gap fill (spinner)
    spice_review   → flagged titles, user picks which to demote
    done           → summary stats

Re-runnable. library.save() dedupes on Spoonacular source_id and protects
times_cooked / user_notes / status fields on existing entries.
"""

import streamlit as st

from mealplan import bootstrap, library, spoonacular
from mealplan.event_log import EVT_BOOTSTRAP_COMPLETED, log_event
from mealplan.rules import default_rules, load_rules

from screens._shared import go

_STAGE_KEY = "mealplan_bootstrap_stage"
_CONFIG_KEY = "mealplan_bootstrap_config"
_FAV_CANDIDATES_KEY = "mealplan_bootstrap_fav_candidates"
_FAV_PICKS_KEY = "mealplan_bootstrap_fav_picks"  # slug -> chosen idx or "skip"
_SWEEP_RESULTS_KEY = "mealplan_bootstrap_sweep_results"
_GAP_RESULTS_KEY = "mealplan_bootstrap_gap_results"
_SPICE_FLAGS_KEY = "mealplan_bootstrap_spice_flags"
_SPICE_DEMOTED_KEY = "mealplan_bootstrap_spice_demoted"
_LIB_SIZE_BEFORE_KEY = "mealplan_bootstrap_lib_size_before"
_POINTS_BEFORE_KEY = "mealplan_bootstrap_points_before"


def render():
    st.title("🌱 Bootstrap Recipe Library")
    st.caption("One-time Spoonacular pull to seed ~110 recipes. Re-runnable; "
               "won't clobber recipes you've already cooked or annotated.")

    col_back, col_reset = st.columns([1, 1])
    with col_back:
        if st.button("← Home", key="bs_back"):
            go("home")
    with col_reset:
        if st.button("↻ Start over", key="bs_reset"):
            _reset()
            st.rerun()

    st.divider()

    stage = st.session_state.get(_STAGE_KEY, "config")
    if stage == "config":
        _render_config()
    elif stage == "favorites_pick":
        _render_favorites_pick()
    elif stage == "running":
        _render_running()
    elif stage == "spice_review":
        _render_spice_review()
    elif stage == "done":
        _render_done()
    else:
        st.error(f"Unknown stage {stage!r}; resetting.")
        _reset()
        st.rerun()


def _reset():
    for key in (_STAGE_KEY, _CONFIG_KEY, _FAV_CANDIDATES_KEY, _FAV_PICKS_KEY,
                _SWEEP_RESULTS_KEY, _GAP_RESULTS_KEY, _SPICE_FLAGS_KEY,
                _SPICE_DEMOTED_KEY):
        st.session_state.pop(key, None)


# ---------------------------------------------------------------------------
# Stage: config
# ---------------------------------------------------------------------------

def _render_config():
    st.subheader("Configure the pull")
    rules = load_rules()
    default_cuisines = (rules.get("cuisines") or {}).get("rotation_set") \
        or default_rules()["cuisines"]["rotation_set"]

    # Tunable parameters
    cuisines = st.multiselect(
        "Cuisines to sweep",
        options=default_cuisines,
        default=default_cuisines,
        key="bs_cuisines",
    )
    col1, col2, col3 = st.columns(3)
    with col1:
        per_cuisine = int(st.number_input(
            "Recipes per cuisine", min_value=1, max_value=20,
            value=bootstrap.DEFAULT_PER_CUISINE, step=1,
            key="bs_per_cuisine"))
    with col2:
        max_ready = int(st.number_input(
            "Max ready time (minutes)", min_value=15, max_value=180, value=60, step=5,
            key="bs_max_ready"))
    with col3:
        gap_threshold = int(st.number_input(
            "Protein gap threshold", min_value=1, max_value=20, value=5, step=1,
            key="bs_gap"))

    mild_only = st.checkbox(
        "Filter spicy titles during fetch (mild_only)",
        value=True, key="bs_mild")

    st.markdown("**Favorites to seed** (uncheck any you'd rather skip):")
    favs_to_pick = []
    cols = st.columns(2)
    for i, (slug, queries) in enumerate(bootstrap.FAVORITES):
        with cols[i % 2]:
            label = queries[0].title()
            if st.checkbox(label, value=True, key=f"bs_fav_{slug}"):
                favs_to_pick.append(slug)

    config = bootstrap.BootstrapConfig(
        cuisines=cuisines,
        per_cuisine=per_cuisine,
        max_ready=max_ready,
        protein_gap_threshold=gap_threshold,
        mild_only=mild_only,
        favorites_to_pick=favs_to_pick,
    )

    # Cost estimate
    estimate = bootstrap.estimated_cost(config)
    remaining = spoonacular.points_remaining_today()
    st.divider()
    col_est, col_rem = st.columns(2)
    with col_est:
        st.metric("Estimated cost", f"{estimate} points")
    with col_rem:
        st.metric("Points remaining today", f"{remaining} / {spoonacular.DAILY_POINT_CAP}")

    if estimate > remaining:
        st.error(f"Not enough daily budget. Bootstrap needs ~{estimate} points; "
                 f"only {remaining} remain. Wait until UTC midnight, trim the "
                 f"cuisine list, or reduce per-cuisine count.")
        disabled = True
    else:
        st.info(f"After bootstrap: ~{remaining - estimate} points headroom.")
        disabled = False

    if st.button("Start bootstrap →", type="primary",
                 disabled=disabled, use_container_width=True, key="bs_start"):
        st.session_state[_CONFIG_KEY] = config
        # Pre-fetch favorite candidates here so the next screen has data.
        with st.spinner(f"Fetching {len(favs_to_pick)} favorite candidate sets…"):
            results: list[bootstrap.FavoriteCandidate] = []
            for slug in favs_to_pick:
                queries = next(
                    (q for s, q in bootstrap.FAVORITES if s == slug),
                    [slug.replace("_", " ")],
                )
                try:
                    results.append(
                        bootstrap.find_favorite_candidates(slug, queries, mild=mild_only))
                except Exception as e:
                    st.warning(f"Failed to query '{queries[0]}': {e}")
                    results.append(bootstrap.FavoriteCandidate(
                        slug=slug, title_query=queries[0], candidates=[]))
        st.session_state[_FAV_CANDIDATES_KEY] = results
        st.session_state[_FAV_PICKS_KEY] = {}
        st.session_state[_STAGE_KEY] = "favorites_pick" if favs_to_pick else "running"
        st.rerun()


# ---------------------------------------------------------------------------
# Stage: favorites_pick
# ---------------------------------------------------------------------------

def _render_favorites_pick():
    st.subheader("Pick the canonical version of each favorite")
    st.caption("These become your seed favorites (cadence [4, 6]). Pick one per row "
               "or choose Skip if none look right — you can paste-import later.")

    candidates: list[bootstrap.FavoriteCandidate] = \
        st.session_state.get(_FAV_CANDIDATES_KEY) or []
    picks: dict = st.session_state.setdefault(_FAV_PICKS_KEY, {})

    for fc in candidates:
        st.divider()
        display = bootstrap.BootstrapConfig.display_name(fc.slug)
        st.markdown(f"### {display}")
        # Surface when Spoonacular's literal-substring matching forced us to
        # broaden the query — helps the user understand why "Burger Bites"
        # showed up instead of a smash burger.
        if fc.title_query.lower() != display.lower():
            st.caption(f"_(no Spoonacular results for '{display}' — broadened to "
                       f"'{fc.title_query}')_")
        if not fc.candidates:
            st.warning(
                "No candidates from any fallback query. "
                "Skipping — use **📝 Paste a recipe** later to import this one "
                "from a Claude.ai chat."
            )
            picks[fc.slug] = "skip"
            continue

        # Build a radio with N+1 options (N candidates + Skip).
        options = list(range(len(fc.candidates))) + ["skip"]

        def _fmt(opt, fc=fc):
            if opt == "skip":
                return "↪ Skip this favorite"
            r = fc.candidates[opt]
            return f"{r.get('title','(untitled)')} · {r.get('ready_in_minutes','?')} min"

        current = picks.get(fc.slug, 0)
        if current not in options:
            current = 0
        idx = st.radio(
            "choose",
            options,
            format_func=_fmt,
            key=f"bs_favpick_{fc.slug}",
            index=options.index(current),
            label_visibility="collapsed",
        )
        picks[fc.slug] = idx
        # Preview the highlighted recipe.
        if idx != "skip":
            r = fc.candidates[idx]
            cols = st.columns([1, 3])
            with cols[0]:
                if r.get("image_url"):
                    st.image(r["image_url"], width=180)
            with cols[1]:
                st.caption(", ".join(r.get("cuisines", [])) or "(no cuisine tag)")
                st.caption(f"Proteins: {', '.join(r.get('proteins') or []) or '—'} · "
                           f"Carbs: {', '.join(r.get('carbs') or []) or '—'}")
                st.caption(f"Equipment: {', '.join(r.get('equipment') or []) or '—'}")
                if r.get("source_url"):
                    st.markdown(f"[Source ↗]({r['source_url']})")

    st.session_state[_FAV_PICKS_KEY] = picks

    st.divider()
    if st.button("Confirm picks → Cuisine sweep", type="primary",
                 use_container_width=True, key="bs_fav_confirm"):
        saved = 0
        skipped = 0
        for fc in candidates:
            choice = picks.get(fc.slug, 0)
            if choice == "skip" or not fc.candidates:
                skipped += 1
                continue
            try:
                bootstrap.save_favorite_pick(fc.slug, fc.candidates[choice])
                saved += 1
            except Exception as e:
                st.error(f"Couldn't save {fc.title_query}: {e}")
        st.success(f"Saved {saved} favorite(s); skipped {skipped}.")
        st.session_state[_STAGE_KEY] = "running"
        st.rerun()


# ---------------------------------------------------------------------------
# Stage: running (cuisine sweep + gap fill)
# ---------------------------------------------------------------------------

def _render_running():
    config: bootstrap.BootstrapConfig = st.session_state.get(_CONFIG_KEY)
    if not config:
        st.error("Missing config; resetting.")
        _reset()
        st.rerun()
        return

    # Run if not yet done.
    if _SWEEP_RESULTS_KEY not in st.session_state:
        # Snapshot before-counts so the bootstrap_completed event can
        # report new_recipes_added + points_used accurately.
        st.session_state[_LIB_SIZE_BEFORE_KEY] = library.data_summary()["total"]
        st.session_state[_POINTS_BEFORE_KEY] = spoonacular.points_used_today()
        st.subheader("Cuisine sweep + protein gap-fill")
        st.caption(f"Pulling {len(config.cuisines)} cuisines × {config.per_cuisine} recipes "
                   f"each. This takes ~{len(config.cuisines) * 2}s.")
        with st.spinner("Sweeping…"):
            results = bootstrap.run_cuisine_sweep(config)
        st.session_state[_SWEEP_RESULTS_KEY] = results
        with st.spinner("Filling protein gaps…"):
            gap_results = bootstrap.run_protein_gap_fill(config)
        st.session_state[_GAP_RESULTS_KEY] = gap_results
        with st.spinner("Scanning for spicy titles…"):
            flags = bootstrap.scan_spicy_titles()
        st.session_state[_SPICE_FLAGS_KEY] = flags
        # Move on automatically — spice review is its own stage.
        st.session_state[_STAGE_KEY] = "spice_review"
        st.rerun()
        return

    # Should not normally render — handled above.
    st.info("Running…")


# ---------------------------------------------------------------------------
# Stage: spice_review
# ---------------------------------------------------------------------------

def _render_spice_review():
    flags: list[bootstrap.SpiceFlag] = st.session_state.get(_SPICE_FLAGS_KEY) or []
    sweep = st.session_state.get(_SWEEP_RESULTS_KEY) or []
    gap = st.session_state.get(_GAP_RESULTS_KEY) or {}

    st.subheader("Sweep summary")
    total_saved = sum(len(r.saved_ids) for r in sweep) + sum(len(v) for v in gap.values())
    st.write(f"**{total_saved}** recipes saved across {len(sweep)} cuisines + "
             f"{len(gap)} protein gap-fills.")
    errs = [r for r in sweep if r.error]
    if errs:
        with st.expander(f"⚠ {len(errs)} cuisine(s) errored"):
            for r in errs:
                st.write(f"• **{r.cuisine}** — {r.error}")

    st.divider()
    st.subheader("Spice scan")
    if not flags:
        st.success("No spicy titles found. Nothing to review.")
        if st.button("Finish →", type="primary", use_container_width=True,
                     key="bs_spice_skip"):
            st.session_state[_STAGE_KEY] = "done"
            st.rerun()
        return

    st.caption(f"Found {len(flags)} recipe(s) with a spicy keyword in the title. "
               f"Your household preference is **mild** by default — check any "
               f"you'd like demoted to `never_again` (also adds to exclusions).")

    chosen: list[str] = []
    for fl in flags:
        col_cb, col_kw = st.columns([5, 1])
        with col_cb:
            on = st.checkbox(
                f"**{fl.title}**  `({fl.recipe_id})`",
                value=True, key=f"bs_spice_{fl.recipe_id}")
            if on:
                chosen.append(fl.recipe_id)
        with col_kw:
            st.caption(f"matched: `{fl.keyword}`")

    st.divider()
    if st.button("Apply demotions → Finish", type="primary",
                 use_container_width=True, key="bs_spice_apply"):
        count = bootstrap.demote_spicy(chosen)
        st.session_state[_SPICE_DEMOTED_KEY] = count
        st.session_state[_STAGE_KEY] = "done"
        st.rerun()


# ---------------------------------------------------------------------------
# Stage: done
# ---------------------------------------------------------------------------

def _render_done():
    summary = library.data_summary()
    sweep = st.session_state.get(_SWEEP_RESULTS_KEY) or []
    gap = st.session_state.get(_GAP_RESULTS_KEY) or {}
    demoted = int(st.session_state.get(_SPICE_DEMOTED_KEY) or 0)
    points_used = spoonacular.points_used_today()

    # Fire the bootstrap_completed event once on first render of done.
    # _LIB_SIZE_BEFORE_KEY is set in running stage; if missing we skip
    # the event (means user landed here via reset/back).
    if _LIB_SIZE_BEFORE_KEY in st.session_state:
        lib_before = int(st.session_state.pop(_LIB_SIZE_BEFORE_KEY))
        points_before = int(st.session_state.pop(_POINTS_BEFORE_KEY, 0) or 0)
        config = st.session_state.get(_CONFIG_KEY)
        log_event(EVT_BOOTSTRAP_COMPLETED, {
            "cuisines_swept":     list(config.cuisines) if config else [],
            "per_cuisine":        int(config.per_cuisine) if config else 0,
            "max_ready":          int(config.max_ready) if config else 0,
            "favorites_picked":   sum(1 for r in (st.session_state.get(_FAV_CANDIDATES_KEY) or [])
                                      if (st.session_state.get(_FAV_PICKS_KEY) or {}).get(r.slug) not in (None, "skip")),
            "new_recipes_added":  summary["total"] - lib_before,
            "points_used":        points_used - points_before,
            "spicy_demoted":      demoted,
            "library_size_after": summary["total"],
        })

    st.subheader("✅ Bootstrap complete")

    col_a, col_b, col_c, col_d = st.columns(4)
    with col_a:
        st.metric("Library size", summary["total"])
    with col_b:
        st.metric("Favorites", summary["by_status"].get("favorite", 0))
    with col_c:
        st.metric("Demoted (spice)", demoted)
    with col_d:
        st.metric("Points used today", f"{points_used} / {spoonacular.DAILY_POINT_CAP}")

    st.divider()
    st.markdown("**By cuisine** (top 8)")
    by_cuisine = summary.get("by_cuisine") or {}
    if by_cuisine:
        cols = st.columns(4)
        for i, (cuisine, n) in enumerate(list(by_cuisine.items())[:8]):
            with cols[i % 4]:
                st.metric(cuisine, n)

    st.markdown("**By protein**")
    by_protein = summary.get("by_protein") or {}
    if by_protein:
        cols = st.columns(min(7, len(by_protein)))
        for i, (p, n) in enumerate(by_protein.items()):
            with cols[i % len(cols)]:
                st.metric(p, n)

    if sweep:
        with st.expander("Per-cuisine detail"):
            for r in sweep:
                st.write(
                    f"**{r.cuisine}** — saved {len(r.saved_ids)} recipe(s)"
                    + (f"  ⚠ {r.error}" if r.error else "")
                )
    if gap:
        with st.expander("Protein gap-fills"):
            for protein, ids in gap.items():
                st.write(f"**{protein}** — added {len(ids)} recipe(s)")

    st.divider()
    col_home, col_rules = st.columns(2)
    with col_home:
        if st.button("← Back to home", use_container_width=True, key="bs_done_home"):
            _reset()
            go("home")
    with col_rules:
        if st.button("⚙ Tweak rules / favorites", use_container_width=True,
                     key="bs_done_rules"):
            _reset()
            go("mealplan_rules")
