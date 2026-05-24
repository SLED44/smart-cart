"""Rules engine — evaluates candidate recipes against the user's ruleset.

Public surface (to be implemented in Phase 2):
    - evaluate_candidate(recipe, rules, current_lineup, history, relaxation_level=0) -> Evaluation
    - validate_rules(rules) -> list[str]  (server-side validation errors)

See MEAL_PLAN_PRD.md §8 for the full spec, including the hard/soft rules
breakdown and the relaxation order (locked to option A — provisional).
"""
