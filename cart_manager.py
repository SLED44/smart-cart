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
from preference_store import append_session_log
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
        _log.info("CART %r: qty=%s upc=%s (%r)",
                  item.get("item_name", "?"), quantity, upc,
                  primary.get("product_name", "?"))
        cart_items.append({"upc": upc, "quantity": quantity})

    return cart_items


def _post_batch(cart_items: list, token: str) -> tuple[list, list]:
    """
    Attempt to post a batch of items to the Kroger cart.

    Returns (succeeded_upcs, failed_items) where:
        succeeded_upcs: list of UPCs that were accepted
        failed_items:   list of dicts with upc, quantity, error

    Handles the PRD Open Question #2 ambiguity:
    - Tries batch with quantity field first
    - If that fails with a 4xx, falls back to individual calls
    """
    if not cart_items:
        return [], []

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
        return [], [
            {"upc": i["upc"], "quantity": i["quantity"], "error": str(e)}
            for i in cart_items
        ]

    if response.ok:
        # Batch succeeded — all items accepted
        return [i["upc"] for i in cart_items], []

    if response.status_code == 400:
        # Batch format may be unsupported — try one at a time
        print("  ⚠ Batch cart post returned 400. Falling back to individual item posts...")
        return _post_individually(cart_items, token)

    if response.status_code == 401:
        # Token issue — shouldn't happen with our refresh logic
        return [], [
            {"upc": i["upc"], "quantity": i["quantity"],
             "error": "Authorization error — please restart the app to re-authorize."}
            for i in cart_items
        ]

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
    ]


def _post_individually(cart_items: list, token: str) -> tuple[list, list]:
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
            # Try without quantity field (some Kroger API versions don't support it)
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
                else:
                    error_msg = f"Error {retry.status_code}"
                    failed_items.append({"upc": upc, "quantity": quantity, "error": error_msg})
            except requests.RequestException as e:
                failed_items.append({"upc": upc, "quantity": quantity, "error": str(e)})
        else:
            error_msg = f"Error {response.status_code}"
            failed_items.append({"upc": upc, "quantity": quantity, "error": error_msg})

        time.sleep(CART_CALL_DELAY)

    return succeeded_upcs, failed_items


# ---------------------------------------------------------------------------
# Result assembly
# ---------------------------------------------------------------------------

def _build_cart_result(
    confirmed_items: list,
    succeeded_upcs: list,
    failed_cart_items: list,
) -> dict:
    """
    Build the CartResult dict from raw API outcomes.
    Matches API results back to original confirmed items for display.
    """
    succeeded_upc_set = set(succeeded_upcs)
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
            succeeded.append(item_result)

            # Add to estimated total
            price = primary.get("promo_price") or primary.get("price")
            if price:
                quantity = max(1, round(item.get("quantity", 1)))
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

    for i in range(0, len(cart_items), BATCH_SIZE):
        batch = cart_items[i:i + BATCH_SIZE]
        succeeded_upcs, failed = _post_batch(batch, token)
        all_succeeded_upcs.extend(succeeded_upcs)
        all_failed_items.extend(failed)

    # Build result
    result = _build_cart_result(confirmed_items, all_succeeded_upcs, all_failed_items)

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
