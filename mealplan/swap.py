"""Per-slot swap candidate picker.

Public surface (Phase 6):
    - get_swap_candidates(slot_index, cuisine, protein, name_search, seen_ids) -> list[Recipe]

Library-first; falls back to a Spoonacular query when the library has
fewer than 5 eligible candidates for a given cuisine+protein filter.

See MEAL_PLAN_PRD.md §10 for the full spec.
"""
