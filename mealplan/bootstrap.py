"""
mealplan/bootstrap.py
---------------------
One-shot Spoonacular bootstrap to seed the recipe library (~110 recipes).

Four steps, each safely re-runnable (library.save is idempotent on
Spoonacular source_id, and never clobbers recipes with times_cooked > 0
or non-empty user_notes — see [[mealplan_library]]):

    Step 1: Six fan favorites — query by title, surface top 3 candidates
            per favorite, save the user's pick as ``lib_<slug>`` with
            status="favorite" + add an entry to meal_plan_rules.favorites
            with cadence [4, 6].
    Step 2: Cuisine sweep — for each cuisine in rotation_set, pull
            ``per_cuisine`` main courses (default 7) and save all.
    Step 3: Protein gap-fill — pull more recipes for any protein with
            < threshold representation in the library after step 2.
    Step 4: Spice scan — flag titles containing 'spicy', 'hot', 'fire',
            'ghost', 'habanero' for the user to confirm whether to
            demote to never_again.

This module is pure orchestration. The UI ([[mealplan_bootstrap_screen]])
calls the public functions and renders progress.
"""

import re
from dataclasses import dataclass, field
from typing import Callable

from mealplan import library, spoonacular
from mealplan.rules import (
    KEY_RULES,
    default_rules,
    load_rules,
    save_rules,
)
from mealplan.spoonacular import SPICY_TITLE_KEYWORDS

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

FAVORITES = (
    # (slug, list of progressively-broader search queries)
    # Spoonacular's complexSearch does literal substring title matching, not
    # semantic search. Full PRD titles like "Smash Burgers" return zero hits
    # because no recipe is titled that exactly. Fall back to broader words
    # ("burger") until we get candidates the user can pick from.
    ("smash_burgers",            ["smash burger", "smashburger", "burger"]),
    ("chicken_katsu",            ["chicken katsu", "katsu"]),
    ("miso_glazed_salmon",       ["miso glazed salmon", "miso salmon", "miso"]),
    ("greek_lamb_meatballs",     ["greek lamb meatballs", "lamb meatballs", "lamb"]),
    ("moroccan_pork_tenderloin", ["moroccan pork tenderloin", "moroccan pork", "pork tenderloin"]),
    ("tofu_banh_mi_bowls",       ["tofu banh mi", "banh mi", "tofu bowl"]),
)
"""Slug + ordered list of progressively-broader search queries. Bootstrap
falls through the list until one returns candidates."""

DEFAULT_PROTEIN_GAP_THRESHOLD = 5
# 5 instead of 7 (PRD §15.1.2) so the full bootstrap fits in the 150/day
# Spoonacular free-tier cap even when favorites burn extra points on
# progressive query fallbacks. Bump back to 7 once we have headroom.
DEFAULT_PER_CUISINE = 5
DEFAULT_MAX_READY = 60
DEFAULT_FAV_CADENCE = [4, 6]


# ---------------------------------------------------------------------------
# Result + config types
# ---------------------------------------------------------------------------

@dataclass
class BootstrapConfig:
    """Tunable parameters surfaced in the bootstrap UI."""
    cuisines: list[str] = field(default_factory=list)   # subset of rotation_set
    per_cuisine: int = DEFAULT_PER_CUISINE
    max_ready: int = DEFAULT_MAX_READY
    protein_gap_threshold: int = DEFAULT_PROTEIN_GAP_THRESHOLD
    mild_only: bool = True
    favorites_to_pick: list[str] = field(  # slugs from FAVORITES
        default_factory=lambda: [slug for slug, _ in FAVORITES])

    @staticmethod
    def display_name(slug: str) -> str:
        """Human-readable label for the slug (uses first query as the canonical name)."""
        for s, queries in FAVORITES:
            if s == slug:
                return queries[0].title()
        return slug


@dataclass
class FavoriteCandidate:
    slug: str
    title_query: str
    candidates: list[dict]   # normalized Recipe dicts, up to 3


@dataclass
class CuisineSweepResult:
    cuisine: str
    saved_ids: list[str]
    error: str = ""


@dataclass
class SpiceFlag:
    recipe_id: str
    title: str
    keyword: str


# ---------------------------------------------------------------------------
# Cost estimation
# ---------------------------------------------------------------------------

def estimated_cost(config: BootstrapConfig) -> int:
    """
    Approximation matching spoonacular._request's cost model:
        complexSearch with addRecipeInformation=true and number=N: ~ 1 + N

    Returns total points the full bootstrap should consume. Used by the
    UI to gate against ``points_remaining_today``.
    """
    cost = 0
    # Step 1: 3 candidates per favorite, ×2 fallback budget (Spoonacular's
    # literal title matching often misses the canonical name and we retry
    # with broader queries — see find_favorite_candidates).
    cost += sum(2 * (1 + 3) for _slug in config.favorites_to_pick)
    # Step 2: cuisine sweep
    cost += sum(1 + config.per_cuisine for _c in config.cuisines)
    # Step 3: gap-fill — assume up to 2 proteins need top-up, 5 each
    cost += 2 * (1 + 5)
    return cost


# ---------------------------------------------------------------------------
# Step 1 — Favorites
# ---------------------------------------------------------------------------

def find_favorite_candidates(
    slug: str,
    queries: list[str] | str,
    mild: bool = True,
) -> FavoriteCandidate:
    """
    Pull top 3 Spoonacular candidates for one favorite, with progressive
    query fallback.

    Spoonacular's complexSearch does literal title substring matching. The
    canonical name ("Smash Burgers") often returns zero hits; broader words
    ("burger") do. We try the first query, fall back to the next if the
    first returns empty, etc. Costs 1 + n points per attempted query.

    Args:
        slug:    library slug (becomes recipe_id as lib_<slug>)
        queries: ordered list of query strings (broadest last). A bare
                 string is wrapped in a one-element list.
        mild:    apply the mild-spice client-side filter

    Returns:
        FavoriteCandidate with title_query set to whichever query actually
        produced hits (or the original query if every fallback was empty).
    """
    if isinstance(queries, str):
        queries = [queries]
    last_tried = queries[0]
    for q in queries:
        last_tried = q
        candidates = spoonacular.search(
            query=q,
            number=3,
            max_ready=DEFAULT_MAX_READY,
            mild=mild,
            sort="popularity",
        )
        if candidates:
            return FavoriteCandidate(slug=slug, title_query=q, candidates=candidates)
    # Every fallback was empty.
    return FavoriteCandidate(slug=slug, title_query=last_tried, candidates=[])


def save_favorite_pick(slug: str, chosen_recipe: dict) -> str:
    """
    Save the user's chosen Spoonacular result as a favorite library entry
    AND register it in meal_plan_rules.favorites if not already there.

    The recipe_id is forced to ``lib_<slug>`` so future references are
    stable. Returns the recipe_id used.
    """
    recipe_id = f"lib_{slug}"
    chosen = dict(chosen_recipe)
    chosen["id"] = recipe_id
    chosen["status"] = "favorite"
    library.save(chosen)

    # Add to rules.favorites if not already present.
    rules = load_rules()
    existing = {f.get("recipe_id") for f in (rules.get("favorites") or [])}
    if recipe_id not in existing:
        rules.setdefault("favorites", []).append({
            "recipe_id":      recipe_id,
            "cadence_weeks":  list(DEFAULT_FAV_CADENCE),
            "last_used_week": None,
        })
        save_rules(rules)
    return recipe_id


# ---------------------------------------------------------------------------
# Step 2 — Cuisine sweep
# ---------------------------------------------------------------------------

def run_cuisine_sweep(
    config: BootstrapConfig,
    progress: Callable[[str, int, int], None] | None = None,
) -> list[CuisineSweepResult]:
    """
    Pull config.per_cuisine main courses for each cuisine in config.cuisines.
    Calls progress(cuisine, completed_count, total) after each cuisine.
    """
    results: list[CuisineSweepResult] = []
    total = len(config.cuisines)
    for i, cuisine in enumerate(config.cuisines, start=1):
        try:
            recipes = spoonacular.search(
                cuisine=cuisine,
                number=config.per_cuisine,
                max_ready=config.max_ready,
                mild=config.mild_only,
                sort="popularity",
            )
            saved_ids: list[str] = []
            for r in recipes:
                rid = library.save(r)
                saved_ids.append(rid)
            results.append(CuisineSweepResult(cuisine=cuisine, saved_ids=saved_ids))
        except Exception as e:
            results.append(CuisineSweepResult(cuisine=cuisine, saved_ids=[], error=str(e)))
        if progress:
            progress(cuisine, i, total)
    return results


# ---------------------------------------------------------------------------
# Step 3 — Protein gap-fill
# ---------------------------------------------------------------------------

def run_protein_gap_fill(
    config: BootstrapConfig,
    progress: Callable[[str, int, int], None] | None = None,
) -> dict[str, list[str]]:
    """
    For each protein with fewer than ``protein_gap_threshold`` recipes in
    the library after the sweep, pull 5 more. Returns {protein: [saved_ids]}.
    """
    summary = library.data_summary()
    by_protein = summary.get("by_protein") or {}
    target = config.protein_gap_threshold

    gaps = []
    for protein in ("beef", "pork", "chicken", "fish", "lamb", "shrimp", "plant"):
        if by_protein.get(protein, 0) < target:
            gaps.append(protein)

    out: dict[str, list[str]] = {}
    for i, protein in enumerate(gaps, start=1):
        kwargs = {
            "number":    5,
            "max_ready": config.max_ready,
            "mild":      config.mild_only,
            "sort":      "popularity",
        }
        if protein == "plant":
            kwargs["diet"] = "vegetarian"
        else:
            kwargs["protein"] = protein
        try:
            recipes = spoonacular.search(**kwargs)
            saved_ids = [library.save(r) for r in recipes]
            out[protein] = saved_ids
        except Exception as e:
            out[protein] = []
            # Recorded as empty; UI surfaces the partial result.
        if progress:
            progress(protein, i, len(gaps))
    return out


# ---------------------------------------------------------------------------
# Step 4 — Spice scan
# ---------------------------------------------------------------------------

def scan_spicy_titles() -> list[SpiceFlag]:
    """
    Walk every active recipe in the library, flag titles containing any
    of the spicy keywords. UI lets user confirm whether to demote to
    never_again or keep.
    """
    flagged: list[SpiceFlag] = []
    for rid, recipe in (library.get_all() or {}).items():
        if recipe.get("status") == "never_again":
            continue
        title = (recipe.get("title") or "").lower()
        for kw in SPICY_TITLE_KEYWORDS:
            if re.search(rf"\b{kw}\b", title):
                flagged.append(SpiceFlag(recipe_id=rid, title=recipe.get("title", ""), keyword=kw))
                break
    return flagged


def demote_spicy(recipe_ids: list[str]) -> int:
    """Mark recipes as never_again and add to rules.exclusions. Returns count demoted."""
    if not recipe_ids:
        return 0
    rules = load_rules()
    excl = set(rules.get("exclusions") or [])
    count = 0
    for rid in recipe_ids:
        if library.set_status(rid, "never_again"):
            count += 1
        excl.add(rid)
    rules["exclusions"] = sorted(excl)
    save_rules(rules)
    return count
