"""
mealplan/rules.py
-----------------
Meal-planner rules engine.

Public interface:
    default_rules()                                          -> dict
    load_rules()                                             -> dict
    save_rules(rules)                                        -> None
    validate_rules(rules)                                    -> list[str]
    evaluate_candidate(recipe, rules, lineup, history, level=0) -> Evaluation
    bump_state_after_confirm(rules, lineup, history)         -> dict
    relaxation_label(level)                                  -> str

All rules persist in the existing Supabase ``kv`` table under the
``meal_plan_rules`` key (no schema change). See PRD §7.1 for the schema
and §8 for the evaluation spec. Relaxation order is locked to Option A
(see PRD §20.1).

Pure Python — no Anthropic, Spoonacular, or Kroger calls.

Design note — relaxation semantics
----------------------------------
PRD §8.2 describes every soft rule (variety, favorites cadence,
must-include cuisine, soft caps) as *scoring-only*: a rule breach reduces
the candidate's score but does NOT make it ineligible. PRD §8.3 then says
relaxation levels 1–4 "loosen specific checks" — but if those checks are
already scoring-only at L0, raising the relaxation level only removes the
score penalty; it cannot unlock additional candidates.

This module implements PRD §8.2 literally: hard rules (exclusions,
pair-exclusions, absolute ceilings, vegetarian cap, shrimp cadence,
spice level, ``never_again``) are the only eligibility gates. Relaxation
levels 1–4 affect ``score`` only. The planner's escalation loop is
therefore defensive — it fires only when *hard* rules zero out
eligibility, at which point no level of relaxation helps (those hard
rules are explicitly listed as "never relaxed" in PRD §8.3).

Practical impact: under this implementation, ``generate_lineup`` either
succeeds at level 0 or raises ``NoCandidatesError`` because a hard rule
exhausted the pool. If you want true escalation behavior (e.g.
``max_per_week`` is a hard rule at L0 that softens to a penalty at L4),
that's a one-line change to ``evaluate_candidate`` — but it contradicts
PRD §8.2.8's "still eligible at relaxation 0 IF under ceiling, but
heavily penalized" wording. Surface to user before changing.
"""

import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from supabase_kv import kv_get, kv_put

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

KEY_RULES = "meal_plan_rules"

# Word-boundary spice keywords, mirror of mealplan.spoonacular per PRD §8.2.7.
_SPICY_TITLE_KEYWORDS = ("spicy", "hot", "fire", "ghost", "habanero")

# Relaxation levels (Option A, locked per §20.1).
MAX_RELAXATION_LEVEL = 4

_RELAXATION_LABELS = {
    0: "no relaxation",
    1: "ignore variety penalties",
    2: "ignore favorites cadence boost",
    3: "ignore must-include cuisine",
    4: "allow soft caps (max_per_week) to be exceeded — ceiling still enforced",
}


def relaxation_label(level: int) -> str:
    return _RELAXATION_LABELS.get(level, f"unknown level {level}")


# Recent-history window for the "recently cooked" penalty (PRD §8.2).
_RECENT_PENALTY_WEEKS = 4

# Soft-cap and variety penalties, exactly per PRD §8.2.
_PENALTY_VARIETY_CUISINE = -20
_PENALTY_VARIETY_PROTEIN = -15
_PENALTY_VARIETY_CARB = -15
_PENALTY_SOFT_CAP = -30
_PENALTY_RECENT_HISTORY = -40

_BONUS_FAVORITE_DUE = 30
_BONUS_FAVORITE_FORCE = 50
_BONUS_CUISINE_MUST_INCLUDE = 20

_BASE_SCORE = 100.0


# ---------------------------------------------------------------------------
# Result types
# ---------------------------------------------------------------------------

@dataclass
class Evaluation:
    """Per-candidate result. See PRD §8.1."""
    eligible: bool
    score: float = 0.0
    reasons: list[str] = field(default_factory=list)
    relaxations_applied: list[str] = field(default_factory=list)
    rejection_reason: str = ""


# ---------------------------------------------------------------------------
# Defaults
# ---------------------------------------------------------------------------

def default_rules() -> dict:
    """Starter ruleset matching PRD §7.1. Used by the rules editor on first run."""
    return {
        "household": {
            "size":                    4,
            "meals_per_week_default":  5,
            "spice":                   "mild",
            "default_appliance":       "air_fryer",
            "buy_dont_make_sauces":    True,
        },
        "protein_limits": {
            "beef":    {"max_per_week": 1,    "absolute_ceiling": 2},
            "pork":    {"max_per_week": None, "absolute_ceiling": None},
            "chicken": {"max_per_week": None, "absolute_ceiling": None},
            "fish":    {"max_per_week": None, "absolute_ceiling": None},
            "lamb":    {"max_per_week": None, "absolute_ceiling": None},
            "shrimp":  {"max_per_week": 1,    "absolute_ceiling": 1},
            "plant":   {"max_per_week": 1,    "absolute_ceiling": 1},
        },
        "protein_cadences": [
            {"protein": "shrimp", "cadence_weeks": 4, "last_used_week": None},
        ],
        "carb_limits": {
            "rice":   2,
            "pasta":  None,
            "bread":  None,
            "grain":  None,
            "potato": None,
            "salad":  2,
        },
        "cuisines": {
            "rotation_set": [
                "american", "italian", "mexican", "japanese", "korean",
                "vietnamese", "thai", "chinese", "mediterranean", "greek",
                "middle_eastern", "moroccan", "indian",
            ],
            "must_include_one_of_per_week": ["american", "italian", "mexican"],
            "forbid_back_to_back_same_cuisine": True,
        },
        "favorites": [],
        "exclusions": [],
        "pair_exclusions": [],
        "state": {
            "current_week":             1,
            "shrimp_counter":           0,
            "last_plan_confirmed_at":   None,
        },
    }


# ---------------------------------------------------------------------------
# Persistence
# ---------------------------------------------------------------------------

def load_rules() -> dict:
    """Read from KV; return defaults if unset. The returned dict is merged
    onto defaults so a missing key never crashes the engine."""
    stored = kv_get(KEY_RULES, None)
    if not stored:
        return default_rules()
    return _merge_with_defaults(stored)


def save_rules(rules: dict) -> None:
    errs = validate_rules(rules)
    if errs:
        raise ValueError("invalid rules: " + "; ".join(errs))
    kv_put(KEY_RULES, rules)


def _merge_with_defaults(stored: dict) -> dict:
    """Recursive top-level merge: stored wins, but missing keys come from defaults."""
    base = default_rules()
    out = dict(base)
    for k, v in stored.items():
        if isinstance(v, dict) and isinstance(base.get(k), dict):
            out[k] = {**base[k], **v}
        else:
            out[k] = v
    return out


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------

_VALID_SPICE = ("mild", "medium", "hot")
_VALID_PROTEINS = ("beef", "pork", "chicken", "fish", "lamb", "shrimp", "plant")
_VALID_CARBS = ("rice", "pasta", "bread", "grain", "potato", "salad")


def validate_rules(rules: dict) -> list[str]:
    """Return a list of error strings (empty list = valid)."""
    errs: list[str] = []

    h = rules.get("household") or {}
    if not isinstance(h.get("size"), int) or h["size"] < 1:
        errs.append("household.size must be a positive integer")
    mpw = h.get("meals_per_week_default")
    if not isinstance(mpw, int) or not (1 <= mpw <= 7):
        errs.append("household.meals_per_week_default must be 1..7")
    if h.get("spice") not in _VALID_SPICE:
        errs.append(f"household.spice must be one of {_VALID_SPICE}")

    plimits = rules.get("protein_limits") or {}
    for p in _VALID_PROTEINS:
        if p not in plimits:
            errs.append(f"protein_limits.{p} missing")
            continue
        entry = plimits[p] or {}
        for fld in ("max_per_week", "absolute_ceiling"):
            v = entry.get(fld)
            if v is not None and (not isinstance(v, int) or v < 0):
                errs.append(f"protein_limits.{p}.{fld} must be null or non-negative int")

    clim = rules.get("carb_limits") or {}
    for c in _VALID_CARBS:
        if c not in clim:
            errs.append(f"carb_limits.{c} missing")
            continue
        v = clim[c]
        if v is not None and (not isinstance(v, int) or v < 0):
            errs.append(f"carb_limits.{c} must be null or non-negative int")

    cs = rules.get("cuisines") or {}
    rot = cs.get("rotation_set") or []
    if not isinstance(rot, list) or not rot:
        errs.append("cuisines.rotation_set must be a non-empty list")
    musts = cs.get("must_include_one_of_per_week") or []
    if not isinstance(musts, list):
        errs.append("cuisines.must_include_one_of_per_week must be a list")
    else:
        for m in musts:
            if m not in rot:
                errs.append(f"must_include cuisine {m!r} not in rotation_set")

    if not isinstance(rules.get("favorites") or [], list):
        errs.append("favorites must be a list")
    for fav in rules.get("favorites") or []:
        if not isinstance(fav, dict) or "recipe_id" not in fav:
            errs.append("favorites entries need a recipe_id")

    if not isinstance(rules.get("exclusions") or [], list):
        errs.append("exclusions must be a list of recipe_ids")
    if not isinstance(rules.get("pair_exclusions") or [], list):
        errs.append("pair_exclusions must be a list of [id, id] pairs")
    for pair in rules.get("pair_exclusions") or []:
        if not (isinstance(pair, (list, tuple)) and len(pair) == 2):
            errs.append(f"pair_exclusion {pair!r} must be a 2-element list")

    state = rules.get("state") or {}
    cw = state.get("current_week")
    if not isinstance(cw, int) or cw < 1:
        errs.append("state.current_week must be a positive integer")

    return errs


# ---------------------------------------------------------------------------
# Evaluation
# ---------------------------------------------------------------------------

def evaluate_candidate(
    recipe: dict,
    rules: dict,
    current_lineup: list[dict],
    history: list[dict] | None = None,
    relaxation_level: int = 0,
) -> Evaluation:
    """
    Evaluate a single recipe against the ruleset. See PRD §8.2.

    Hard rules (never relaxed) short-circuit to eligible=False.
    Soft rules contribute to the score and are dampened by relaxation_level.
    """
    history = history or []

    # --- Hard rules ----------------------------------------------------------
    rid = recipe.get("id", "")
    excl = set(rules.get("exclusions") or [])
    if rid in excl:
        return Evaluation(False, rejection_reason="in exclusions")
    if recipe.get("status") == "never_again":
        return Evaluation(False, rejection_reason="status=never_again")

    for pair in rules.get("pair_exclusions") or []:
        if len(pair) != 2:
            continue
        a, b = pair
        lineup_ids = {r.get("id") for r in current_lineup}
        if (rid == a and b in lineup_ids) or (rid == b and a in lineup_ids):
            return Evaluation(False, rejection_reason=f"pair-excluded with {b if rid == a else a}")

    # Absolute protein ceiling (PRD §8.2.4) — never relaxed.
    proteins = [p.lower() for p in (recipe.get("proteins") or [])]
    lineup_protein_counts = _count_proteins(current_lineup)
    plimits = rules.get("protein_limits") or {}
    for p in proteins:
        ceiling = ((plimits.get(p) or {}).get("absolute_ceiling"))
        if ceiling is not None and (lineup_protein_counts.get(p, 0) + 1) > ceiling:
            return Evaluation(False, rejection_reason=f"absolute ceiling for {p} ({ceiling})")

    # Vegetarian cap — covered by absolute ceiling on "plant", but PRD §8.2.5
    # also calls it out explicitly. The above loop already enforces it.

    # Shrimp cadence (PRD §8.2.6) — never relaxed.
    cadence_block = _shrimp_cadence_block(rules)
    if cadence_block and "shrimp" in proteins:
        last_used = cadence_block.get("last_used_week")
        cadence = int(cadence_block.get("cadence_weeks") or 0)
        cw = int(((rules.get("state") or {}).get("current_week")) or 1)
        if last_used is not None and cadence > 0 and (cw - int(last_used)) < cadence:
            return Evaluation(False, rejection_reason=f"shrimp cadence: {cw - int(last_used)}wk < {cadence}wk")

    # Spice level (PRD §8.2.7) — never relaxed.
    if (rules.get("household") or {}).get("spice") == "mild":
        if _is_spicy_title(recipe.get("title", "")):
            return Evaluation(False, rejection_reason="spicy title (household=mild)")

    # --- Soft scoring --------------------------------------------------------
    score = _BASE_SCORE
    reasons: list[str] = []
    relaxations: list[str] = []

    favorites = rules.get("favorites") or []
    fav_entry = next((f for f in favorites if f.get("recipe_id") == rid), None)
    cw = int(((rules.get("state") or {}).get("current_week")) or 1)

    # Favorites cadence bonus (relaxation L2 zeroes this).
    if fav_entry and relaxation_level < 2:
        last_used = fav_entry.get("last_used_week")
        cadence = fav_entry.get("cadence_weeks") or []
        if last_used is None and cadence:
            # Never used — treat as "due" if cadence_weeks[0] <= current_week.
            if cw >= int(cadence[0]):
                score += _BONUS_FAVORITE_DUE
                reasons.append(f"favorite, never used (+{_BONUS_FAVORITE_DUE})")
            if len(cadence) >= 2 and cw >= int(cadence[1]):
                score += (_BONUS_FAVORITE_FORCE - _BONUS_FAVORITE_DUE)
                reasons.append(f"favorite past force window (total +{_BONUS_FAVORITE_FORCE})")
        elif last_used is not None and cadence:
            gap = cw - int(last_used)
            if len(cadence) >= 1 and gap >= int(cadence[0]):
                score += _BONUS_FAVORITE_DUE
                reasons.append(f"favorite due ({gap}wk since last) (+{_BONUS_FAVORITE_DUE})")
            if len(cadence) >= 2 and gap >= int(cadence[1]):
                score += (_BONUS_FAVORITE_FORCE - _BONUS_FAVORITE_DUE)
                reasons.append(f"favorite force window ({gap}wk) (total +{_BONUS_FAVORITE_FORCE})")
    elif fav_entry and relaxation_level >= 2:
        relaxations.append("favorites cadence ignored (L2)")

    # Must-include cuisine (relaxation L3 zeroes this).
    must = set((rules.get("cuisines") or {}).get("must_include_one_of_per_week") or [])
    cuisines_lower = [c.lower() for c in (recipe.get("cuisines") or [])]
    covered_in_lineup = {
        c for r in current_lineup for c in (cuisine.lower() for cuisine in (r.get("cuisines") or []))
    }
    if relaxation_level < 3:
        if must and (must & set(cuisines_lower)) and not (must & covered_in_lineup):
            score += _BONUS_CUISINE_MUST_INCLUDE
            reasons.append(f"covers must-include cuisine (+{_BONUS_CUISINE_MUST_INCLUDE})")
    else:
        if must and (must & set(cuisines_lower)) and not (must & covered_in_lineup):
            relaxations.append("must-include cuisine ignored (L3)")

    # Variety penalties (relaxation L1 zeroes these).
    lineup_cuisines = {
        c for r in current_lineup for c in (cuisine.lower() for cuisine in (r.get("cuisines") or []))
    }
    lineup_proteins = set(lineup_protein_counts.keys())
    lineup_carbs = {
        c for r in current_lineup for c in (carb.lower() for carb in (r.get("carbs") or []))
    }
    if relaxation_level < 1:
        if lineup_cuisines & set(cuisines_lower):
            score += _PENALTY_VARIETY_CUISINE
            reasons.append(f"cuisine already in lineup ({_PENALTY_VARIETY_CUISINE})")
        if lineup_proteins & set(proteins):
            score += _PENALTY_VARIETY_PROTEIN
            reasons.append(f"protein already in lineup ({_PENALTY_VARIETY_PROTEIN})")
        if lineup_carbs & {c.lower() for c in (recipe.get("carbs") or [])}:
            score += _PENALTY_VARIETY_CARB
            reasons.append(f"carb already in lineup ({_PENALTY_VARIETY_CARB})")
    else:
        if lineup_cuisines & set(cuisines_lower) or \
           lineup_proteins & set(proteins) or \
           lineup_carbs & {c.lower() for c in (recipe.get("carbs") or [])}:
            relaxations.append("variety penalties ignored (L1)")

    # Soft cap penalty (relaxation L4 zeroes this; absolute ceiling is still
    # enforced above as a hard rule).
    soft_cap_breaches = []
    for p in proteins:
        max_pw = ((plimits.get(p) or {}).get("max_per_week"))
        if max_pw is not None and (lineup_protein_counts.get(p, 0) + 1) > max_pw:
            soft_cap_breaches.append(f"{p}>{max_pw}/wk")
    carb_counts = _count_carbs(current_lineup)
    clim = rules.get("carb_limits") or {}
    for c in (recipe.get("carbs") or []):
        c_low = c.lower()
        max_pw = clim.get(c_low)
        if max_pw is not None and (carb_counts.get(c_low, 0) + 1) > max_pw:
            soft_cap_breaches.append(f"{c_low}>{max_pw}/wk")
    if soft_cap_breaches:
        if relaxation_level < 4:
            score += _PENALTY_SOFT_CAP
            reasons.append(f"soft cap breach [{', '.join(soft_cap_breaches)}] ({_PENALTY_SOFT_CAP})")
        else:
            relaxations.append(f"soft cap exceeded (L4): {', '.join(soft_cap_breaches)}")

    # Recently cooked penalty (always applied — variety bias from history).
    if _used_within_weeks(recipe, history, _RECENT_PENALTY_WEEKS, cw):
        score += _PENALTY_RECENT_HISTORY
        reasons.append(
            f"cooked within last {_RECENT_PENALTY_WEEKS}wk ({_PENALTY_RECENT_HISTORY})"
        )

    return Evaluation(
        eligible=True,
        score=score,
        reasons=reasons,
        relaxations_applied=relaxations,
    )


# ---------------------------------------------------------------------------
# Post-confirm state bumps
# ---------------------------------------------------------------------------

def bump_state_after_confirm(rules: dict, confirmed_recipes: list[dict]) -> dict:
    """
    Apply state changes after a plan is confirmed. Returns the mutated rules
    (caller is responsible for ``save_rules``).

    - Snapshot ``current_week``, mark confirmed recipes as last_used at that
      week, then increment ``current_week``.
    - For shrimp: if any confirmed recipe contains shrimp, reset
      ``shrimp_counter`` to 0 and stamp the cadence entry's last_used_week;
      otherwise increment shrimp_counter by 1.
    - For favorites: stamp last_used_week on any favorite that's in the plan.
    - Stamp ``state.last_plan_confirmed_at``.
    """
    rules = _deepish_copy(rules)
    state = rules.setdefault("state", {})
    snap_week = int(state.get("current_week") or 1)

    confirmed_ids = {r.get("id") for r in confirmed_recipes if r.get("id")}
    proteins_used: set[str] = set()
    for r in confirmed_recipes:
        for p in (r.get("proteins") or []):
            proteins_used.add(p.lower())

    # Favorites
    for fav in rules.get("favorites") or []:
        if fav.get("recipe_id") in confirmed_ids:
            fav["last_used_week"] = snap_week

    # Protein cadences (currently only shrimp)
    for entry in rules.get("protein_cadences") or []:
        prot = (entry.get("protein") or "").lower()
        if prot in proteins_used:
            entry["last_used_week"] = snap_week

    # Shrimp counter (UI helper)
    if "shrimp" in proteins_used:
        state["shrimp_counter"] = 0
    else:
        state["shrimp_counter"] = int(state.get("shrimp_counter") or 0) + 1

    state["current_week"] = snap_week + 1
    state["last_plan_confirmed_at"] = _now_iso()
    return rules


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _count_proteins(recipes: list[dict]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for r in recipes:
        for p in (r.get("proteins") or []):
            key = p.lower()
            counts[key] = counts.get(key, 0) + 1
    return counts


def _count_carbs(recipes: list[dict]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for r in recipes:
        for c in (r.get("carbs") or []):
            key = c.lower()
            counts[key] = counts.get(key, 0) + 1
    return counts


def _shrimp_cadence_block(rules: dict) -> dict | None:
    for entry in (rules.get("protein_cadences") or []):
        if (entry.get("protein") or "").lower() == "shrimp":
            return entry
    return None


def _is_spicy_title(title: str) -> bool:
    t = (title or "").lower()
    return any(re.search(rf"\b{kw}\b", t) for kw in _SPICY_TITLE_KEYWORDS)


def _used_within_weeks(recipe: dict, history: list[dict], weeks: int, current_week: int) -> bool:
    """True iff this recipe appeared in a plan within the last `weeks` confirmed plans."""
    if not history or weeks <= 0:
        return False
    rid = recipe.get("id")
    if not rid:
        return False
    # history entries have a `meals: list[{recipe_id, ...}]` and `week_number`
    cutoff_week = current_week - weeks
    for plan in history:
        wn = plan.get("week_number")
        if isinstance(wn, int) and wn <= cutoff_week:
            continue
        for meal in (plan.get("meals") or []):
            if meal.get("recipe_id") == rid:
                return True
    return False


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _deepish_copy(d: Any) -> Any:
    """Shallow recursive copy that handles dicts and lists — enough for the
    rule blob, avoids the cost of full copy.deepcopy."""
    if isinstance(d, dict):
        return {k: _deepish_copy(v) for k, v in d.items()}
    if isinstance(d, list):
        return [_deepish_copy(x) for x in d]
    return d
