"""Lineup planner — generates N recipe lineups respecting the rules engine.

Public surface (Phase 2):
    - generate_lineup(N, rules, library, history) -> list[Recipe]
    - regenerate_lineup(N, rules, library, history, prior_lineup) -> list[Recipe]
      (whole-plan "Give me 5 new options"; penalises recently-shown recipes)

See MEAL_PLAN_PRD.md §9 for the full algorithm.
"""
