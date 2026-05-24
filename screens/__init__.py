"""SmartCart screens package.

Each screen module exposes a `render()` function. The router in main.py
dispatches based on `st.session_state.screen`.

Meal-plan screens are intentionally not imported by main.py's router yet —
the modules exist as Phase 0 scaffolding for the meal-plan feature build
(see MEAL_PLAN_PRD.md §22 for the phasing).
"""

from screens import (
    connect_kroger,
    home,
    item_filter,
    login,
    preferences,
    preview,
    review,
    sale_scan,
    staples,
    store_setup,
    summary,
)

# Meal-plan screens — scaffolded but not wired into the router yet.
from screens import (  # noqa: F401
    mealplan_active,
    mealplan_bootstrap,
    mealplan_cook,
    mealplan_home,
    mealplan_library,
    mealplan_paste_recipe,
    mealplan_propose,
    mealplan_rules,
    mealplan_state_import,
    mealplan_swap,
)

__all__ = [
    "connect_kroger",
    "home",
    "item_filter",
    "login",
    "preferences",
    "preview",
    "review",
    "sale_scan",
    "staples",
    "store_setup",
    "summary",
]
