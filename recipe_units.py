"""
recipe_units.py
---------------
Pure, UI-free quantity/unit logic shared by the recipe display
(``screens/_recipe_view.py``) and the meal-plan grocery aggregator
(``mealplan/grocery.py``).

Both layers must round quantities the *same* way, or the shopping list and the
cook screen disagree (e.g. the recipe shows "2 cans kidney beans" while the
grocery list says "1.33 cans"). Keeping the tables + rounding here — with no
Streamlit import — lets grocery.py reuse them without pulling in the UI stack.

Public surface:
    format_minutes(minutes, short)      total time as "8 hr 20 min" / "8h 20m"
    _DISCRETE_UNITS                     set of whole-purchase units
    _round_step(unit)   -> float        display granularity for a unit
    _round_to(value, step) -> float     half-up rounding to a step
    _is_whole_item(name, unit) -> bool  countable whole item (onion, lime, …)
    round_quantity(amount, unit, name) -> float
        one summed amount → the shopper-facing quantity, rounded exactly the
        way the recipe display rounds it.
"""

import math
import re


# Units that describe a whole, indivisible purchase item. Scaling these to a
# fraction ("0.8 count tomatoes", "1.6 cans beans") reads as nonsense — you buy
# whole cans / onions / limes. For these we round to a sensible whole number
# (min 1) and, when rounding lands back on the original count, fall back to the
# original_text phrasing (which also carries can sizes and the word "canned").
_DISCRETE_UNITS = {
    "count", "can", "cans", "clove", "cloves", "package", "packages", "pkg",
    "stick", "sticks", "head", "heads", "slice", "slices", "loaf", "loaves",
    "ear", "ears", "sprig", "sprigs", "bunch", "bunches",
    "large", "medium", "small",
    # Packaged-goods containers — you buy whole jars/bags/boxes, never a
    # fraction of one. Added with the ingredient-data re-normalization so
    # jarred/bottled/bagged items scale to a whole container count.
    "jar", "jars", "bottle", "bottles", "bag", "bags", "box", "boxes",
    "tub", "tubs", "container", "containers", "packet", "packets",
    "carton", "cartons", "stalk", "stalks", "rib", "ribs",
}


def _round_step(unit: str) -> float:
    """Granularity a scaled amount snaps to, so a cook never reads "3.2 cup" or
    "0.2 tsp". Returns the smallest increment we'll show for `unit`."""
    u = unit.lower()
    if u in ("lb", "lbs", "pound", "pounds"):
        return 0.5          # half-pound is the cutoff — never 1.25 lb
    if u in ("oz", "ounce", "ounces", "fl oz", "floz"):
        return 1.0          # whole ounces
    if u in ("cup", "cups"):
        return 0.25         # quarter-cup measuring marks
    if u in ("tsp", "teaspoon", "teaspoons", "tbsp", "tbsps", "tbsp.",
             "tablespoon", "tablespoons"):
        return 0.25         # quarter-spoon
    return 0.25             # generic default — keep everything on nice fractions


def _round_to(value: float, step: float) -> float:
    """Round half-up to the nearest `step` (Python's round() is banker's, which
    surprises in a kitchen: round(2.5) == 2)."""
    return math.floor(value / step + 0.5) * step


# Whole, countable items that should never render as a fraction even when the
# imported `unit` is blank or "count" (e.g. "¾ bell pepper"). Matched as whole
# words against the ingredient name. NOTE: kept to clearly-countable nouns —
# bare "pepper" is excluded so "black pepper" stays measurable.
_WHOLE_ITEMS = (
    "onion", "bell pepper", "poblano", "jalapeno", "jalapeño", "lime", "lemon",
    "egg", "avocado", "zucchini", "cucumber", "carrot", "shallot", "scallion",
    "green onion", "tortilla", "bun", "roll", "pita", "naan", "potato",
    "sweet potato", "bay leaf", "chicken thigh", "chicken breast",
    "chicken tenderloin", "pork chop", "corn tortilla", "flour tortilla",
    "eggplant", "leek", "artichoke",
)
# Match singular or plural ("chicken thigh" → "chicken thighs", "potato" →
# "potatoes"), but not substrings ("egg" must not hit "eggplant").
_WHOLE_RE = re.compile(
    r"\b(?:" + "|".join(re.escape(w) for w in _WHOLE_ITEMS) + r")(?:e?s)?\b", re.I)


def _is_whole_item(name: str, unit: str) -> bool:
    """A countable whole item the cook buys individually. Only applies when the
    unit is blank or 'count' — a real measure unit (lb/cup/…) always wins."""
    if unit.lower() not in ("", "count"):
        return False
    return bool(_WHOLE_RE.search(name or ""))


def format_minutes(minutes, short: bool = False) -> str:
    """Render a total time the way a cook reads a clock, not a stopwatch.

    Slow-cooker recipes carry real values like 500 (8h20m of mostly unattended
    time), and "500 min" / "500m" reads as broken. Hours are split out once the
    total passes an hour.

        45   -> "45 min"      / "45m"
        75   -> "1 hr 15 min" / "1h 15m"
        480  -> "8 hr"        / "8h"
        500  -> "8 hr 20 min" / "8h 20m"
    """
    try:
        total = int(round(float(minutes)))
    except (TypeError, ValueError):
        return ""
    if total <= 0:
        return ""
    hours, mins = divmod(total, 60)
    h_unit, m_unit, sep = ("h", "m", " ") if short else (" hr", " min", " ")
    if not hours:
        return f"{mins}{m_unit}"
    if not mins:
        return f"{hours}{h_unit}"
    return f"{hours}{h_unit}{sep}{mins}{m_unit}"


def round_quantity(amount: float, unit: str, name: str = "") -> float:
    """Round a summed ingredient amount to the shopper-facing quantity, using
    the same rules the recipe display applies so the two never disagree.

    - Discrete whole purchases (cans/jars/counts/whole produce) → whole number,
      min 1 (you can't buy 0.67 of a can).
    - Everything else → snapped to the unit's kitchen increment (half-pound for
      lb, whole ounces, quarter-cup/spoon, …), floored to at least one step so a
      real ingredient never rounds away to zero.
    """
    amount = float(amount or 0)
    if amount <= 0:
        return 0.0
    u = (unit or "").lower()
    if u in _DISCRETE_UNITS or _is_whole_item(name, unit):
        return float(max(1, int(_round_to(amount, 1.0))))
    step = _round_step(unit)
    return round(max(step, _round_to(amount, step)), 2)
