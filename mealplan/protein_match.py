"""
mealplan/protein_match.py
-------------------------
"Use up this protein" — match recipes to a specific cut the household already
has ("1.5 lb pork shoulder", "2 lbs pork loin").

The library only tags proteins at the animal level (``proteins: ["pork"]``),
which is too coarse: pork shoulder wants a braise, pork chops don't. So a cut
is matched against ingredient *names*: every significant word of the cut has
to appear in one ingredient. "pork loin" does not match "pork tenderloin"
(words, not substrings), and "chicken" does not match "chicken broth" or
"fish" match "fish sauce" (flavouring ingredients are skipped).

Library only by design — no Spoonacular, no LLM. When the library runs out
of a cut, the screens say so and point at Paste recipe.

Public interface:
    parse_request(text)              -> dict | None
    matching_ingredient(recipe, cut) -> dict | None
    recipe_matches(recipe, cut)      -> bool
    ingredient_lb(ingredient)        -> float | None
    amount_note(have_lb, need_lb)    -> str
"""

import re

# Descriptors that don't change which cut it is.
_NOISE = {
    "boneless", "skinless", "bone", "in", "skin", "on", "fresh", "lean", "raw",
    "whole", "of", "and", "or", "extra", "organic", "the", "a", "trimmed",
    "cut", "cubed", "piece", "into", "large", "small", "medium", "boston",
    "lb", "pound", "oz", "ounce", "kg", "g", "gram", "about",
}
# Same cut, different name. Applied to both sides before comparing.
_SYNONYMS = {"butt": "shoulder"}
# An ingredient containing any of these is a flavouring, not the protein.
_FLAVOURING = {
    "broth", "stock", "bouillon", "base", "sauce", "seasoning", "powder",
    "fat", "dripping", "gravy", "paste", "flavor", "flavour",
}

_AMOUNT_RE = re.compile(
    r"^\s*(?P<num>\d+\s+\d+/\d+|\d+/\d+|\d*\.?\d+)\s*"
    r"(?P<unit>lbs?|pounds?|oz|ounces?|kgs?|kilograms?|g|grams?)?\.?\s+"
    r"(?:of\s+)?(?P<rest>.+)$",
    re.IGNORECASE,
)
_TO_LB = {"lb": 1.0, "lbs": 1.0, "pound": 1.0, "kgs": 2.20462, "oz": 1 / 16, "ounce": 1 / 16,
          "kg": 2.20462, "kilogram": 2.20462, "g": 1 / 453.592, "gram": 1 / 453.592}


def _singular(word: str) -> str:
    if len(word) > 3 and word.endswith("s") and not word.endswith("ss"):
        return word[:-1]
    return word


def _tokens(text: str) -> set[str]:
    words = re.sub(r"[^a-z]+", " ", (text or "").lower()).split()
    out = set()
    for w in words:
        w = _singular(w)
        w = _SYNONYMS.get(w, w)
        if w not in _NOISE:
            out.add(w)
    return out


def _parse_number(s: str) -> float:
    s = s.strip()
    if " " in s:
        whole, frac = s.split(None, 1)
        return float(whole) + _parse_number(frac)
    if "/" in s:
        n, d = s.split("/", 1)
        return float(n) / float(d)
    return float(s)


def _unit_to_lb(unit: str) -> float | None:
    u = _singular((unit or "").lower().rstrip("."))
    return _TO_LB.get(u)


def parse_request(text: str) -> dict | None:
    """'1.5 lbs pork shoulder' -> {'text', 'cut': 'pork shoulder', 'amount_lb': 1.5}.

    The amount is optional ('pork shoulder' -> amount_lb None). A bare number
    with no unit ('2 pork chops') is a count, not a weight, so amount_lb stays
    None and the number is dropped from the cut. Returns None for blank input
    or input with no usable cut words.
    """
    raw = (text or "").strip()
    if not raw:
        return None
    cut, amount_lb = raw, None
    m = _AMOUNT_RE.match(raw)
    if m:
        cut = m.group("rest").strip()
        factor = _unit_to_lb(m.group("unit") or "")
        if factor is not None:
            try:
                amount_lb = round(_parse_number(m.group("num")) * factor, 2)
            except (ValueError, ZeroDivisionError):
                amount_lb = None
    cut = re.sub(r"\s+", " ", cut).strip(" .,").lower()
    if not _tokens(cut):
        return None
    return {"text": raw, "cut": cut, "amount_lb": amount_lb}


# A bare animal name ("fish", "pork") — recipes name the species or cut
# (salmon, cod), so these fall back to the recipe's protein tags.
_ANIMALS = {"beef", "pork", "chicken", "turkey", "fish", "lamb", "shrimp"}


def recipe_matches(recipe: dict, cut: str) -> bool:
    want = _tokens(cut)
    if len(want) == 1 and want <= _ANIMALS:
        tags = {(p or "").lower() for p in recipe.get("proteins") or []}
        if want <= tags:
            return True
    return matching_ingredient(recipe, cut) is not None


def matching_ingredient(recipe: dict, cut: str) -> dict | None:
    """The recipe's ingredient that is ``cut``, or None."""
    want = _tokens(cut)
    if not want:
        return None
    for ing in recipe.get("ingredients") or []:
        have = _tokens(ing.get("name") or "")
        if have & _FLAVOURING:
            continue
        if want <= have:
            return ing
    return None


def ingredient_lb(ingredient: dict | None) -> float | None:
    """The ingredient's amount in pounds, or None if it isn't a weight."""
    if not ingredient:
        return None
    factor = _unit_to_lb(ingredient.get("unit") or "")
    try:
        amount = float(ingredient.get("amount") or 0)
    except (TypeError, ValueError):
        return None
    if factor is None or amount <= 0:
        return None
    return amount * factor


def _fmt_lb(x: float) -> str:
    return f"{x:.2f}".rstrip("0").rstrip(".") + " lb"


def amount_note(have_lb: float | None, need_lb: float | None) -> str:
    """'uses 3 lb — you have 1.5 lb' style note; '' when either side is unknown."""
    if not need_lb:
        return ""
    if not have_lb:
        return f"uses {_fmt_lb(need_lb)}"
    return f"uses {_fmt_lb(need_lb)} — you have {_fmt_lb(have_lb)}"
