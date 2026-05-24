"""Meal-planner package.

Pure-Python feature that proposes weekly meal lineups from a self-hosted
recipe library, then hands off the aggregated grocery list to SmartCart's
existing preview → match → cart pipeline.

This package does NOT call the Anthropic API — the planner is deterministic.
Recipes from outside Spoonacular are normalized externally (in Claude.ai
chat) and pasted into the app as JSON.

See MEAL_PLAN_PRD.md for the full spec.
"""
