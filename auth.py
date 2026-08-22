"""
auth.py
-------
Household login that survives a page reload.

Streamlit's ``session_state`` lives and dies with the browser's WebSocket
connection, so the plain ``authenticated`` flag drops on every refresh, on
the Kroger OAuth round-trip (Kroger -> us is a fresh connection), and every
time Streamlit Cloud recycles the container after an idle spell. The result
was a password prompt several times a shopping session.

So the flag is backed by a first-party cookie holding a signed token:

    v1.<expires_at>.<hmac-sha256(APP_PASSWORD, "v1.<expires_at>")>

Nothing secret rides in the cookie and nothing is stored server-side — the
signature is the whole check, so a paused Supabase can't lock anyone out.
Rotating ``APP_PASSWORD`` invalidates every outstanding token for free.

Reading is via ``st.context.cookies`` (populated from the WebSocket handshake
headers). Writing needs the browser, so it goes through a zero-height
``components.html`` iframe: those run with ``allow-same-origin``, so
``document.cookie`` there lands on the app's own origin. If a browser ever
blocks that, the cookie simply never appears and login falls back to today's
per-session behaviour.
"""

import base64
import hashlib
import hmac
import json
import os
import time

import streamlit as st
import streamlit.components.v1 as components

COOKIE_NAME = "sc_auth"

# 30 days, refreshed whenever a visit lands with less than half of it left,
# so anyone who shops even monthly never sees the password prompt again.
TOKEN_TTL_SECONDS = 30 * 24 * 60 * 60
_REFRESH_BELOW = TOKEN_TTL_SECONDS // 2

_VERSION = "v1"

# session_state bookkeeping
_OP_KEY = "_auth_cookie_op"          # ("set", token) | ("clear", "") pending a browser write
_CHECKED_KEY = "_auth_cookie_read"   # cookie consulted once per Streamlit session


# ---------------------------------------------------------------------------
# Token minting / checking
# ---------------------------------------------------------------------------

def _signing_key() -> bytes:
    """HMAC key = the household password. Empty when unconfigured, which
    disables persistence entirely (nothing to sign with)."""
    return os.getenv("APP_PASSWORD", "").encode("utf-8")


def _sign(payload: str, key: bytes) -> str:
    digest = hmac.new(key, payload.encode("utf-8"), hashlib.sha256).digest()
    return base64.urlsafe_b64encode(digest).decode("ascii").rstrip("=")


def issue_token(now: float | None = None) -> str | None:
    """Mint a signed token, or None when APP_PASSWORD isn't configured."""
    key = _signing_key()
    if not key:
        return None
    expires_at = int((now if now is not None else time.time()) + TOKEN_TTL_SECONDS)
    payload = f"{_VERSION}.{expires_at}"
    return f"{payload}.{_sign(payload, key)}"


def verify_token(token: str, now: float | None = None) -> int | None:
    """Return the token's expiry when it is well-formed, correctly signed and
    unexpired; None otherwise."""
    key = _signing_key()
    if not key or not token:
        return None
    parts = token.split(".")
    if len(parts) != 3:
        return None
    version, expires_raw, signature = parts
    if version != _VERSION:
        return None
    try:
        expires_at = int(expires_raw)
    except ValueError:
        return None
    if not hmac.compare_digest(_sign(f"{version}.{expires_raw}", key), signature):
        return None
    if expires_at <= (now if now is not None else time.time()):
        return None
    return expires_at


# ---------------------------------------------------------------------------
# Cookie plumbing
# ---------------------------------------------------------------------------

def _cookie_from_browser() -> str:
    """Read the auth cookie off the current connection. Empty string when the
    Streamlit build predates st.context.cookies or the cookie isn't set."""
    try:
        cookies = st.context.cookies or {}
        return cookies.get(COOKIE_NAME, "") or ""
    except Exception:
        return ""


def _write_cookie_js(value: str, max_age: int) -> str:
    """JS that writes (or, with max_age 0, expires) the cookie on the parent
    document — the Streamlit app itself, not the component iframe."""
    payload = json.dumps({"name": COOKIE_NAME, "value": value, "maxAge": max_age})
    return (
        "<script>(function(){var c=" + payload + ";"
        "function write(doc, loc){"
        "var secure = loc && loc.protocol === 'https:' ? '; Secure' : '';"
        "doc.cookie = c.name + '=' + c.value + '; path=/; max-age=' + c.maxAge"
        " + '; SameSite=Lax' + secure;}"
        "try { write(window.parent.document, window.parent.location); }"
        "catch (e) { try { write(document, location); } catch (e2) {} }"
        "})();</script>"
    )


def _embed_script(markup: str) -> None:
    """Drop a zero-height script iframe into the page. st.iframe superseded
    st.components.v1.html; fall back for older builds (requirements only
    guarantee 1.42, and the rest of the app still uses components.html)."""
    iframe = getattr(st, "iframe", None)
    if iframe is not None:
        iframe(markup, height=1, width=1)
    else:
        components.html(markup, height=0, width=0)


def sync_cookie() -> None:
    """Flush any pending cookie write. Call once per run, early, from the
    router — the write is queued in session_state by start_session() /
    end_session() so it survives the st.rerun() those trigger."""
    op = st.session_state.pop(_OP_KEY, None)
    if not op:
        return
    action, token = op
    max_age = TOKEN_TTL_SECONDS if action == "set" else 0
    _embed_script(_write_cookie_js(token, max_age))


# ---------------------------------------------------------------------------
# Session lifecycle
# ---------------------------------------------------------------------------

def restore_session() -> bool:
    """Adopt a valid auth cookie as a logged-in session. Runs at most once per
    Streamlit session, so signing out isn't undone by the stale cookie value
    still present in this connection's handshake headers."""
    if st.session_state.get("authenticated"):
        return True
    if st.session_state.get(_CHECKED_KEY):
        return False
    st.session_state[_CHECKED_KEY] = True

    token = _cookie_from_browser()
    expires_at = verify_token(token)
    if expires_at is None:
        return False

    st.session_state.authenticated = True
    # Sliding expiry: a token past its halfway mark gets replaced on sight.
    if expires_at - time.time() < _REFRESH_BELOW:
        fresh = issue_token()
        if fresh:
            st.session_state[_OP_KEY] = ("set", fresh)
    return True


def start_session() -> None:
    """Mark this session authenticated and queue the cookie write."""
    st.session_state.authenticated = True
    st.session_state[_CHECKED_KEY] = True
    token = issue_token()
    if token:
        st.session_state[_OP_KEY] = ("set", token)


def end_session() -> None:
    """Sign out here and everywhere: drop the flag and expire the cookie."""
    st.session_state.authenticated = False
    st.session_state[_CHECKED_KEY] = True
    st.session_state[_OP_KEY] = ("clear", "")


# ---------------------------------------------------------------------------
# Self-test:  python3 auth.py --test
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import sys

    if "--test" not in sys.argv:
        print("Usage: python3 auth.py --test")
        raise SystemExit(1)

    os.environ["APP_PASSWORD"] = "test-password"

    token = issue_token()
    assert token and token.startswith(f"{_VERSION}."), "no token minted"
    assert verify_token(token), "freshly minted token rejected"
    print("✓ Mint + verify")

    assert verify_token(token[:-1] + ("a" if token[-1] != "a" else "b")) is None
    assert verify_token("v1.9999999999.notasignature") is None
    assert verify_token("garbage") is None
    assert verify_token("") is None
    assert verify_token("v2." + token.split(".", 1)[1]) is None, "version not pinned"
    print("✓ Forged / malformed tokens rejected")

    stale = issue_token(now=time.time() - TOKEN_TTL_SECONDS - 60)
    assert verify_token(stale) is None, "expired token accepted"
    print("✓ Expiry enforced")

    os.environ["APP_PASSWORD"] = "rotated-password"
    assert verify_token(token) is None, "token survived a password rotation"
    print("✓ Rotating APP_PASSWORD invalidates outstanding tokens")

    os.environ["APP_PASSWORD"] = ""
    assert issue_token() is None and verify_token(token) is None
    print("✓ No APP_PASSWORD → persistence disabled")

    print("\n✓ All tests passed.")
