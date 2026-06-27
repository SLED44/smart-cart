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
from mealplan.rules import MAX_RELAXATION_LEVEL, evaluate_candidate, relaxation_label

TARGET_N = 5
MIN_MATCH = 3            # always surface at least this many matches for the slot
SPOONACULAR_FETCH_N = 8  # over-fetch a bit so rules evaluation can still hit TARGET_N


@dataclass
class SwapCandidate:
    recipe: dict
    score: float
    reasons: list[str] = field(default_factory=list)
    relaxations_applied: list[str] = field(default_factory=list)
    source: str = "library"  # "library" or "spoonacular"
    protein_match: bool = True  # False only for the thin-protein top-up fillers


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
    min_match: int = MIN_MATCH,
) -> SwapResult:
    """
    Return up to ``n`` ranked swap candidates for ``slot_index``.

    The lineup minus that slot is used for rules evaluation (so removing
    a chicken dish makes other chicken dishes eligible again under the
    variety penalty math).

    When a ``protein`` filter is set (the swap screen seeds it from the meal
    being replaced), this guarantees at least ``min_match`` candidates of that
    protein whenever the library holds that many — escalating relaxation so
    variety/soft-cap penalties can't starve the list down to one or two. If the
    library genuinely has fewer than ``min_match`` of that protein (e.g. lamb),
    it tops up with other proteins, flagged via ``note`` and ranked last.
    """
    history = history or []
    seen_ids = set(seen_ids or [])

    eval_lineup = [r for i, r in enumerate(current_lineup) if i != slot_index]
    excluded_ids = {r.get("id") for r in current_lineup if r.get("id")}

    # One fetch of swap/outcome feedback for the whole pool evaluation.
    try:
        from mealplan.event_log import feedback_signals
        feedback = feedback_signals()
    except Exception:
        feedback = {}

    # Step 1 — library-side filter (protein-matched when a protein is given).
    pool = library.filter(cuisine=cuisine, protein=protein, name_search=name_search)
    pool = [r for r in pool
            if r.get("id") not in seen_ids and r.get("id") not in excluded_ids]

    # 'plant' must mean *vegetarian* — library.filter matches any recipe whose
    # proteins include 'plant', which would let meat dishes through (pasta e
    # fagioli has bacon, mabo tofu has beef). Narrow to genuinely meatless.
    if (protein or "").lower() == "plant":
        pool = [r for r in pool
                if {p.lower() for p in (r.get("proteins") or [])} <= {"plant"}]

    # Evaluate at strict level 0 first; only escalate relaxation if that can't
    # fill the screen, so the matched list reaches min_match without needlessly
    # surfacing relaxed picks when level 0 already has enough.
    candidates = _evaluate_pool(pool, rules, eval_lineup, history, source="library",
                                feedback=feedback, max_level=0)
    if len(candidates) < n:
        candidates = _evaluate_pool(pool, rules, eval_lineup, history, source="library",
                                    feedback=feedback, max_level=MAX_RELAXATION_LEVEL)
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
            fresh = []

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
        already = {c.recipe.get("id") for c in result.candidates}
        new_pool = [r for r in freshly_saved if r.get("id") not in already]

        new_evals = _evaluate_pool(
            new_pool, rules, eval_lineup, history, source="spoonacular",
            feedback=feedback, max_level=MAX_RELAXATION_LEVEL,
        )
        result.candidates.extend(new_evals)
    elif name_search and not cuisine and not protein:
        result.note = ("No library matches. Add a cuisine + protein filter to "
                       "let me hit Spoonacular for fresh ideas.")

    # Step 3 — thin-protein safety net. With a protein filter active but still
    # under min_match (the library just doesn't hold that many), top up from the
    # rest of the library so the user always sees at least min_match options.
    # These fillers are flagged (protein_match=False) and ranked after the
    # genuine matches by _top_n.
    if protein and len(result.candidates) < min_match:
        matched_ct = len(result.candidates)
        already = {c.recipe.get("id") for c in result.candidates}
        broaden = [r for r in library.filter(cuisine=cuisine, name_search=name_search)
                   if r.get("id") not in seen_ids and r.get("id") not in excluded_ids
                   and r.get("id") not in already]
        fillers = _evaluate_pool(broaden, rules, eval_lineup, history, source="library",
                                 feedback=feedback, max_level=MAX_RELAXATION_LEVEL,
                                 protein_match=False)
        if fillers:
            result.candidates.extend(fillers)
            result.note = (f"Only {matched_ct} {protein} option"
                           f"{'s' if matched_ct != 1 else ''} in your library — "
                           f"added other proteins to round out the list.")

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
    feedback: dict | None = None,
    max_level: int = 0,
    protein_match: bool = True,
) -> list[SwapCandidate]:
    """Evaluate ``pool`` at relaxation levels 0..``max_level``, adding each
    recipe at the *lowest* level where it becomes eligible. Hard gates
    (exclusions, never_again, ceilings, cadence, spice) are level-independent,
    so escalating only loosens variety/soft-cap penalties — it never surfaces a
    banned recipe."""
    out: list[SwapCandidate] = []
    chosen: set[str] = set()
    for level in range(max_level + 1):
        for recipe in pool:
            rid = recipe.get("id")
            if rid in chosen:
                continue
            ev = evaluate_candidate(recipe, rules, eval_lineup, history,
                                    relaxation_level=level, feedback=feedback)
            if not ev.eligible:
                continue
            relax = list(ev.relaxations_applied or [])
            if level > 0:
                relax.append(f"relaxation level {level}: {relaxation_label(level)}")
            out.append(SwapCandidate(
                recipe=recipe,
                score=ev.score,
                reasons=ev.reasons,
                relaxations_applied=relax,
                source=source,
                protein_match=protein_match,
            ))
            chosen.add(rid)
    return out


def _top_n(result: SwapResult, n: int) -> SwapResult:
    """Rank genuine protein-matches first, then by score desc, breaking ties by
    least-recently-cooked; cap at n."""
    result.candidates.sort(
        key=lambda c: (not c.protein_match, -c.score,
                       c.recipe.get("last_cooked_at") or "0000")
    )
    result.candidates = result.candidates[:n]
    return result
