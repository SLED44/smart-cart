"""Grocery list aggregator — collapses N recipes' ingredients into a SmartCart-ready list.

Public surface (Phase 8):
    - aggregate_grocery_list(recipe_ids, household_size, library) -> list[Item]
    - AISLE_MAP  (Spoonacular aisle → SmartCart category, see PRD §12.3)

V1: only sums quantities when units match exactly. No unit conversion.

See MEAL_PLAN_PRD.md §12 for the full spec.
"""
