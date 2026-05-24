"""Spoonacular API client.

Public surface (Phase 1):
    - search(cuisine=None, protein=None, max_ready=60, mild=True, number=5, ...) -> list[Recipe]
    - get_recipe(spoonacular_id) -> Recipe
    - points_used_today() -> int  (rate-limit awareness for free tier)

Endpoints used (see PRD §14.1):
    GET /recipes/complexSearch
    GET /recipes/{id}/information

API key resolved from os.getenv("SPOONACULAR_API_KEY").

See MEAL_PLAN_PRD.md §14 for the full spec.
"""
