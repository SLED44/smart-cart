"""
mealplan/library.py
-------------------
Recipe library CRUD over the existing Supabase ``kv`` table.

Backed by a single ``recipe_library`` KV key — an object mapping
``recipe_id`` → Recipe (schema per PRD §7.2).

Public interface:
    get(recipe_id)                    -> dict | None
    get_all()                         -> dict[str, dict]
    save(recipe)                      -> str   (returns the recipe_id used)
    delete(recipe_id)                 -> bool
    all_active()                      -> list[dict]
    filter(...)                       -> list[dict]
    find_by_source(source, source_id) -> dict | None
    data_summary()                    -> dict
    slugify(text)                     -> str

``save()`` is idempotent: if a Spoonacular recipe with the same
``source + source_id`` is already in the library, the existing entry is
updated in place and the existing ``recipe_id`` is returned. User-managed
fields (``user_notes``, ``times_cooked``, ``last_cooked_at``, ``status``,
``added_at``) are NEVER overwritten by a re-save — they're protected so
re-running the Spoonacular bootstrap is safe.
"""

import re
from datetime import datetime, timezone

from supabase_kv import kv_get, kv_put

KEY_RECIPE_LIBRARY = "recipe_library"

# User-managed fields that a re-save MUST NOT clobber.
_PROTECTED_FIELDS = (
    "user_notes",
    "times_cooked",
    "last_cooked_at",
    "status",
    "added_at",
    "rating",       # 1-5 star rating (None = unrated); durable, survives regen
    "rated_at",
)

# Status values, per PRD §7.2 (+ "retired", added 2026-06-12 for library
# curation: out of rotation like never_again, but means "cut for fit/quality,
# restorable" rather than "user hated it" — keeps dietitian stats honest).
STATUS_ACTIVE = "active"
STATUS_FAVORITE = "favorite"
STATUS_NEVER_AGAIN = "never_again"
STATUS_RETIRED = "retired"

VALID_STATUSES = (STATUS_ACTIVE, STATUS_FAVORITE, STATUS_NEVER_AGAIN, STATUS_RETIRED)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def slugify(text: str) -> str:
    """
    Lowercase, alphanumeric-only slug with underscore separators.
    Used to derive recipe IDs from titles when no ID is supplied.

        "Classic Smash Burgers"   -> "classic_smash_burgers"
        "Miso-Glazed Salmon Soba" -> "miso_glazed_salmon_soba"
    """
    slug = text.lower().strip()
    slug = re.sub(r"[^a-z0-9]+", "_", slug)
    return slug.strip("_")


# ---------------------------------------------------------------------------
# Raw bucket access
# ---------------------------------------------------------------------------

def get_all() -> dict:
    """Return the full ``recipe_id -> Recipe`` map. Empty dict if unset."""
    return kv_get(KEY_RECIPE_LIBRARY, {}) or {}


def _put_all(library: dict) -> None:
    kv_put(KEY_RECIPE_LIBRARY, library)


def get(recipe_id: str) -> dict | None:
    """Return one recipe by id, or None if missing."""
    return get_all().get(recipe_id)


# ---------------------------------------------------------------------------
# Lookups
# ---------------------------------------------------------------------------

def find_by_source(source: str, source_id: str) -> dict | None:
    """Locate a recipe by its origin (e.g. source='spoonacular', source_id='12345')."""
    if not source or not source_id:
        return None
    for recipe in get_all().values():
        if recipe.get("source") == source and str(recipe.get("source_id")) == str(source_id):
            return recipe
    return None


# ---------------------------------------------------------------------------
# Save (idempotent on source+source_id)
# ---------------------------------------------------------------------------

def save(recipe: dict) -> str:
    """
    Upsert a recipe and return the recipe_id it was stored under.

    ID resolution order:
        1. If ``recipe["id"]`` is set, that wins.
        2. Else if ``source+source_id`` matches an existing entry, reuse its id.
        3. Else generate a default id:
             spoonacular  -> ``sp_<source_id>``
             user_manual  -> ``user_<epoch>_<slug>``
             claude_chat  -> ``cc_<epoch>_<slug>``

    Protected fields (``user_notes``, ``times_cooked``, ``last_cooked_at``,
    ``status``, ``added_at``) on the existing entry are preserved unless the
    incoming recipe explicitly overrides them with a non-empty value.
    """
    library = get_all()

    recipe_id = recipe.get("id")
    existing = None

    if recipe_id and recipe_id in library:
        existing = library[recipe_id]
    elif not recipe_id:
        # Try source+source_id dedup before falling back to a generated id.
        match = find_by_source(recipe.get("source", ""), recipe.get("source_id", ""))
        if match:
            recipe_id = match["id"]
            existing = match
        else:
            recipe_id = _generate_id(recipe)

    merged = _merge(existing, recipe, recipe_id)
    library[recipe_id] = merged
    _put_all(library)
    return recipe_id


def _generate_id(recipe: dict) -> str:
    source = recipe.get("source", "")
    sid = recipe.get("source_id", "")
    title = recipe.get("title", "")
    if source == "spoonacular" and sid:
        return f"sp_{sid}"
    slug = slugify(title) or "untitled"
    prefix = {"claude_chat": "cc", "user_manual": "user"}.get(source, "lib")
    # Epoch helps distinguish re-pastes of the same title.
    epoch = int(datetime.now(timezone.utc).timestamp())
    return f"{prefix}_{epoch}_{slug}"


def _merge(existing: dict | None, incoming: dict, recipe_id: str) -> dict:
    """
    Build the record to persist. ``incoming`` wins for canonical recipe data
    (title, ingredients, image, etc.); ``existing`` wins for user-managed
    fields unless the caller explicitly supplied a non-empty replacement.
    """
    merged = dict(existing) if existing else {}
    for k, v in incoming.items():
        if k in _PROTECTED_FIELDS:
            continue
        merged[k] = v

    # Protected fields: preserve existing, or fall back to incoming, or default.
    if existing:
        for field in _PROTECTED_FIELDS:
            if field in incoming and _is_meaningful(incoming[field]):
                merged[field] = incoming[field]
            else:
                merged[field] = existing.get(field, _default_for(field))
    else:
        for field in _PROTECTED_FIELDS:
            merged[field] = incoming.get(field, _default_for(field))

    merged["id"] = recipe_id
    if not merged.get("added_at"):
        merged["added_at"] = _now_iso()
    if merged.get("status") not in VALID_STATUSES:
        merged["status"] = STATUS_ACTIVE
    return merged


def _is_meaningful(value) -> bool:
    """A protected field is only overwritten when the new value is truthy."""
    if value is None:
        return False
    if isinstance(value, str) and not value.strip():
        return False
    if isinstance(value, (int, float)) and value == 0:
        return False
    return True


def _default_for(field: str):
    return {
        "user_notes":      "",
        "times_cooked":    0,
        "last_cooked_at":  None,
        "status":          STATUS_ACTIVE,
        "added_at":        None,
        "rating":          None,
        "rated_at":        None,
    }[field]


# ---------------------------------------------------------------------------
# Mutations
# ---------------------------------------------------------------------------

def delete(recipe_id: str) -> bool:
    """Remove a recipe. Returns True iff it existed."""
    library = get_all()
    if recipe_id not in library:
        return False
    del library[recipe_id]
    _put_all(library)
    return True


def set_status(recipe_id: str, status: str) -> bool:
    """Update only the status field. Returns False if recipe missing."""
    if status not in VALID_STATUSES:
        raise ValueError(f"invalid status {status!r}; must be one of {VALID_STATUSES}")
    library = get_all()
    if recipe_id not in library:
        return False
    library[recipe_id]["status"] = status
    _put_all(library)
    return True


def set_rating(recipe_id: str, stars: int | None) -> bool:
    """Set (1-5) or clear (None) a recipe's star rating. Stamps rated_at.
    Returns False if the recipe is missing or stars is out of range."""
    if stars is not None and stars not in (1, 2, 3, 4, 5):
        raise ValueError(f"stars must be 1-5 or None, got {stars!r}")
    library = get_all()
    if recipe_id not in library:
        return False
    r = library[recipe_id]
    r["rating"] = stars
    r["rated_at"] = _now_iso() if stars is not None else None
    _put_all(library)
    return True


def record_cooked(recipe_id: str, notes: str | None = None) -> bool:
    """Increment times_cooked, stamp last_cooked_at, optionally replace user_notes."""
    library = get_all()
    if recipe_id not in library:
        return False
    r = library[recipe_id]
    r["times_cooked"] = int(r.get("times_cooked") or 0) + 1
    r["last_cooked_at"] = _now_iso()
    if notes is not None:
        r["user_notes"] = notes
    _put_all(library)
    return True


# ---------------------------------------------------------------------------
# Queries
# ---------------------------------------------------------------------------

def all_active() -> list[dict]:
    """All recipes the planner is allowed to consider (excludes never_again
    and retired)."""
    return [
        r for r in get_all().values()
        if r.get("status") not in (STATUS_NEVER_AGAIN, STATUS_RETIRED)
    ]


def filter(
    cuisine: str | list[str] | None = None,
    protein: str | list[str] | None = None,
    status: str | list[str] | None = None,
    name_search: str | None = None,
) -> list[dict]:
    """
    Return recipes matching every supplied filter.

    Each filter may be a single string or a list. A None filter is ignored.
    ``name_search`` is a case-insensitive substring match on title.
    """
    cuisines = _as_set(cuisine)
    proteins = _as_set(protein)
    statuses = _as_set(status)
    needle = name_search.strip().lower() if name_search else ""

    out = []
    for r in get_all().values():
        if statuses and r.get("status", STATUS_ACTIVE) not in statuses:
            continue
        if cuisines and not (cuisines & {c.lower() for c in r.get("cuisines", [])}):
            continue
        if proteins and not (proteins & {p.lower() for p in r.get("proteins", [])}):
            continue
        if needle and needle not in (r.get("title", "").lower()):
            continue
        out.append(r)
    return out


def _as_set(v) -> set[str]:
    if v is None:
        return set()
    if isinstance(v, str):
        return {v.lower()}
    return {str(x).lower() for x in v}


# ---------------------------------------------------------------------------
# Summary
# ---------------------------------------------------------------------------

def data_summary() -> dict:
    """Counts by status, cuisine, protein. Used by bootstrap + rules screens."""
    lib = get_all()
    by_status = {STATUS_ACTIVE: 0, STATUS_FAVORITE: 0, STATUS_NEVER_AGAIN: 0}
    by_cuisine: dict[str, int] = {}
    by_protein: dict[str, int] = {}
    for r in lib.values():
        s = r.get("status", STATUS_ACTIVE)
        by_status[s] = by_status.get(s, 0) + 1
        for c in r.get("cuisines", []) or []:
            by_cuisine[c.lower()] = by_cuisine.get(c.lower(), 0) + 1
        for p in r.get("proteins", []) or []:
            by_protein[p.lower()] = by_protein.get(p.lower(), 0) + 1
    return {
        "total":      len(lib),
        "by_status":  by_status,
        "by_cuisine": dict(sorted(by_cuisine.items(), key=lambda kv: -kv[1])),
        "by_protein": dict(sorted(by_protein.items(), key=lambda kv: -kv[1])),
    }


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import json
    import sys

    if "--test" in sys.argv:
        print("--- library round-trip self-test ---")
        # Round-trip a synthetic recipe and verify protected-field semantics.
        test = {
            "source": "spoonacular",
            "source_id": "__library_smoke_test__",
            "title": "Smoke Test Recipe",
            "cuisines": ["italian"],
            "proteins": ["chicken"],
            "carbs": ["pasta"],
            "ingredients": [{"name": "pasta", "amount": 1, "unit": "lb", "aisle": "Pantry"}],
            "instructions": [{"step_number": 1, "text": "Cook it."}],
            "servings_original": 4,
            "ready_in_minutes": 30,
            "image_url": "",
        }
        rid = save(test)
        assert rid == "sp___library_smoke_test__", f"unexpected id {rid}"
        r = get(rid)
        assert r and r["title"] == "Smoke Test Recipe"
        assert r["status"] == STATUS_ACTIVE
        assert r["times_cooked"] == 0
        assert r["added_at"]
        # Cook it.
        record_cooked(rid, notes="too salty next time")
        r2 = get(rid)
        assert r2["times_cooked"] == 1, r2
        assert r2["user_notes"] == "too salty next time"
        # Re-save — must NOT clobber user_notes / times_cooked.
        save({**test, "title": "Smoke Test Recipe (v2)"})
        r3 = get(rid)
        assert r3["title"] == "Smoke Test Recipe (v2)", "canonical fields should update"
        assert r3["user_notes"] == "too salty next time", "user_notes must be preserved"
        assert r3["times_cooked"] == 1, "times_cooked must be preserved"
        assert r3["added_at"] == r["added_at"], "added_at must be preserved"
        # Status round-trip.
        set_status(rid, STATUS_FAVORITE)
        assert get(rid)["status"] == STATUS_FAVORITE
        # Filter.
        hits = filter(cuisine="italian", protein="chicken")
        assert any(h["id"] == rid for h in hits)
        # Cleanup.
        assert delete(rid)
        assert get(rid) is None
        print("✓ library self-test passed")

    if "--stats" in sys.argv:
        print(json.dumps(data_summary(), indent=2))

    if "--dump" in sys.argv:
        print(json.dumps(get_all(), indent=2))

    if "--get" in sys.argv:
        idx = sys.argv.index("--get")
        rid = sys.argv[idx + 1]
        print(json.dumps(get(rid), indent=2))

    if "--delete" in sys.argv:
        idx = sys.argv.index("--delete")
        rid = sys.argv[idx + 1]
        print("deleted" if delete(rid) else "not found")

    if len(sys.argv) == 1:
        print("Usage: python3 mealplan/library.py [--test|--stats|--dump|--get <id>|--delete <id>]")
