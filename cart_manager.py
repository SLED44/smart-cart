"""
cart_manager.py
---------------
Handles posting confirmed grocery items to the authenticated user's
Kroger cart via the cart.basic:write API scope.

Called once per session after the item-by-item review is complete.

Public interface:
    post_to_cart(confirmed_items) -> CartResult
    retry_failed_items(failed_items) -> CartResult

CartResult structure:
    {
        "succeeded":        list,   Items successfully added to cart
        "failed":           list,   Items that failed with error detail
        "success_count":    int,
        "failure_count":    int,
        "estimated_total":  float,  Sum of prices for succeeded items
        "cart_url":         str,    Direct link to Kroger cart
    }

Each item in succeeded/failed retains all original confirmed item fields,
plus:
    "cart_status":  "added" | "failed"
    "cart_error":   str | None   (error message if failed)

Kroger cart endpoint (PRD 7.4):
    PUT /v1/cart/add
    Scope: cart.basic:write
    Body: { "items": [{ "upc": "...", "quantity": N }, ...] }

Open Question #2 from PRD:
    Does cart.basic:write support quantity per item, or must quantities
    be passed as separate add calls? This module handles both cases:
    - First attempts batch with quantity field
    - Falls back to repeated single-item calls if quantity is rejected
"""

import os
import time

import requests
from dotenv import load_dotenv

from kroger_auth import get_valid_token
from preference_store import append_run_log, append_session_log
from applog import get_logger

_log = get_logger(__name__)

load_dotenv(override=True)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

KROGER_CART_URL = "https://api.kroger.com/v1/cart/add"
CITY_MARKET_CART_URL = "https://www.citymarket.com/cart"
KROGER_CART_FALLBACK_URL = "https://www.kroger.com/cart"

# Delay between individual cart calls when falling back to one-at-a-time
CART_CALL_DELAY = 0.3

# Max items per batch request — Kroger's documented limit
BATCH_SIZE = 50

# ---------------------------------------------------------------------------
# Cart API helpers
# ---------------------------------------------------------------------------

def _build_cart_items(confirmed_items: list) -> list[dict]:
    """
    Convert confirmed SmartCart items to Kroger cart API format.

    Each confirmed item must have:
        primary.upc   The Kroger UPC to add
        quantity      How many to add

    Quantity is rounded to the nearest integer (Kroger cart items
    are whole units). Minimum quantity of 1 enforced.
    """
    cart_items = []
    for item in confirmed_items:
        primary = item.get("primary")
        if not primary:
            continue

        upc = primary.get("upc", "").strip()
        if not upc:
            continue

        quantity = max(1, round(item.get("quantity", 1)))
        _log.info("CART %r: qty=%s upc=%s (%r, %s, soldBy=%s)",
                  item.get("item_name", "?"), quantity, upc,
                  primary.get("product_name", "?"), primary.get("size", ""),
                  primary.get("sold_by", ""))
        cart_items.append({"upc": upc, "quantity": quantity})

    return cart_items


def _post_batch(cart_items: list, token: str) -> tuple[list, list, list]:
    """
    Attempt to post a batch of items to the Kroger cart.

    Returns (succeeded_upcs, failed_items, qty_dropped_upcs) where:
        succeeded_upcs:   list of UPCs that were accepted
        failed_items:     list of dicts with upc, quantity, error
        qty_dropped_upcs: accepted only after re-posting WITHOUT a quantity,
                          so Kroger added 1 regardless of what was asked

    Handles the PRD Open Question #2 ambiguity:
    - Tries batch with quantity field first
    - If that fails with a 4xx, falls back to individual calls
    """
    if not cart_items:
        return [], [], []

    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type":  "application/json",
        "Accept":        "application/json",
    }

    payload = {"items": cart_items}

    try:
        response = requests.put(
            KROGER_CART_URL,
            json=payload,
            headers=headers,
            timeout=30,
        )
    except requests.RequestException as e:
        # Network-level failure — mark all items as failed
        _log.warning("CART batch of %d: request failed: %s", len(cart_items), e)
        return [], [
            {"upc": i["upc"], "quantity": i["quantity"], "error": str(e)}
            for i in cart_items
        ], []

    _log.info("CART batch of %d: HTTP %s %s", len(cart_items),
              response.status_code, response.text[:300])

    if response.ok:
        # Batch succeeded — all items accepted
        return [i["upc"] for i in cart_items], [], []

    if response.status_code == 400:
        # Batch format may be unsupported — try one at a time
        _log.warning("CART batch returned 400 — falling back to individual item posts")
        return _post_individually(cart_items, token)

    if response.status_code == 401:
        # Token issue — shouldn't happen with our refresh logic
        return [], [
            {"upc": i["upc"], "quantity": i["quantity"],
             "error": "Authorization error — please restart the app to re-authorize."}
            for i in cart_items
        ], []

    # Other error — mark all as failed with status detail
    error_msg = f"Kroger API error {response.status_code}"
    try:
        detail = response.json()
        if "errors" in detail:
            error_msg += f": {detail['errors']}"
    except Exception:
        pass

    return [], [
        {"upc": i["upc"], "quantity": i["quantity"], "error": error_msg}
        for i in cart_items
    ], []


def _post_individually(cart_items: list, token: str) -> tuple[list, list, list]:
    """
    Fall back: post each item to the Kroger cart one at a time.
    Used when batch posting fails or is unsupported.
    """
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type":  "application/json",
        "Accept":        "application/json",
    }

    succeeded_upcs = []
    failed_items = []
    qty_dropped_upcs = []

    for cart_item in cart_items:
        upc = cart_item["upc"]
        quantity = cart_item["quantity"]

        # Try with quantity first
        payload = {"items": [{"upc": upc, "quantity": quantity}]}

        try:
            response = requests.put(
                KROGER_CART_URL,
                json=payload,
                headers=headers,
                timeout=15,
            )
        except requests.RequestException as e:
            failed_items.append({"upc": upc, "quantity": quantity, "error": str(e)})
            time.sleep(CART_CALL_DELAY)
            continue

        if response.ok:
            succeeded_upcs.append(upc)
        elif response.status_code == 400:
            # Try without quantity field (some Kroger API versions don't support it).
            # Kroger then adds exactly 1, so this is recorded as a quantity drop
            # and surfaced on the summary screen rather than passing silently.
            _log.warning("CART upc=%s qty=%s rejected (400): %s — retrying WITHOUT quantity (adds 1)",
                         upc, quantity, response.text[:300])
            payload_no_qty = {"items": [{"upc": upc}]}
            try:
                retry = requests.put(
                    KROGER_CART_URL,
                    json=payload_no_qty,
                    headers=headers,
                    timeout=15,
                )
                if retry.ok:
                    succeeded_upcs.append(upc)
                    if quantity != 1:
                        qty_dropped_upcs.append(upc)
                else:
                    error_msg = f"Error {retry.status_code}"
                    _log.warning("CART upc=%s no-qty retry failed: HTTP %s %s",
                                 upc, retry.status_code, retry.text[:300])
                    failed_items.append({"upc": upc, "quantity": quantity, "error": error_msg})
            except requests.RequestException as e:
                failed_items.append({"upc": upc, "quantity": quantity, "error": str(e)})
        else:
            error_msg = f"Error {response.status_code}"
            _log.warning("CART upc=%s qty=%s failed: HTTP %s %s",
                         upc, quantity, response.status_code, response.text[:300])
            failed_items.append({"upc": upc, "quantity": quantity, "error": error_msg})

        time.sleep(CART_CALL_DELAY)

    return succeeded_upcs, failed_items, qty_dropped_upcs


# ---------------------------------------------------------------------------
# Result assembly
# ---------------------------------------------------------------------------

def _build_cart_result(
    confirmed_items: list,
    succeeded_upcs: list,
    failed_cart_items: list,
    qty_dropped_upcs: list,
) -> dict:
    """
    Build the CartResult dict from raw API outcomes.
    Matches API results back to original confirmed items for display.
    """
    succeeded_upc_set = set(succeeded_upcs)
    qty_dropped_set = set(qty_dropped_upcs)
    failed_upc_map = {f["upc"]: f["error"] for f in failed_cart_items}

    succeeded = []
    failed = []
    estimated_total = 0.0

    for item in confirmed_items:
        primary = item.get("primary")
        if not primary:
            # Item had no product — shouldn't be in confirmed list but handle safely
            continue

        upc = primary.get("upc", "")
        item_result = {**item}

        if upc in succeeded_upc_set:
            item_result["cart_status"] = "added"
            item_result["cart_error"]  = None
            # Kroger accepted it only without a quantity, so it holds 1.
            # Keyed by UPC, so an item that asked for exactly 1 isn't flagged
            # just because another line shared its UPC.
            item_result["qty_dropped"] = (upc in qty_dropped_set
                                          and max(1, round(item.get("quantity", 1))) != 1)
            succeeded.append(item_result)

            # Add to estimated total
            price = primary.get("promo_price") or primary.get("price")
            if price:
                quantity = 1 if item_result["qty_dropped"] else max(1, round(item.get("quantity", 1)))
                estimated_total += price * quantity

        else:
            error = failed_upc_map.get(upc, "Unknown error")
            item_result["cart_status"] = "failed"
            item_result["cart_error"]  = error
            failed.append(item_result)

    return {
        "succeeded":       succeeded,
        "failed":          failed,
        "success_count":   len(succeeded),
        "failure_count":   len(failed),
        "estimated_total": round(estimated_total, 2),
        "cart_url":        CITY_MARKET_CART_URL,
    }


# ---------------------------------------------------------------------------
# Public interface
# ---------------------------------------------------------------------------

def post_to_cart(confirmed_items: list) -> dict:
    """
    Post all confirmed items to the authenticated user's Kroger cart.

    Args:
        confirmed_items: List of matched item dicts that the user confirmed
                         during the review screen. Each must have a
                         primary.upc field and a quantity field.

    Returns:
        CartResult dict (see module docstring).

    Does not raise — failures are captured in the result's "failed" list
    so the UI can surface them for individual retry.
    """
    if not confirmed_items:
        return {
            "succeeded":       [],
            "failed":          [],
            "success_count":   0,
            "failure_count":   0,
            "estimated_total": 0.0,
            "cart_url":        CITY_MARKET_CART_URL,
        }

    print(f"Posting {len(confirmed_items)} items to Kroger cart...")

    # Get a fresh token
    try:
        token = get_valid_token()
    except RuntimeError as e:
        # Token failure — mark everything as failed
        return {
            "succeeded":       [],
            "failed":          [
                {**item, "cart_status": "failed", "cart_error": str(e)}
                for item in confirmed_items
            ],
            "success_count":   0,
            "failure_count":   len(confirmed_items),
            "estimated_total": 0.0,
            "cart_url":        CITY_MARKET_CART_URL,
        }

    # Build the cart item list
    cart_items = _build_cart_items(confirmed_items)

    if not cart_items:
        print("  ⚠ No valid UPCs found in confirmed items.")
        return {
            "succeeded":       [],
            "failed":          confirmed_items,
            "success_count":   0,
            "failure_count":   len(confirmed_items),
            "estimated_total": 0.0,
            "cart_url":        CITY_MARKET_CART_URL,
        }

    # Post in batches (handles lists larger than BATCH_SIZE)
    all_succeeded_upcs = []
    all_failed_items = []
    all_qty_dropped = []

    for i in range(0, len(cart_items), BATCH_SIZE):
        batch = cart_items[i:i + BATCH_SIZE]
        succeeded_upcs, failed, qty_dropped = _post_batch(batch, token)
        all_succeeded_upcs.extend(succeeded_upcs)
        all_failed_items.extend(failed)
        all_qty_dropped.extend(qty_dropped)

    # Build result
    result = _build_cart_result(confirmed_items, all_succeeded_upcs,
                                all_failed_items, all_qty_dropped)

    print(f"Cart post complete: "
          f"{result['success_count']} added, "
          f"{result['failure_count']} failed.")

    return result


def retry_failed_items(failed_items: list) -> dict:
    """
    Retry posting a subset of items that failed in the initial cart post.
    Used by the session summary screen's "Retry Failed" button.

    Args:
        failed_items: The "failed" list from a previous CartResult.

    Returns:
        A new CartResult for just the retried items.
    """
    print(f"Retrying {len(failed_items)} failed items...")
    return post_to_cart(failed_items)


# ---------------------------------------------------------------------------
# Session logging
# ---------------------------------------------------------------------------

def log_completed_session(
    cart_result: dict,
    new_preferences_count: int = 0,
    skipped_items: list | None = None,
    not_found_items: list | None = None,
    raw_text: str = "",
) -> None:
    """
    Write a session summary to the rolling session log.
    Called by main.py after the cart post completes.

    Args:
        cart_result:            Output of post_to_cart()
        new_preferences_count:  How many new preferences were saved this session
        skipped_items:          Items the user skipped during review
        not_found_items:        Items with no Kroger match
    """
    append_session_log({
        "items_added":      cart_result["success_count"],
        "items_skipped":    len(skipped_items or []),
        "items_not_found":  len(not_found_items or []),
        "new_preferences":  new_preferences_count,
        "estimated_total":  cart_result["estimated_total"],
    })
    # Item-level trace, persisted so a bad order can be diagnosed afterwards
    # (Streamlit Cloud's stdout log is gone after a container recycle). Never
    # let a trace failure break the checkout flow.
    try:
        append_run_log(_build_run_trace(
            cart_result, skipped_items or [], not_found_items or [], raw_text))
    except Exception as e:
        _log.warning("run_log write failed: %s", e)


def _trace_row(item: dict, outcome: str) -> dict:
    """One item's journey: parsed ask -> matched product -> what hit the cart."""
    p = item.get("primary") or {}
    return {
        "item":         item.get("item_name"),
        "outcome":      outcome,
        "requested":    item.get("requested_quantity"),
        "unit":         item.get("unit", ""),
        "notes":        item.get("notes", ""),
        "qty_default":  item.get("qty_default"),
        "qty_sent":     max(1, round(item.get("quantity", 1))) if p else None,
        "qty_in_cart":  (1 if item.get("qty_dropped") else max(1, round(item.get("quantity", 1))))
                        if outcome == "added" else 0,
        "qty_edited":   item.get("qty_user_edited", False),
        "qty_dropped":  item.get("qty_dropped", False),
        "swapped":      item.get("swapped", False),
        "match_type":   item.get("match_type"),
        "upc":          p.get("upc"),
        "product":      p.get("product_name"),
        "size":         p.get("size"),
        "sold_by":      p.get("sold_by"),
        "cart_error":   item.get("cart_error"),
    }


def _build_run_trace(cart_result: dict, skipped: list, not_found: list,
                     raw_text: str) -> dict:
    rows = [_trace_row(i, "added") for i in cart_result.get("succeeded", [])]
    rows += [_trace_row(i, "cart_failed") for i in cart_result.get("failed", [])]
    rows += [_trace_row(i, "skipped") for i in skipped]
    rows += [_trace_row(i, "not_found") for i in not_found]
    return {"raw_text": raw_text, "items": rows}


# ---------------------------------------------------------------------------
# Display helpers
# ---------------------------------------------------------------------------

def format_estimated_total(amount: float) -> str:
    """Format an estimated cart total for display."""
    return f"${amount:.2f}"


def get_cart_url() -> str:
    """Returns the City Market cart URL for the 'Open Cart' button."""
    return CITY_MARKET_CART_URL


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import sys

    print("SmartCart — Cart Manager")
    print("-" * 40)
    print("This module posts confirmed items to your Kroger cart.")
    print("It cannot be meaningfully tested in isolation without")
    print("real matched items from the product matcher.")
    print()
    print("To test cart posting, run a full end-to-end session via:")
    print("  streamlit run main.py")
    print()
    print("Or to verify your Kroger token is valid, run:")
    print("  python3 kroger_auth.py")

    if "--check-token" in sys.argv:
        from kroger_auth import token_status
        status = token_status()
        print(f"\nToken status: {status['message']}")
