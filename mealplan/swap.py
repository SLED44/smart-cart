"""
mealplan/swap.py
----------------
Per-slot swap candidate picker for the propose screen.

Library-first; falls back to a one-shot Spoonacular fetch when the library
has fewer than 5 eligible candidates AND the user supplied both a cuisine
and a protein filter. Spoonacular results are saved to the library on
first view (cache once, free forever).

See PRD §10 for the full spec.
"""

from dataclasses import dataclass, field

from mealplan import library, spoonacular
from mealplan.rules import evaluate_candidate

TARGET_N = 5
SPOONACULAR_FETCH_N = 8  # over-fetch a bit so rules evaluation can still hit TARGET_N


@dataclass
class SwapCandidate:
    recipe: dict
    score: float
    reasons: list[str] = field(default_factory=list)
    relaxations_applied: list[str] = field(default_factory=list)
    source: str = "library"  # "library" or "spoonacular"


@dataclass
class SwapResult:
    candidates: list[SwapCandidate] = field(default_factory=list)
    spoonacular_attempted: bool = False
    spoonacular_error: str = ""
    note: str = ""


def get_swap_candidates(
    slot_index: int,
    current_lineup: list[dict],
    rules: dict,
    history: list[dict] | None = None,
    cuisine: str | None = None,
    protein: str | None = None,
    name_search: str | None = None,
    seen_ids: set[str] | None = None,
    n: int = TARGET_N,
) -> SwapResult:
    """
    Return up to ``n`` ranked swap candidates for ``slot_index``.

    The lineup minus that slot is used for rules evaluation (so removing
    a chicken dish makes other chicken dishes eligible again under the
    variety penalty math).
    """
    history = history or []
    seen_ids = set(seen_ids or [])

    eval_lineup = [r for i, r in enumerate(current_lineup) if i != slot_index]
    excluded_ids = {r.get("id") for r in current_lineup if r.get("id")}

    # Step 1 — library-side filter
    pool = library.filter(cuisine=cuisine, protein=protein, name_search=name_search)
    pool = [r for r in pool
            if r.get("id") not in seen_ids and r.get("id") not in excluded_ids]

    candidates = _evaluate_pool(pool, rules, eval_lineup, history, source="library")
    result = SwapResult(candidates=candidates)

    if len(candidates) >= n:
        return _top_n(result, n)

    # Step 2 — Spoonacular fallback (requires BOTH cuisine and protein per PRD §10.2)
    if cuisine and protein:
        result.spoonacular_attempted = True
        try:
            fresh = spoonacular.search(
                cuisine=cuisine,
                protein=protein,
                max_ready=60,
                mild=True,
                sort="popularity",
                number=SPOONACULAR_FETCH_N,
            )
        except Exception as e:
            result.spoonacular_error = str(e)
            return _top_n(result, n)

        # Cache to library + re-fetch any normalised ids
        new_ids: list[str] = []
        for r in fresh:
            rid = library.save(r)
            new_ids.append(rid)

        # Pull the freshly-saved entries back via library.get and re-evaluate.
        freshly_saved = [
            library.get(rid) for rid in new_ids
            if rid not in seen_ids and rid not in excluded_ids
        ]
        freshly_saved = [r for r in freshly_saved if r]

        # De-dup against library hits we already evaluated.
        already = {c.recipe.get("id") for c in candidates}
        new_pool = [r for r in freshly_saved if r.get("id") not in already]

        new_evals = _evaluate_pool(
            new_pool, rules, eval_lineup, history, source="spoonacular"
        )
        result.candidates.extend(new_evals)
    else:
        if name_search and not cuisine and not protein:
            result.note = ("No library matches. Add a cuisine + protein filter to "
                           "let me hit Spoonacular for fresh ideas.")
        elif not (cuisine or protein or name_search):
            result.note = "Library exhausted. Pick a cuisine + protein for Spoonacular fallback."

    return _top_n(result, n)


def mark_never_again(recipe_id: str, rules: dict) -> dict:
    """
    Demote a recipe to never_again AND add it to rules.exclusions.
    Returns the mutated rules dict (caller persists via save_rules).
    """
    library.set_status(recipe_id, "never_again")
    excl = set(rules.get("exclusions") or [])
    excl.add(recipe_id)
    rules["exclusions"] = sorted(excl)
    return rules


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _evaluate_pool(
    pool: list[dict],
    rules: dict,
    eval_lineup: list[dict],
    history: list[dict],
    source: str,
) -> list[SwapCandidate]:
    out: list[SwapCandidate] = []
    for recipe in pool:
        ev = evaluate_candidate(recipe, rules, eval_lineup, history, relaxation_level=0)
        if not ev.eligible:
            continue
        out.append(SwapCandidate(
            recipe=recipe,
            score=ev.score,
            reasons=ev.reasons,
            relaxations_applied=ev.relaxations_applied,
            source=source,
        ))
    return out


def _top_n(result: SwapResult, n: int) -> SwapResult:
    """Sort by score desc, break ties by least-recently-cooked, cap at n."""
    result.candidates.sort(
        key=lambda c: (-c.score, c.recipe.get("last_cooked_at") or "0000")
    )
    result.candidates = result.candidates[:n]
    return result
