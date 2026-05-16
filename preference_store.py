"""
preference_store.py
-------------------
Single source of truth for all persistent SmartCart data.

Persists three logical buckets in the Supabase `kv` table:
    preferences   Saved household product preferences (dict keyed by item)
    staples       Weekly staple items (list)
    session_log   Rolling log of last 10 shopping sessions (list)

Public interface:
    --- Preferences ---
    get_all_preferences() -> dict
    get_preference(item_key) -> dict | None
    save_preference(item_key, product_data, source) -> None
    delete_preference(item_key) -> None

    --- Staples ---
    get_all_staples() -> list
    get_staple(item_key) -> dict | None
    save_staple(staple_data) -> None
    delete_staple(item_key) -> None
    reorder_staples(ordered_keys) -> None

    --- Session Log ---
    append_session_log(session_data) -> None
    get_session_log() -> list

    --- Utilities ---
    normalise_item_key(name) -> str
    data_summary() -> dict
    export_data() -> dict
    import_data(snapshot) -> None
"""

import re
from datetime import datetime, timezone

from supabase_kv import kv_get, kv_put

# ---------------------------------------------------------------------------
# Storage keys
# ---------------------------------------------------------------------------

KEY_PREFERENCES = "preferences"
KEY_STAPLES     = "staples"
KEY_SESSION_LOG = "session_log"

MAX_SESSION_LOG_ENTRIES = 10


def _now_iso() -> str:
    """Current UTC time as ISO 8601 string."""
    return datetime.now(timezone.utc).isoformat()


# ---------------------------------------------------------------------------
# Key normalisation
# ---------------------------------------------------------------------------

def normalise_item_key(name: str) -> str:
    """
    Convert a human-readable item name to a stable, lowercase key.

    Examples:
        "Whole Milk"        -> "whole_milk"
        "2% Milk (organic)" -> "2_milk_organic"
        "chicken breast"    -> "chicken_breast"
        "  Large  Eggs  "   -> "large_eggs"

    This key is used as the dictionary key in preferences.json and
    as the staple identifier in staples.json. Consistent normalisation
    ensures that "Whole Milk" and "whole milk" resolve to the same
    preference entry.
    """
    key = name.lower().strip()
    # Replace any non-alphanumeric characters (spaces, punctuation) with underscores
    key = re.sub(r"[^a-z0-9]+", "_", key)
    # Strip leading/trailing underscores
    key = key.strip("_")
    return key


# ---------------------------------------------------------------------------
# Preferences
# ---------------------------------------------------------------------------

def get_all_preferences() -> dict:
    """
    Returns the full preferences dictionary.
    Keys are normalised item names; values are product preference dicts.

    Returns empty dict if no preferences have been saved yet.

    Structure of each preference entry:
    {
        "item_key":     "whole_milk",
        "kroger_upc":   "0001111041700",
        "product_name": "Kroger Whole Milk",
        "brand":        "Kroger",
        "size":         "1 gallon",
        "price":        4.99,
        "saved_at":     "2026-03-01T12:00:00+00:00",
        "source":       "review"   # or "manual"
    }
    """
    return kv_get(KEY_PREFERENCES, {}) or {}


def get_preference(item_key: str) -> dict | None:
    """
    Returns the saved preference for a single item, or None if not found.
    Accepts either a raw name ("Whole Milk") or an already-normalised key.
    """
    key = normalise_item_key(item_key)
    prefs = get_all_preferences()
    return prefs.get(key)


def save_preference(
    item_key: str,
    product_data: dict,
    source: str = "review",
) -> None:
    """
    Save or update a product preference.

    Args:
        item_key:     Item name (will be normalised) or already-normalised key.
        product_data: Dict with Kroger product details. Required fields:
                      kroger_upc, product_name, brand, size.
                      Optional: price, category.
        source:       "review" (set during a shopping session) or
                      "manual" (set via the Preferences screen).

    The preference is keyed by normalised item name so subsequent sessions
    can look it up regardless of how the item was phrased in the list.
    """
    key = normalise_item_key(item_key)
    prefs = get_all_preferences()

    prefs[key] = {
        "item_key":     key,
        "kroger_upc":   product_data.get("kroger_upc", ""),
        "product_name": product_data.get("product_name", ""),
        "brand":        product_data.get("brand", ""),
        "size":         product_data.get("size", ""),
        "price":        product_data.get("price"),
        "category":     product_data.get("category", ""),
        "saved_at":     _now_iso(),
        "source":       source,
    }

    kv_put(KEY_PREFERENCES, prefs)


def delete_preference(item_key: str) -> bool:
    """
    Remove a preference entry.
    Returns True if the entry existed and was deleted, False if not found.
    """
    key = normalise_item_key(item_key)
    prefs = get_all_preferences()

    if key not in prefs:
        return False

    del prefs[key]
    kv_put(KEY_PREFERENCES, prefs)
    return True


def update_preference_upc(item_key: str, new_upc: str, new_product_data: dict) -> bool:
    """
    Update the linked Kroger product for an existing preference.
    Used by the Preferences screen's Edit flow.
    Returns True if the preference existed and was updated.
    """
    key = normalise_item_key(item_key)
    prefs = get_all_preferences()

    if key not in prefs:
        return False

    prefs[key].update({
        "kroger_upc":   new_upc,
        "product_name": new_product_data.get("product_name", prefs[key]["product_name"]),
        "brand":        new_product_data.get("brand", prefs[key]["brand"]),
        "size":         new_product_data.get("size", prefs[key]["size"]),
        "price":        new_product_data.get("price", prefs[key].get("price")),
        "saved_at":     _now_iso(),
        "source":       "manual",
    })

    kv_put(KEY_PREFERENCES, prefs)
    return True


# ---------------------------------------------------------------------------
# Staples
# ---------------------------------------------------------------------------

def get_all_staples() -> list:
    """
    Returns the staples list, sorted by sort_order.
    Returns empty list if no staples have been saved yet.

    Structure of each staple entry:
    {
        "item_key":        "whole_milk",
        "display_name":    "Whole Milk",
        "default_quantity": 2,
        "preferred_upc":   "0001111041700",  # or null
        "category":        "Dairy",
        "sort_order":      0
    }
    """
    staples = kv_get(KEY_STAPLES, []) or []
    return sorted(staples, key=lambda s: s.get("sort_order", 999))


def get_staple(item_key: str) -> dict | None:
    """Returns a single staple by item_key, or None if not found."""
    key = normalise_item_key(item_key)
    for staple in get_all_staples():
        if staple.get("item_key") == key:
            return staple
    return None


def save_staple(staple_data: dict) -> None:
    """
    Add a new staple or update an existing one (matched by item_key).

    Required fields in staple_data:
        display_name      Human-readable name shown in the UI
        default_quantity  How many to add each session (number)

    Optional fields:
        item_key          Auto-generated from display_name if not provided
        preferred_upc     Linked Kroger product UPC (can be set later)
        category          Display category e.g. "Dairy", "Produce"
        sort_order        Display order (auto-assigned if not provided)
    """
    staples = get_all_staples()

    # Generate item_key from display_name if not provided
    if "item_key" not in staple_data or not staple_data["item_key"]:
        staple_data["item_key"] = normalise_item_key(staple_data.get("display_name", "unknown"))

    key = staple_data["item_key"]

    # Auto-assign sort_order if not provided (append to end)
    if "sort_order" not in staple_data:
        max_order = max((s.get("sort_order", 0) for s in staples), default=-1)
        staple_data["sort_order"] = max_order + 1

    # Ensure required fields have defaults
    staple_data.setdefault("preferred_upc", None)
    staple_data.setdefault("category", "Other")
    staple_data.setdefault("default_quantity", 1)

    # Update existing or append new
    existing_index = next(
        (i for i, s in enumerate(staples) if s.get("item_key") == key), None
    )
    if existing_index is not None:
        # Preserve sort_order if not explicitly changing it
        if "sort_order" not in staple_data:
            staple_data["sort_order"] = staples[existing_index].get("sort_order", 0)
        staples[existing_index] = staple_data
    else:
        staples.append(staple_data)

    kv_put(KEY_STAPLES, staples)


def delete_staple(item_key: str) -> bool:
    """
    Remove a staple entry.
    Returns True if found and deleted, False if not found.
    """
    key = normalise_item_key(item_key)
    staples = get_all_staples()
    new_staples = [s for s in staples if s.get("item_key") != key]

    if len(new_staples) == len(staples):
        return False  # Nothing was removed

    kv_put(KEY_STAPLES, new_staples)
    return True


def reorder_staples(ordered_keys: list[str]) -> None:
    """
    Update sort_order for all staples based on a new ordered list of keys.
    Used by the drag-to-reorder UI in the Staples screen.

    Args:
        ordered_keys: List of item_key strings in the desired display order.
    """
    staples = get_all_staples()
    key_to_staple = {s["item_key"]: s for s in staples}

    reordered = []
    for i, key in enumerate(ordered_keys):
        if key in key_to_staple:
            key_to_staple[key]["sort_order"] = i
            reordered.append(key_to_staple[key])

    # Append any staples that weren't in ordered_keys (shouldn't happen, but safe)
    included_keys = set(ordered_keys)
    for staple in staples:
        if staple["item_key"] not in included_keys:
            reordered.append(staple)

    kv_put(KEY_STAPLES, reordered)


def link_staple_to_preference(item_key: str, upc: str) -> bool:
    """
    Set the preferred_upc on a staple entry.
    Called when a user confirms a product choice for a staple during review.
    Returns True if the staple was found and updated.
    """
    key = normalise_item_key(item_key)
    staples = get_all_staples()

    for staple in staples:
        if staple.get("item_key") == key:
            staple["preferred_upc"] = upc
            kv_put(KEY_STAPLES, staples)
            return True

    return False


# ---------------------------------------------------------------------------
# Session log
# ---------------------------------------------------------------------------

def append_session_log(session_data: dict) -> None:
    """
    Add a session summary to the rolling log.
    Keeps only the most recent MAX_SESSION_LOG_ENTRIES entries.

    Expected session_data fields:
        items_added     int   Number of items confirmed and added to cart
        items_skipped   int   Number of items the user skipped
        items_not_found int   Number of items with no Kroger match
        new_preferences int   Number of new preferences saved this session
        estimated_total float Estimated cart total in dollars (optional)
    """
    log = kv_get(KEY_SESSION_LOG, []) or []

    entry = {
        "date":             _now_iso(),
        "items_added":      session_data.get("items_added", 0),
        "items_skipped":    session_data.get("items_skipped", 0),
        "items_not_found":  session_data.get("items_not_found", 0),
        "new_preferences":  session_data.get("new_preferences", 0),
        "estimated_total":  session_data.get("estimated_total"),
    }

    log.append(entry)

    # Keep only the most recent entries
    if len(log) > MAX_SESSION_LOG_ENTRIES:
        log = log[-MAX_SESSION_LOG_ENTRIES:]

    kv_put(KEY_SESSION_LOG, log)


def get_session_log() -> list:
    """
    Returns the session log as a list, most recent entry last.
    Returns empty list if no sessions have been logged yet.
    """
    return kv_get(KEY_SESSION_LOG, []) or []


# ---------------------------------------------------------------------------
# Utilities
# ---------------------------------------------------------------------------

def data_summary() -> dict:
    """
    Returns a summary of current stored data.
    Used by the home screen to show counts (e.g. "14 staples on file").
    """
    prefs = get_all_preferences()
    staples = get_all_staples()
    log = get_session_log()

    return {
        "preference_count": len(prefs),
        "staple_count":     len(staples),
        "session_count":    len(log),
        "last_session":     log[-1]["date"] if log else None,
    }


def export_data() -> dict:
    """
    Returns all stored data as a single dict.
    Useful for backup or debugging.
    """
    return {
        "preferences": get_all_preferences(),
        "staples":     get_all_staples(),
        "session_log": get_session_log(),
        "exported_at": _now_iso(),
        "schema":      1,
    }


def import_data(snapshot: dict, replace: bool = True) -> dict:
    """
    Restore from a snapshot produced by export_data().
    By default replaces all three buckets. Returns counts of what was loaded.
    """
    counts = {"preferences": 0, "staples": 0, "session_log": 0}

    if "preferences" in snapshot and isinstance(snapshot["preferences"], dict):
        kv_put(KEY_PREFERENCES, snapshot["preferences"])
        counts["preferences"] = len(snapshot["preferences"])

    if "staples" in snapshot and isinstance(snapshot["staples"], list):
        kv_put(KEY_STAPLES, snapshot["staples"])
        counts["staples"] = len(snapshot["staples"])

    if "session_log" in snapshot and isinstance(snapshot["session_log"], list):
        kv_put(KEY_SESSION_LOG, snapshot["session_log"])
        counts["session_log"] = len(snapshot["session_log"])

    return counts


# ---------------------------------------------------------------------------
# CLI entry point — run this file directly to inspect stored data
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import json
    import sys

    print("SmartCart — Preference Store")
    print("-" * 40)

    summary = data_summary()
    print(f"Preferences saved: {summary['preference_count']}")
    print(f"Staples on file:   {summary['staple_count']}")
    print(f"Sessions logged:   {summary['session_count']}")
    if summary["last_session"]:
        print(f"Last session:      {summary['last_session']}")

    if "--dump" in sys.argv:
        print("\n--- Full Data Dump ---")
        print(json.dumps(export_data(), indent=2))

    if "--test" in sys.argv:
        print("\n--- Running self-test ---")

        # Test preference save/get/delete
        test_key = "test_item_whole_milk"
        save_preference(test_key, {
            "kroger_upc": "0001234567890",
            "product_name": "Test Milk 1 Gallon",
            "brand": "TestBrand",
            "size": "1 gallon",
            "price": 4.99,
        }, source="manual")
        pref = get_preference(test_key)
        assert pref is not None, "Preference not found after save"
        assert pref["brand"] == "TestBrand", "Brand mismatch"
        deleted = delete_preference(test_key)
        assert deleted, "Delete returned False"
        assert get_preference(test_key) is None, "Preference still exists after delete"
        print("✓ Preferences: save, get, delete")

        # Test key normalisation
        assert normalise_item_key("Whole Milk") == "whole_milk"
        assert normalise_item_key("  Large  Eggs  ") == "large_eggs"
        assert normalise_item_key("2% Milk (organic)") == "2_milk_organic"
        print("✓ Key normalisation")

        # Test staple save/get/delete
        save_staple({
            "display_name": "Test Staple Eggs",
            "default_quantity": 2,
            "category": "Dairy",
        })
        staple = get_staple("test_staple_eggs")
        assert staple is not None, "Staple not found after save"
        assert staple["default_quantity"] == 2, "Quantity mismatch"
        delete_staple("test_staple_eggs")
        assert get_staple("test_staple_eggs") is None, "Staple still exists after delete"
        print("✓ Staples: save, get, delete")

        # Test session log
        append_session_log({
            "items_added": 18,
            "items_skipped": 2,
            "items_not_found": 0,
            "new_preferences": 3,
            "estimated_total": 87.50,
        })
        log = get_session_log()
        assert len(log) > 0, "Session log empty after append"
        assert log[-1]["items_added"] == 18, "Session data mismatch"
        print("✓ Session log: append, get")

        print("\n✓ All tests passed.")
