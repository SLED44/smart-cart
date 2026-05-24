"""One-shot Spoonacular bootstrap to seed the recipe library (~110 recipes).

Public surface (Phase 4):
    - estimated_cost(config) -> int  (Spoonacular points)
    - run_bootstrap(config, progress_callback) -> BootstrapResult

Steps (PRD §15.1):
    1. Six fan favorites — query by title, user picks canonical version
    2. Cuisine sweep — 13 cuisines × 7 recipes each
    3. Protein gap-fill — under-represented proteins
    4. Spice filter post-pass — flag spicy titles for user confirmation

Idempotent — re-runs add to the library, never overwrite recipes with
times_cooked > 0 or non-empty user_notes.

See MEAL_PLAN_PRD.md §15 for the full spec.
"""
