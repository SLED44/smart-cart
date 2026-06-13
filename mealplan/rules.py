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
# Appliance preference (extension beyond PRD §8.2 — decided 2026-05-25,
# see memory/mealplan_appliance_bonus.md). Recipes whose equipment array
# includes the household.default_appliance get a small score boost so they
# float toward the top without crowding out the variety of the library.
_BONUS_APPLIANCE_MATCH = 10

# Planning-feedback signals from the event log (extension beyond PRD §8.2 —
# added 2026-06-12 after the same recipe was proposed and swapped out two
# weeks running). Sourced from event_log.feedback_signals(); never relaxed.
_PENALTY_SWAPPED_OUT = -25   # per swap-out in the look-back window…
_SWAPPED_OUT_FLOOR = -50     # …capped here so one bad month isn't a death sentence
_PENALTY_MADE_CHANGES = -10  # cooked, but needed tweaks — mild demotion
_BONUS_MADE_IT = 15          # cooked as written — gentle promotion

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
    """
    Dietitian-tuned defaults for an active family of 4 with kids under 12.

    Designed 2026-05-26 (diverges from PRD §7.1 which was placeholders).
    See memory/mealplan_dietitian_defaults.md for the full reasoning.

    High-level shape per dimension:
      Household:  size=4, mild spice (kids), air fryer default, store-bought sauces.
      Proteins:   chicken is the workhorse (soft 2/wk, ceiling 3). Fish hard-
                  capped at 1/wk per household preference. Red meat capped
                  (beef ≤2/wk ceiling, pork ≤2). Lamb + shrimp occasional.
                  Plant ≥1/wk for nutrient + variety reasons.
      Cadences:   Shrimp hard-capped at every 4 weeks (cost + variety).
      Carbs:      Rice + pasta + potato + salad each capped at 2/wk so no carb
                  dominates. Bread tighter (≤1) since it's the easy default
                  (burgers/sandwiches). Grain (quinoa/farro/couscous) uncapped
                  to encourage exposure.
      Cuisines:   13 rotation. Must include one of american/italian/mexican every
                  week — kid-anchor cuisines.

    For active families with teens / adult-only households / different focus
    (heart, weight, performance), call this then customise via the rules editor.
    """
    return {
        "household": {
            "size":                    4,
            "meals_per_week_default":  5,    # weekday dinners; leftovers cover lunch
            "spice":                   "mild",
            "default_appliance":       "air_fryer",
            "buy_dont_make_sauces":    True,
        },
        "protein_limits": {
            # max_per_week = soft cap (-30 score penalty if exceeded)
            # absolute_ceiling = hard cap (rejected outright above this)
            "beef":    {"max_per_week": 1, "absolute_ceiling": 2},
            "pork":    {"max_per_week": 1, "absolute_ceiling": 2},
            "chicken": {"max_per_week": 2, "absolute_ceiling": 3},
            "turkey":  {"max_per_week": 1, "absolute_ceiling": 2},
            "fish":    {"max_per_week": 1, "absolute_ceiling": 1},
            "lamb":    {"max_per_week": 1, "absolute_ceiling": 1},
            "shrimp":  {"max_per_week": 1, "absolute_ceiling": 1},
            "plant":   {"max_per_week": 1, "absolute_ceiling": 2},
        },
        "protein_cadences": [
            # Shrimp every 4 weeks (PRD §7.1) — cost + protein variety.
            {"protein": "shrimp", "cadence_weeks": 4, "last_used_week": None},
        ],
        "carb_limits": {
            "rice":   2,    # katsu, fried rice, etc.
            "pasta":  2,    # cap to force grain rotation
            "bread":  1,    # easy to overdo (burgers, sandwiches)
            "grain":  None, # quinoa/couscous/farro — encourage these
            "potato": 2,    # kid-friendly, healthy
            "salad":  2,    # main-dish salads
        },
        "cuisines": {
            "rotation_set": [
                "american", "italian", "mexican",
                "japanese", "korean", "vietnamese", "thai", "chinese",
                "mediterranean", "greek", "middle_eastern", "moroccan", "indian",
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
_VALID_PROTEINS = ("beef", "pork", "chicken", "turkey", "fish", "lamb", "shrimp", "plant")
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
    feedback: dict | None = None,
) -> Evaluation:
    """
    Evaluate a single recipe against the ruleset. See PRD §8.2.

    Hard rules (never relaxed) short-circuit to eligible=False.
    Soft rules contribute to the score and are dampened by relaxation_level.

    ``feedback`` is event_log.feedback_signals() — per-recipe swap-out and
    cooked-outcome counts. Optional so pure-rules tests don't need KV; the
    planner fetches it once per generation and passes it down.
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

    # Appliance preference (never relaxed — small bonus, never blocks).
    default_app = ((rules.get("household") or {}).get("default_appliance") or "").lower()
    equipment = {(e or "").lower() for e in (recipe.get("equipment") or [])}
    if default_app and default_app in equipment:
        score += _BONUS_APPLIANCE_MATCH
        reasons.append(f"matches default appliance {default_app} (+{_BONUS_APPLIANCE_MATCH})")

    # Planning feedback (never relaxed) — what actually happened to this
    # recipe in recent proposals and kitchens.
    fb = (feedback or {}).get(rid) or {}
    swaps = int(fb.get("swapped_out") or 0)
    if swaps:
        pen = max(_SWAPPED_OUT_FLOOR, swaps * _PENALTY_SWAPPED_OUT)
        score += pen
        reasons.append(f"swapped out of {swaps} recent proposal(s) ({pen})")
    if int(fb.get("made_it") or 0):
        score += _BONUS_MADE_IT
        reasons.append(f"cooked as written recently (+{_BONUS_MADE_IT})")
    if int(fb.get("made_changes") or 0):
        score += _PENALTY_MADE_CHANGES
        reasons.append(f"needed changes when cooked ({_PENALTY_MADE_CHANGES})")

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
