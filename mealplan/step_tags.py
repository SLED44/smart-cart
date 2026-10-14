"""
mealplan/step_tags.py
---------------------
Which ingredients each recipe step uses — drives the cook pane's
"tap a step, its ingredients light up" highlighting.

Claude Haiku reads the recipe once and returns ``step number -> ingredient
indices``; the result is stored on the recipe under ``step_ingredients``:

    {"fp": "<fingerprint>", "map": {"1": [13], "2": [0, 1, 2]}}

``fp`` hashes the ingredient lines and step texts, so editing a recipe makes
its stored tags stale and they're recomputed on the next open. Recipes get
tagged lazily the first time the cook screen opens them (``ensure``); the
``--backfill`` CLI tags the whole library in one go.

Why not plain text matching: the old matcher keyed on each ingredient's
last word with substring search, so "cook in a saucepan" lit up soy sauce,
oyster sauce and fish sauce, while "chicken breasts" never matched a step
that just says "chicken" (~30% of library ingredients never lit at all).
``keyword_map`` is a stricter whole-word version of that idea, kept only as
the fallback when Claude can't be reached.
"""

import hashlib
import json
import os
import re

from applog import get_logger

_log = get_logger(__name__)

FIELD = "step_ingredients"
_MODEL = "claude-haiku-4-5-20251001"

SYSTEM_PROMPT = """You map recipe steps to the ingredients each step uses, for a cooking app that highlights a step's ingredients when the cook taps it.

You get numbered ingredients (index: name | original line | group) and numbered steps. For each step, list the indices of the ingredients the cook adds, mixes, cooks, or handles in that step.

Rules:
- Resolve references to the specific ingredient meant: "the lamb" -> ground lamb, "the spices" / "the herbs" / "the dry ingredients" -> each of those ingredients.
- When several ingredients share a word (soy sauce vs oyster sauce, cornstarch for the beef vs cornstarch for the sauce), pick only the one(s) this step means. Use amounts and groups to tell duplicates apart.
- A word inside an unrelated word is not a mention ("saucepan" is not sauce, "foil" is not oil).
- If a step uses a mixture made in an earlier step ("pour in the sauce", "add the marinade"), do not re-list the mixture's components. If "the sauce" is a single listed ingredient (e.g. pasta sauce), list that ingredient.
- Steps that use no listed ingredient (preheat the oven, rest, serve) get an empty list.

Reply with JSON only: {"steps": {"1": [indices], "2": [indices], ...}} with one key per step."""


class StepTagError(RuntimeError):
    """Claude couldn't produce a usable step -> ingredient map."""


# ---------------------------------------------------------------------------
# Stored tags
# ---------------------------------------------------------------------------

def fingerprint(recipe: dict) -> str:
    """Hash of the recipe text the tags were computed from."""
    ings = [[i.get("name", ""), i.get("original_text", "")]
            for i in recipe.get("ingredients") or []]
    steps = [s.get("text", "") for s in recipe.get("instructions") or []]
    raw = json.dumps([ings, steps], ensure_ascii=False)
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:16]


def stored_map(recipe: dict) -> dict | None:
    """The saved map, or None if missing or stale (recipe edited since)."""
    tags = recipe.get(FIELD)
    if isinstance(tags, dict) and tags.get("fp") == fingerprint(recipe):
        return tags.get("map") or {}
    return None


def ensure(recipe: dict, allow_llm: bool = True) -> dict[str, list[int]]:
    """Return ``{"<step>": [ingredient indices]}`` for the recipe.

    Uses the stored map when it's current; otherwise asks Claude and saves
    the answer to the library. Never raises — on any failure it falls back
    to ``keyword_map`` (not saved, so the next open retries Claude).
    """
    cached = stored_map(recipe)
    if cached is not None:
        return cached
    if allow_llm:
        try:
            mapping = claude_map(recipe)
        except StepTagError as e:
            _log.warning("STEPTAGS %s: Claude failed, using keyword fallback: %s",
                         recipe.get("id"), e)
        else:
            _save(recipe, mapping)
            return mapping
    return keyword_map(recipe)


def _save(recipe: dict, mapping: dict) -> None:
    from mealplan import library  # local import keeps this module test-friendly

    tags = {"fp": fingerprint(recipe), "map": mapping}
    recipe[FIELD] = tags
    rid = recipe.get("id")
    if not rid:
        return
    try:
        library.set_field(rid, FIELD, tags)
    except Exception as e:  # a failed save just means we re-tag next time
        _log.warning("STEPTAGS %s: could not save tags: %s", rid, e)


# ---------------------------------------------------------------------------
# Claude
# ---------------------------------------------------------------------------

def build_prompt(recipe: dict) -> str:
    lines = ["Ingredients:"]
    for i, ing in enumerate(recipe.get("ingredients") or []):
        parts = [ing.get("name", "")]
        if ing.get("original_text"):
            parts.append(ing["original_text"])
        if ing.get("group"):
            parts.append(f"group: {ing['group']}")
        lines.append(f"{i}: " + " | ".join(parts))
    lines.append("")
    lines.append("Steps:")
    for n, step in enumerate(recipe.get("instructions") or [], start=1):
        lines.append(f"{n}. {step.get('text', '')}")
    return "\n".join(lines)


def claude_map(recipe: dict) -> dict[str, list[int]]:
    """One Haiku call -> validated step map. Raises StepTagError."""
    steps = recipe.get("instructions") or []
    if not steps:
        return {}
    api_key = os.getenv("ANTHROPIC_API_KEY", "")
    if not api_key:
        raise StepTagError("ANTHROPIC_API_KEY is not set")

    import anthropic

    try:
        message = anthropic.Anthropic(api_key=api_key).messages.create(
            model=_MODEL,
            max_tokens=2048,
            system=SYSTEM_PROMPT,
            messages=[{"role": "user", "content": build_prompt(recipe)}],
        )
    except anthropic.APIError as e:
        raise StepTagError(f"Anthropic API error: {e}") from e
    if message.stop_reason == "max_tokens":
        raise StepTagError("response was cut off")

    text = "".join(b.text for b in message.content if getattr(b, "type", "") == "text")
    return parse_response(text, n_steps=len(steps),
                          n_ings=len(recipe.get("ingredients") or []))


def _extract_json(text: str):
    """JSON object from the reply, tolerating code fences / stray prose."""
    match = re.search(r"\{[\s\S]*\}", text)
    if not match:
        return None
    try:
        return json.loads(match.group(0))
    except json.JSONDecodeError:
        return None


def parse_response(text: str, n_steps: int, n_ings: int) -> dict[str, list[int]]:
    """Parse + validate Claude's JSON. Out-of-range steps/indices are dropped,
    so a sloppy answer can't highlight a row that doesn't exist."""
    data = _extract_json(text or "")
    raw = data.get("steps") if isinstance(data, dict) else None
    if not isinstance(raw, dict):
        raise StepTagError(f"unexpected response: {(text or '')[:200]!r}")

    out: dict[str, list[int]] = {}
    for key, idxs in raw.items():
        try:
            step = int(key)
        except (TypeError, ValueError):
            continue
        if not 1 <= step <= n_steps or not isinstance(idxs, list):
            continue
        valid = sorted({i for i in idxs
                        if isinstance(i, int) and not isinstance(i, bool) and 0 <= i < n_ings})
        if valid:
            out[str(step)] = valid
    return out


# ---------------------------------------------------------------------------
# Keyword fallback
# ---------------------------------------------------------------------------

# Prep/descriptor words that don't identify an ingredient ("kosher salt" is
# salt, "low-sodium soy sauce" is soy sauce).
_DESCRIPTORS = {
    "fresh", "dried", "frozen", "ground", "kosher", "large", "small", "medium",
    "whole", "plain", "extra", "additional", "chopped", "minced", "sliced",
    "diced", "shredded", "grated", "crushed", "toasted", "boneless", "skinless",
    "low", "less", "sodium", "reduced", "fat", "unsalted", "salted", "raw",
    "cooked", "uncooked", "canned", "jarred", "packed", "optional", "of", "a",
    "and", "or", "for", "the",
}

# Words too generic to identify an ingredient on their own ("the sauce",
# "the oil") — they only count as part of a full phrase like "soy sauce".
_GENERIC = {
    "sauce", "cheese", "powder", "oil", "paste", "juice", "seeds", "leaves",
    "vinegar", "milk", "flakes", "crumbs", "mix", "seasoning", "broth",
    "stock", "wine", "cream", "water", "crowns", "florets", "fillets",
    "breasts", "thighs", "cloves", "pieces", "slices", "bunch",
}


def _core_tokens(name: str) -> list[str]:
    name = re.sub(r"\([^)]*\)", " ", (name or "").lower())
    words = re.findall(r"[a-z]+", name)
    return [w for w in words if w not in _DESCRIPTORS]


def _word_re(tokens: list[str]) -> re.Pattern:
    """Whole-word, plural-tolerant pattern for a token sequence."""
    parts = [re.escape(t.rstrip("s")) + r"(?:s|es)?" for t in tokens]
    return re.compile(r"\b" + r"[\s-]+".join(parts) + r"\b")


def keyword_map(recipe: dict) -> dict[str, list[int]]:
    """Whole-word text match of each ingredient against each step.

    An ingredient matches on its full core phrase ("soy sauce"), or on a
    single distinctive word ("chicken" for "chicken breasts") when no other
    ingredient in the recipe shares that word. A longer phrase claims its
    span first, so "garlic powder" in a step doesn't also light "garlic".
    """
    ings = recipe.get("ingredients") or []
    cores = [_core_tokens(i.get("name", "")) for i in ings]
    word_owners: dict[str, set[int]] = {}
    for idx, toks in enumerate(cores):
        for w in toks:
            word_owners.setdefault(w, set()).add(idx)

    order = sorted(range(len(ings)), key=lambda i: -len(" ".join(cores[i])))
    out: dict[str, list[int]] = {}
    for n, step in enumerate(recipe.get("instructions") or [], start=1):
        text = (step.get("text") or "").lower()
        claimed: list[tuple[int, int, str]] = []  # (start, end, phrase)
        hits: set[int] = set()

        def free(m, phrase):
            return all(m.end() <= s or m.start() >= e or p == phrase
                       for s, e, p in claimed)

        for idx in order:
            toks = cores[idx]
            if not toks:
                continue
            phrase = " ".join(toks)
            found = [m for m in _word_re(toks).finditer(text) if free(m, phrase)]
            if not found and len(toks) > 1:
                for w in toks:
                    if w in _GENERIC or len(w) < 3 or len(word_owners[w]) > 1:
                        continue
                    found = [m for m in _word_re([w]).finditer(text) if free(m, w)]
                    if found:
                        phrase = w
                        break
            if found:
                hits.add(idx)
                claimed.extend((m.start(), m.end(), phrase) for m in found)
        if hits:
            out[str(n)] = sorted(hits)
    return out


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import sys

    from dotenv import load_dotenv

    load_dotenv(override=True)
    from mealplan import library

    if "--backfill" in sys.argv:
        # Tag every recipe whose tags are missing or stale. Re-runnable.
        lib = library.get_all()
        todo = [r for r in lib.values() if stored_map(r) is None]
        print(f"{len(todo)} of {len(lib)} recipes need tagging")
        failed = 0
        for n, r in enumerate(todo, start=1):
            try:
                mapping = claude_map(r)
            except StepTagError as e:
                failed += 1
                print(f"  [{n}/{len(todo)}] ✗ {r.get('title')}: {e}")
                continue
            _save(r, mapping)
            print(f"  [{n}/{len(todo)}] ✓ {r.get('title')}")
        sys.exit(1 if failed else 0)

    if "--show" in sys.argv:
        r = library.get(sys.argv[sys.argv.index("--show") + 1])
        mapping = ensure(r) if r else {}
        ings = (r or {}).get("ingredients") or []
        for n, step in enumerate((r or {}).get("instructions") or [], start=1):
            names = [ings[i].get("name") for i in mapping.get(str(n), [])]
            print(f"{n}. {step.get('text', '')[:70]}\n   -> {names}")
        sys.exit(0)

    print("Usage: python3 -m mealplan.step_tags [--backfill | --show <recipe_id>]")
