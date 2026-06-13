"""
mealplan/event_log.py
---------------------
Append-only telemetry for the meal planner.

Why this exists: the existing buckets (recipe_library, current_plan,
meal_plan_history, meal_plan_rules) only track *current state*. They lose
the texture — what got proposed before swaps, which rules changed when,
which recipes get rejected repeatedly. After a few weeks of use, that
texture is what lets a dietitian (or me) refine the rules with evidence
instead of guesses.

Single KV key (`meal_plan_events`). Append-only list, capped at 500
events (~6 months of typical use). All ten event types use the same
shape:

    {
      "ts":   "ISO-8601 UTC",
      "type": "<one of ALL_EVENT_TYPES>",
      "week": int,    # snapshot of state.current_week at log time
      "data": {...},  # type-specific payload (see screen integrations)
    }

Public surface:
    log_event(event_type, data, week=None)
    recent_events(n=100, types=None, since_week=None)
    rules_diff(old, new) -> {dotted.path: (old_value, new_value)}
    summarize_for_dietitian(weeks=4) -> dict
"""

from collections import Counter
from datetime import datetime, timezone
from typing import Any, Iterable

from supabase_kv import kv_get, kv_put

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

KEY_EVENTS = "meal_plan_events"
MAX_EVENTS = 500   # rolling cap; ~6 months at typical use

# Event types
EVT_PLAN_PROPOSED       = "plan_proposed"
EVT_PLAN_REGENERATED    = "plan_regenerated"
EVT_SLOT_SWAPPED        = "slot_swapped"
EVT_PLAN_CONFIRMED      = "plan_confirmed"
EVT_RECIPE_COOKED       = "recipe_cooked"
EVT_RECIPE_CHANGED      = "recipe_changed"
EVT_RECIPE_NEVER_AGAIN  = "recipe_never_again"
EVT_RULES_CHANGED       = "rules_changed"
EVT_BOOTSTRAP_COMPLETED = "bootstrap_completed"
EVT_GROCERY_GENERATED   = "grocery_generated"
EVT_RECIPE_RATED        = "recipe_rated"

ALL_EVENT_TYPES = (
    EVT_PLAN_PROPOSED, EVT_PLAN_REGENERATED, EVT_SLOT_SWAPPED,
    EVT_PLAN_CONFIRMED, EVT_RECIPE_COOKED, EVT_RECIPE_CHANGED,
    EVT_RECIPE_NEVER_AGAIN, EVT_RULES_CHANGED, EVT_BOOTSTRAP_COMPLETED,
    EVT_GROCERY_GENERATED, EVT_RECIPE_RATED,
)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


# ---------------------------------------------------------------------------
# log_event — append + cap
# ---------------------------------------------------------------------------

def log_event(event_type: str, data: dict | None = None, week: int | None = None) -> None:
    """
    Append a structured event to the rolling log. Never raises on
    unknown event_type — defends against typos in caller code.

    Pulls current week from rules if not supplied (lets the caller skip
    one lookup). Caps storage at MAX_EVENTS (drops oldest).
    """
    if event_type not in ALL_EVENT_TYPES:
        # Don't crash on typos — re-tag as _unknown but keep the data.
        data = {"_original_type": event_type, **(data or {})}
        event_type = "_unknown"

    if week is None:
        # Lazy import to avoid circulars (rules imports supabase_kv too)
        try:
            from mealplan.rules import load_rules
            rules = load_rules()
            week = int(((rules.get("state") or {}).get("current_week")) or 0)
        except Exception:
            week = 0

    events = kv_get(KEY_EVENTS, []) or []
    events.append({
        "ts":   _now_iso(),
        "type": event_type,
        "week": int(week),
        "data": data or {},
    })
    if len(events) > MAX_EVENTS:
        events = events[-MAX_EVENTS:]
    kv_put(KEY_EVENTS, events)


# ---------------------------------------------------------------------------
# Query
# ---------------------------------------------------------------------------

def recent_events(
    n: int = 100,
    types: Iterable[str] | None = None,
    since_week: int | None = None,
) -> list[dict]:
    """Return the most recent N events, optionally filtered by type + week."""
    events = kv_get(KEY_EVENTS, []) or []
    if types:
        type_set = set(types)
        events = [e for e in events if e.get("type") in type_set]
    if since_week is not None:
        events = [e for e in events if int(e.get("week", 0)) >= since_week]
    return events[-n:]


def clear_events() -> None:
    """Wipe the log. Use carefully — only for tests / reset flows."""
    kv_put(KEY_EVENTS, [])


# ---------------------------------------------------------------------------
# Feedback signals for scoring (closes the telemetry → planner loop)
# ---------------------------------------------------------------------------

FEEDBACK_WEEKS = 8  # look-back window for swap/outcome scoring signals


def feedback_signals(weeks_back: int = FEEDBACK_WEEKS) -> dict:
    """
    Aggregate per-recipe planning feedback for the rules engine:

        { recipe_id: {"swapped_out": int, "made_it": int, "made_changes": int} }

    One KV fetch. swapped_out counts EVT_SLOT_SWAPPED where the recipe was
    the one removed; made_it / made_changes count cooking outcomes. The
    planner passes this into evaluate_candidate so recipes the user keeps
    rejecting stop being re-proposed (e.g. the same recipe was proposed and
    swapped out two weeks running before this existed).
    """
    try:
        from mealplan.rules import load_rules
        rules = load_rules()
        current_week = int(((rules.get("state") or {}).get("current_week")) or 0)
    except Exception:
        current_week = 0
    since_week = max(0, current_week - weeks_back)

    out: dict[str, dict] = {}

    def _slot(rid: str) -> dict:
        return out.setdefault(rid, {"swapped_out": 0, "made_it": 0, "made_changes": 0})

    for e in recent_events(n=MAX_EVENTS, since_week=since_week):
        d = e.get("data") or {}
        t = e.get("type")
        if t == EVT_SLOT_SWAPPED and d.get("old_recipe_id"):
            _slot(d["old_recipe_id"])["swapped_out"] += 1
        elif t == EVT_RECIPE_COOKED and d.get("recipe_id"):
            _slot(d["recipe_id"])["made_it"] += 1
        elif t == EVT_RECIPE_CHANGED and d.get("recipe_id"):
            _slot(d["recipe_id"])["made_changes"] += 1
    return out


# ---------------------------------------------------------------------------
# Rules-diff helper (used by the rules editor before logging)
# ---------------------------------------------------------------------------

def rules_diff(old: dict | None, new: dict | None, path: str = "") -> dict:
    """
    Return {dotted.path: [old_value, new_value]} for all differing fields.
    Recursive on dicts. Lists are compared as wholes (lambda-deep would be
    overkill for the rules shape).

    Used by mealplan_rules editor on Save to log only what actually changed.
    """
    old = old or {}
    new = new or {}
    if not isinstance(old, dict) or not isinstance(new, dict):
        if old == new:
            return {}
        return {path: [old, new]}

    diff: dict = {}
    keys = set(old.keys()) | set(new.keys())
    for k in keys:
        p = f"{path}.{k}" if path else k
        ov = old.get(k)
        nv = new.get(k)
        if isinstance(ov, dict) and isinstance(nv, dict):
            diff.update(rules_diff(ov, nv, p))
        elif ov != nv:
            diff[p] = [ov, nv]
    return diff


# ---------------------------------------------------------------------------
# Dietitian summary
# ---------------------------------------------------------------------------

def summarize_for_dietitian(weeks: int = 4) -> dict:
    """
    Structured summary of meal-plan activity for human review.

    ``weeks`` looks back N engine-weeks. Engine weeks bump on
    plan_confirmed, so weeks≈real weeks if you confirm one plan per
    real-world week.

    Returns:
        {
          "weeks_covered":          int,
          "current_week":           int,
          "since_week":             int,
          "plans_confirmed":        int,
          "rerolls":                int,
          "swaps":                  int,
          "reroll_ratio":           float (rerolls / plans),
          "swap_ratio":             float (swaps per slot, assuming 5/wk),
          "cooking_outcomes":       {"made_it": int, "made_changes": int,
                                     "never_again": int},
          "most_swapped_out":       [{"recipe_id", "title", "count"}],
          "most_cooked":            [{"recipe_id", "title", "count"}],
          "never_again_added":      [{"recipe_id", "title", "ts", "via"}],
          "rules_changes_timeline": [{"ts", "week", "fields"}],
          "bootstraps":             [{"ts", "cuisines", "added", "points"}],
          "grocery_runs":           int,
        }

    The most_swapped_out list is the highest-signal field for rule
    refinement: persistently swapped recipes are candidates for
    `never_again` or for tighter caps on their protein/cuisine/carb.
    """
    # Lazy import — load_rules needs supabase_kv which is fine, but
    # importing at module top would re-execute when event_log is imported
    # from rules itself if we ever cross-reference.
    from mealplan.rules import load_rules

    rules = load_rules()
    current_week = int(((rules.get("state") or {}).get("current_week")) or 0)
    since_week = max(0, current_week - weeks)

    events = recent_events(n=MAX_EVENTS, since_week=since_week)
    by_type: dict[str, list[dict]] = {t: [] for t in ALL_EVENT_TYPES}
    for e in events:
        t = e.get("type")
        if t in by_type:
            by_type[t].append(e)

    plans        = len(by_type[EVT_PLAN_CONFIRMED])
    rerolls      = len(by_type[EVT_PLAN_REGENERATED])
    swaps        = len(by_type[EVT_SLOT_SWAPPED])
    made_it      = len(by_type[EVT_RECIPE_COOKED])
    made_changes = len(by_type[EVT_RECIPE_CHANGED])
    never_again  = len(by_type[EVT_RECIPE_NEVER_AGAIN])
    grocery_runs = len(by_type[EVT_GROCERY_GENERATED])

    swap_counter = Counter()
    for e in by_type[EVT_SLOT_SWAPPED]:
        rid = e["data"].get("old_recipe_id")
        if rid:
            title = e["data"].get("old_title") or ""
            swap_counter[(rid, title)] += 1
    most_swapped_out = [
        {"recipe_id": rid, "title": title, "count": n}
        for (rid, title), n in swap_counter.most_common(5)
    ]

    cook_counter = Counter()
    for e in by_type[EVT_RECIPE_COOKED] + by_type[EVT_RECIPE_CHANGED]:
        rid = e["data"].get("recipe_id")
        if rid:
            title = e["data"].get("title") or ""
            cook_counter[(rid, title)] += 1
    most_cooked = [
        {"recipe_id": rid, "title": title, "count": n}
        for (rid, title), n in cook_counter.most_common(5)
    ]

    never_again_list = [
        {
            "recipe_id": e["data"].get("recipe_id"),
            "title":     e["data"].get("title", ""),
            "ts":        e.get("ts"),
            "via":       e["data"].get("via", "unknown"),
        }
        for e in by_type[EVT_RECIPE_NEVER_AGAIN]
    ]

    rules_changes = [
        {
            "ts":     e.get("ts"),
            "week":   e.get("week"),
            "fields": e["data"].get("changed_fields", {}),
        }
        for e in by_type[EVT_RULES_CHANGED]
    ]

    bootstraps = [
        {
            "ts":        e.get("ts"),
            "cuisines":  e["data"].get("cuisines_swept", []),
            "added":     e["data"].get("new_recipes_added", 0),
            "points":    e["data"].get("points_used", 0),
        }
        for e in by_type[EVT_BOOTSTRAP_COMPLETED]
    ]

    return {
        "weeks_covered":          weeks,
        "current_week":           current_week,
        "since_week":             since_week,
        "plans_confirmed":        plans,
        "rerolls":                rerolls,
        "swaps":                  swaps,
        "reroll_ratio":           (rerolls / plans) if plans else 0.0,
        "swap_ratio":             (swaps / (plans * 5)) if plans else 0.0,
        "cooking_outcomes":       {"made_it": made_it, "made_changes": made_changes,
                                   "never_again": never_again},
        "most_swapped_out":       most_swapped_out,
        "most_cooked":            most_cooked,
        "never_again_added":      never_again_list,
        "rules_changes_timeline": rules_changes,
        "bootstraps":             bootstraps,
        "grocery_runs":           grocery_runs,
    }


# ---------------------------------------------------------------------------
# CLI — quick inspection
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import json
    import sys
    if "--summary" in sys.argv:
        weeks = 4
        if "--weeks" in sys.argv:
            weeks = int(sys.argv[sys.argv.index("--weeks") + 1])
        print(json.dumps(summarize_for_dietitian(weeks=weeks), indent=2, default=str))
    elif "--tail" in sys.argv:
        n = 20
        if "--n" in sys.argv:
            n = int(sys.argv[sys.argv.index("--n") + 1])
        print(json.dumps(recent_events(n=n), indent=2, default=str))
    elif "--clear" in sys.argv:
        clear_events()
        print("event log cleared")
    else:
        print("Usage: python3 -m mealplan.event_log [--summary [--weeks N] | --tail [--n N] | --clear]")
