"""
mealplan/planner.py
-------------------
Weekly meal lineup generator.

Public interface:
    generate_lineup(n, rules, library_iter, history=None, exclude_ids=None)
        -> LineupResult
    regenerate_lineup(n, rules, library_iter, history, prior_lineup)
        -> LineupResult
    lineup_meta(lineup, rules) -> dict   # for the "Why these picks?" panel

Pure Python. Reads from the rules engine ([[mealplan_rules]]) and a
library iterator (typically ``mealplan.library.all_active()``); never
touches Spoonacular. The swap-time Spoonacular fallback lives in
``mealplan.swap`` (Phase 6).

Algorithm per PRD §9:
    Greedy slot-fill. For each slot, evaluate every active library recipe
    against the current partial lineup, pick the highest-scoring eligible
    candidate. If no candidate is eligible, escalate the relaxation level
    and retry. Raises NoCandidatesError after Level 4 still finds nothing.
"""

from dataclasses import dataclass, field

from mealplan.rules import (
    MAX_RELAXATION_LEVEL,
    evaluate_candidate,
    relaxation_label,
)

# In-memory penalty applied to recipes appearing in `exclude_ids` (used by
# regenerate_lineup so a whole-plan reroll doesn't return the same lineup).
_REGENERATE_PENALTY = -40


# ---------------------------------------------------------------------------
# Result types
# ---------------------------------------------------------------------------

@dataclass
class SlotResult:
    recipe: dict
    score: float
    relaxation_level: int
    reasons: list[str] = field(default_factory=list)
    relaxations_applied: list[str] = field(default_factory=list)
    added_via: str = "proposal"


@dataclass
class LineupResult:
    slots: list[SlotResult] = field(default_factory=list)

    @property
    def recipes(self) -> list[dict]:
        return [s.recipe for s in self.slots]

    @property
    def relaxations_used(self) -> list[int]:
        return [s.relaxation_level for s in self.slots]

    def __len__(self) -> int:
        return len(self.slots)

    def __iter__(self):
        return iter(self.slots)


class NoCandidatesError(RuntimeError):
    """Raised when even Level 4 relaxation produces no eligible candidate."""
    def __init__(self, slot_index: int, partial: LineupResult):
        super().__init__(f"no candidate for slot {slot_index} even at max relaxation")
        self.slot_index = slot_index
        self.partial = partial


# ---------------------------------------------------------------------------
# Generation
# ---------------------------------------------------------------------------

def generate_lineup(
    n: int,
    rules: dict,
    library: list[dict],
    history: list[dict] | None = None,
    exclude_ids: set[str] | None = None,
) -> LineupResult:
    """
    Build an N-recipe lineup greedily from ``library``.

    Args:
        n            number of recipes to plan (typically 1..7)
        rules        as returned by mealplan.rules.load_rules()
        library      iterable of Recipe dicts to consider (PRD §7.2 schema).
                     Caller filters status (e.g. library.all_active()).
        history      list[Plan] for the recent-history penalty
        exclude_ids  recipe ids to penalise heavily (regenerate path)

    Returns LineupResult with one SlotResult per slot.

    Raises:
        NoCandidatesError when even Level 4 yields nothing for a given slot.
        ValueError on bad inputs.
    """
    if n < 1:
        raise ValueError("n must be >= 1")
    history = history or []
    exclude_ids = set(exclude_ids or [])

    result = LineupResult()
    pool = list(library)

    for slot_index in range(n):
        chosen, level = _pick_for_slot(
            pool=pool,
            already_chosen=[s.recipe for s in result.slots],
            rules=rules,
            history=history,
            exclude_ids=exclude_ids,
        )
        if chosen is None:
            raise NoCandidatesError(slot_index=slot_index, partial=result)

        evaluation, score, reasons, relaxations = chosen
        added_via = _classify_added_via(evaluation, rules)
        result.slots.append(SlotResult(
            recipe=evaluation,
            score=score,
            relaxation_level=level,
            reasons=reasons,
            relaxations_applied=relaxations,
            added_via=added_via,
        ))

    return result


def regenerate_lineup(
    n: int,
    rules: dict,
    library: list[dict],
    history: list[dict] | None,
    prior_lineup: list[dict],
) -> LineupResult:
    """
    Whole-plan reroll. Penalises every recipe in ``prior_lineup`` so the
    reroll is unlikely to return the same set. The penalty is in-memory
    only — never persisted (PRD §9.3).
    """
    prior_ids = {r.get("id") for r in (prior_lineup or []) if r.get("id")}
    return generate_lineup(n, rules, library, history=history, exclude_ids=prior_ids)


# ---------------------------------------------------------------------------
# Per-slot selection
# ---------------------------------------------------------------------------

def _pick_for_slot(
    pool: list[dict],
    already_chosen: list[dict],
    rules: dict,
    history: list[dict],
    exclude_ids: set[str],
) -> tuple[tuple[dict, float, list[str], list[str]] | None, int]:
    """
    Try relaxation level 0, then 1, ... up to MAX_RELAXATION_LEVEL. Returns
    (chosen, level) where chosen is (recipe, score, reasons, relaxations).
    """
    used_ids = {r.get("id") for r in already_chosen}
    for level in range(MAX_RELAXATION_LEVEL + 1):
        candidates: list[tuple[float, dict, list[str], list[str]]] = []
        for recipe in pool:
            rid = recipe.get("id")
            if rid in used_ids:
                continue
            ev = evaluate_candidate(recipe, rules, already_chosen, history, level)
            if not ev.eligible:
                continue
            score = ev.score
            if rid in exclude_ids:
                score += _REGENERATE_PENALTY
            candidates.append((score, recipe, ev.reasons, ev.relaxations_applied))

        if not candidates:
            continue

        # Sort by score DESC, tie-break by least-recently-cooked (lex on
        # ISO date; None / "" => "0000" sorts first => oldest).
        candidates.sort(
            key=lambda c: (-c[0], c[1].get("last_cooked_at") or "0000")
        )
        top_score, top_recipe, top_reasons, top_relax = candidates[0]
        # Annotate which level was used so the UI can surface it.
        relax_notes = list(top_relax)
        if level > 0:
            relax_notes.append(f"relaxation level {level}: {relaxation_label(level)}")
        return (top_recipe, top_score, top_reasons, relax_notes), level

    return None, MAX_RELAXATION_LEVEL


def _classify_added_via(recipe: dict, rules: dict) -> str:
    favorites = {f.get("recipe_id") for f in (rules.get("favorites") or [])}
    if recipe.get("id") in favorites:
        return "favorite"
    return "proposal"


# ---------------------------------------------------------------------------
# Meta — feeds the "Why these picks?" UI
# ---------------------------------------------------------------------------

def lineup_meta(result: LineupResult, rules: dict) -> dict:
    """
    Aggregate counters and notes for the "Why these picks?" expander.

    Returns:
        {
            "protein_counts":     {"chicken": 2, "beef": 1, ...},
            "cuisine_counts":     {"american": 1, ...},
            "carb_counts":        {"rice": 2, ...},
            "must_include_covered": ["american"],
            "must_include_missing": ["italian", "mexican"],
            "shrimp_status":      {"weeks_since": int|None, "cadence": int},
            "favorites_status":   [{"recipe_id": str, "due": bool, "force": bool, ...}],
            "relaxations_used":   [int] (one per slot),
            "relaxation_summary": str (human-readable),
        }
    """
    proteins, cuisines, carbs = {}, {}, {}
    for r in result.recipes:
        for p in (r.get("proteins") or []):
            proteins[p.lower()] = proteins.get(p.lower(), 0) + 1
        for c in (r.get("cuisines") or []):
            cuisines[c.lower()] = cuisines.get(c.lower(), 0) + 1
        for cb in (r.get("carbs") or []):
            carbs[cb.lower()] = carbs.get(cb.lower(), 0) + 1

    must = set((rules.get("cuisines") or {}).get("must_include_one_of_per_week") or [])
    covered = sorted(must & set(cuisines.keys()))
    missing = sorted(must - set(cuisines.keys()))

    cw = int(((rules.get("state") or {}).get("current_week")) or 1)
    shrimp_status = None
    for entry in (rules.get("protein_cadences") or []):
        if (entry.get("protein") or "").lower() == "shrimp":
            lu = entry.get("last_used_week")
            shrimp_status = {
                "weeks_since": (cw - int(lu)) if isinstance(lu, int) else None,
                "cadence":     int(entry.get("cadence_weeks") or 0),
            }
            break

    favorites_status = []
    for fav in (rules.get("favorites") or []):
        cadence = fav.get("cadence_weeks") or []
        lu = fav.get("last_used_week")
        gap = (cw - int(lu)) if isinstance(lu, int) else None
        favorites_status.append({
            "recipe_id":     fav["recipe_id"],
            "last_used_week": lu,
            "gap":           gap,
            "due":           bool(cadence and gap is not None and gap >= int(cadence[0])),
            "force":         bool(len(cadence) >= 2 and gap is not None and gap >= int(cadence[1])),
            "ever_used":     lu is not None,
        })

    relax_levels = result.relaxations_used
    max_level = max(relax_levels) if relax_levels else 0
    relaxation_summary = (
        "no relaxation needed"
        if max_level == 0
        else f"max level {max_level}: {relaxation_label(max_level)}"
    )

    return {
        "protein_counts":      proteins,
        "cuisine_counts":      cuisines,
        "carb_counts":         carbs,
        "must_include_covered": covered,
        "must_include_missing": missing,
        "shrimp_status":       shrimp_status,
        "favorites_status":    favorites_status,
        "relaxations_used":    relax_levels,
        "relaxation_summary":  relaxation_summary,
    }
