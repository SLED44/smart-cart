"""
supabase_kv.py
--------------
Tiny key-value store backed by a single Supabase Postgres table.

Replaces local JSON file persistence so SmartCart can run on hosts with
ephemeral disk (Streamlit Cloud, Fly.io free tier, etc.).

Schema (already applied):
    create table public.kv (
        key text primary key,
        value jsonb not null,
        updated_at timestamptz not null default now()
    );

Public interface:
    kv_get(key, default=None) -> Any   Read a JSON value, or default if missing.
    kv_put(key, value)        -> None  Upsert a JSON value.
    kv_delete(key)            -> bool  Delete a key. Returns True if it existed.

Talks to Supabase via PostgREST so we avoid pulling the full supabase-py SDK.
Connects with the service_role key (server-side only) — RLS stays enabled as
a safety net but no policies are needed.

Required env vars:
    SUPABASE_URL          e.g. https://abcd1234.supabase.co
    SUPABASE_SERVICE_KEY  service_role secret from Supabase dashboard
"""

import os
from typing import Any

import requests
from dotenv import load_dotenv

load_dotenv(override=True)

_URL = os.getenv("SUPABASE_URL", "").rstrip("/")
_KEY = os.getenv("SUPABASE_SERVICE_KEY", "")

_TABLE = "kv"
_TIMEOUT = 15


def _require_config() -> None:
    if not _URL or not _KEY:
        raise RuntimeError(
            "SUPABASE_URL and SUPABASE_SERVICE_KEY must be set. "
            "See README → Streamlit Cloud setup."
        )


def _headers(extra: dict | None = None) -> dict:
    h = {
        "apikey": _KEY,
        "Authorization": f"Bearer {_KEY}",
        "Content-Type": "application/json",
    }
    if extra:
        h.update(extra)
    return h


def kv_get(key: str, default: Any = None) -> Any:
    """Return the JSON value stored at `key`, or `default` if absent."""
    _require_config()
    r = requests.get(
        f"{_URL}/rest/v1/{_TABLE}",
        headers=_headers(),
        params={"key": f"eq.{key}", "select": "value"},
        timeout=_TIMEOUT,
    )
    r.raise_for_status()
    rows = r.json()
    if not rows:
        return default
    return rows[0]["value"]


def kv_put(key: str, value: Any) -> None:
    """Upsert `value` (any JSON-serializable object) at `key`."""
    _require_config()
    r = requests.post(
        f"{_URL}/rest/v1/{_TABLE}",
        headers=_headers({"Prefer": "resolution=merge-duplicates,return=minimal"}),
        json={"key": key, "value": value},
        params={"on_conflict": "key"},
        timeout=_TIMEOUT,
    )
    r.raise_for_status()


def kv_delete(key: str) -> bool:
    """Delete `key`. Returns True if a row was removed."""
    _require_config()
    r = requests.delete(
        f"{_URL}/rest/v1/{_TABLE}",
        headers=_headers({"Prefer": "return=representation"}),
        params={"key": f"eq.{key}"},
        timeout=_TIMEOUT,
    )
    r.raise_for_status()
    return bool(r.json())


if __name__ == "__main__":
    print("SmartCart — Supabase KV connectivity check")
    print("-" * 40)
    _require_config()
    print(f"URL: {_URL}")
    print("Writing test value...")
    kv_put("_smoke_test", {"hello": "world"})
    got = kv_get("_smoke_test")
    assert got == {"hello": "world"}, f"round-trip failed: {got}"
    deleted = kv_delete("_smoke_test")
    assert deleted, "delete returned False"
    print("✓ Round-trip succeeded.")
