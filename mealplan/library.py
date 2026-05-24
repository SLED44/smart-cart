"""Recipe library CRUD over the existing Supabase `kv` table.

Public surface (Phase 1):
    - get(recipe_id) -> Recipe | None
    - save(recipe) -> str  (returns recipe_id; idempotent on Spoonacular ID)
    - delete(recipe_id) -> None
    - all_active() -> list[Recipe]
    - filter(cuisine=None, protein=None, status=None, name_search=None) -> list[Recipe]
    - data_summary() -> dict  (counts by status, cuisine, protein)

Backed by the `recipe_library` KV key (single object: recipe_id → Recipe).

See MEAL_PLAN_PRD.md §7.2 for the Recipe schema.
"""
