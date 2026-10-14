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

Both reading and writing go through a tiny ``st.components.v2`` "bridge"
that runs in the browser: it writes any queued cookie change, then reports
the cookie's current value back to Python (which triggers one rerun). The
server-side read, ``st.context.cookies``, works locally but comes back empty
on Streamlit Community Cloud — reportedly its proxy filters cookies off the WebSocket
handshake — which is why login kept falling out on every refresh even though
the cookie was sitting in the browser (found 2026-10-14). ``st.context`` is
still consulted first, so local dev restores without waiting on the bridge.
If a browser blocks the bridge entirely, login falls back to per-session.

iPad / Safari note: WebKit deletes script-written cookies after 7 days with
no visit (ITP), so a week away means one password prompt. Nothing a page
can do about that.
"""

import base64
import hashlib
import hmac
import os
import time

import streamlit as st

COOKIE_NAME = "sc_auth"

# 30 days, refreshed whenever a visit lands with less than half of it left,
# so anyone who shops even monthly never sees the password prompt again.
TOKEN_TTL_SECONDS = 30 * 24 * 60 * 60
_REFRESH_BELOW = TOKEN_TTL_SECONDS // 2

_VERSION = "v1"

# session_state bookkeeping
_OP_KEY = "_auth_cookie_op"          # ("set", token) | ("clear", "") pending a browser write
_CHECKED_KEY = "_auth_cookie_read"   # cookie consulted once per Streamlit session
_BRIDGE_KEY = "sc_auth_bridge"       # component key; its reported state lives here too


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

def _cookie_from_server() -> str:
    """The cookie as the server saw it on the WebSocket handshake. Works
    locally; always empty on Streamlit Community Cloud (see module doc)."""
    try:
        cookies = st.context.cookies or {}
        return cookies.get(COOKIE_NAME, "") or ""
    except Exception:
        return ""


def _cookie_from_bridge() -> str | None:
    """The cookie as the browser bridge last reported it: a string (empty
    when there's no cookie), or None until the bridge has reported once."""
    state = st.session_state.get(_BRIDGE_KEY)
    try:
        return state.get("token") if state is not None else None
    except Exception:
        return None


# Runs in the app page itself (v2 components aren't iframed), so
# document.cookie is the app origin's cookie jar. Write first, then report,
# so a reported value always reflects the write that preceded it.
_BRIDGE_JS = """
export default function ({ data, setStateValue }) {
  const name = data.name;
  if (data.op) {
    const secure = location.protocol === "https:" ? "; Secure" : "";
    document.cookie = name + "=" + data.op.value + "; path=/; max-age=" +
      data.op.maxAge + "; SameSite=Lax" + secure;
  }
  const m = document.cookie.match(new RegExp("(?:^|; )" + name + "=([^;]*)"));
  setStateValue("token", m ? m[1] : "");
}
"""

_bridge = None


def _mount_bridge(op: dict | None) -> None:
    """Render the zero-height bridge, carrying one pending cookie write."""
    global _bridge
    components_v2 = getattr(getattr(st, "components", None), "v2", None)
    if components_v2 is None:
        return  # Streamlit too old for v2 components: per-session login only
    if _bridge is None:
        _bridge = components_v2.component("sc_auth_bridge", js=_BRIDGE_JS)
    _bridge(
        key=_BRIDGE_KEY,
        data={"name": COOKIE_NAME, "op": op},
        on_token_change=lambda: None,
    )


def sync_cookie() -> None:
    """Mount the bridge every run, flushing any pending cookie write. Call
    once per run, early, from the router — writes are queued in
    session_state by start_session() / end_session() so they survive the
    st.rerun() those trigger."""
    op = st.session_state.pop(_OP_KEY, None)
    write = None
    if op:
        action, token = op
        write = {"value": token, "maxAge": TOKEN_TTL_SECONDS if action == "set" else 0}
    _mount_bridge(write)


# ---------------------------------------------------------------------------
# Session lifecycle
# ---------------------------------------------------------------------------

def restore_session() -> bool:
    """Adopt a valid auth cookie as a logged-in session. Decides at most once
    per Streamlit session, so signing out isn't undone by a stale cookie
    value. Until the browser bridge has reported (the first run of a fresh
    page), it returns False without deciding; the bridge's report reruns the
    script and the decision happens then."""
    if st.session_state.get("authenticated"):
        return True
    if st.session_state.get(_CHECKED_KEY):
        return False

    server_token = _cookie_from_server()
    bridge_token = _cookie_from_bridge()
    if not server_token and bridge_token is None:
        return False  # browser hasn't reported yet — wait for its rerun
    st.session_state[_CHECKED_KEY] = True

    token = server_token or bridge_token or ""
    expires_at = verify_token(token)
    # One line per page load in the Streamlit Cloud logs, so a future
    # "it logged me out" can be told apart: no cookie vs. rejected cookie.
    print(
        f"AUTH restore: server_cookie={'yes' if server_token else 'no'} "
        f"browser_cookie={'yes' if bridge_token else 'no'} "
        f"valid={'yes' if expires_at else 'no'}",
        flush=True,
    )
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
