"""
kroger_auth.py
--------------
Kroger OAuth 2.0 + PKCE authentication for SmartCart.

Tokens are persisted in Supabase (key: "kroger_tokens") so the app keeps its
authorization across container restarts on hosts with ephemeral disks
(Streamlit Cloud, Fly.io, etc.).

Two flows are supported:

  1. Hosted flow (used by main.py on Streamlit Cloud or anywhere with a
     public URL):
        build_authorization_url() -> {"url", "state", "code_verifier"}
        exchange_code_for_tokens(code, code_verifier) -> dict
     main.py stashes state + code_verifier in session_state, redirects to
     `url`, then reads `?code=...&state=...` on the return trip.

  2. Local CLI flow (used by `python kroger_auth.py` on your laptop):
        run_local_authorization_flow() — spins up a local HTTP server to
        capture the redirect. Useful for first-time seeding from a dev box.

Public interface (used by other modules):
    get_valid_token() -> str
    get_client_credentials_token() -> str
    token_status() -> dict
    clear_stored_tokens() -> None

When no tokens exist or refresh fails, get_valid_token() raises
NeedsAuthorization. The UI catches this and shows the "Connect Kroger" button.
"""

import base64
import hashlib
import json
import os
import secrets
import threading
import time
import urllib.parse
import webbrowser
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, HTTPServer

import requests
from dotenv import load_dotenv

from supabase_kv import kv_delete, kv_get, kv_put

load_dotenv(override=True)

# ---------------------------------------------------------------------------
# Configuration (all values come from .env)
# ---------------------------------------------------------------------------

CLIENT_ID = os.getenv("KROGER_CLIENT_ID", "")
CLIENT_SECRET = os.getenv("KROGER_CLIENT_SECRET", "")
REDIRECT_URI = os.getenv("KROGER_REDIRECT_URI", "http://localhost:8501/")

KROGER_AUTH_URL = "https://api.kroger.com/v1/connect/oauth2/authorize"
KROGER_TOKEN_URL = "https://api.kroger.com/v1/connect/oauth2/token"

# Scopes: product read + cart write. No payment, no order history.
SCOPES = "product.compact cart.basic:write"

# Supabase KV key for stored tokens
TOKENS_KEY = "kroger_tokens"


class NeedsAuthorization(RuntimeError):
    """Raised when no valid tokens are available and the user must re-auth."""


# ---------------------------------------------------------------------------
# PKCE helpers
# ---------------------------------------------------------------------------

def _generate_pkce_pair() -> tuple[str, str]:
    """Generate a PKCE code_verifier and code_challenge (S256 method)."""
    code_verifier = base64.urlsafe_b64encode(secrets.token_bytes(32)).rstrip(b"=").decode()
    digest = hashlib.sha256(code_verifier.encode()).digest()
    code_challenge = base64.urlsafe_b64encode(digest).rstrip(b"=").decode()
    return code_verifier, code_challenge


# ---------------------------------------------------------------------------
# Token persistence (Supabase KV)
# ---------------------------------------------------------------------------

def _save_tokens(token_data: dict) -> None:
    """Persist tokens to Supabase KV."""
    token_data = dict(token_data)
    token_data["saved_at"] = time.time()
    kv_put(TOKENS_KEY, token_data)


def _load_tokens() -> dict | None:
    """Load tokens from Supabase KV. Returns None if absent."""
    return kv_get(TOKENS_KEY, None)


def _is_access_token_expired(token_data: dict, buffer_seconds: int = 60) -> bool:
    """
    Returns True if the access token has expired or will expire within
    buffer_seconds. Kroger access tokens live for 1800 seconds (30 min).
    """
    saved_at = token_data.get("saved_at", 0)
    expires_in = token_data.get("expires_in", 1800)
    age = time.time() - saved_at
    return age >= (expires_in - buffer_seconds)


# ---------------------------------------------------------------------------
# Hosted OAuth flow — used by Streamlit UI
# ---------------------------------------------------------------------------

def build_authorization_url() -> dict:
    """
    Build the Kroger authorization URL and return everything the UI needs
    to complete the round-trip.

    Returns:
        {
            "url":           str,   Full URL to redirect/open in browser
            "state":         str,   CSRF token to verify on return
            "code_verifier": str,   PKCE verifier to keep until callback
        }

    The caller must store `state` and `code_verifier` somewhere that survives
    the redirect (Streamlit session_state is fine for a single user).
    """
    if not CLIENT_ID or not CLIENT_SECRET:
        raise RuntimeError(
            "KROGER_CLIENT_ID and KROGER_CLIENT_SECRET must be set in your .env file."
        )

    code_verifier, code_challenge = _generate_pkce_pair()
    state = secrets.token_urlsafe(16)

    params = {
        "client_id": CLIENT_ID,
        "redirect_uri": REDIRECT_URI,
        "response_type": "code",
        "scope": SCOPES,
        "state": state,
        "code_challenge": code_challenge,
        "code_challenge_method": "S256",
    }
    url = KROGER_AUTH_URL + "?" + urllib.parse.urlencode(params)
    return {"url": url, "state": state, "code_verifier": code_verifier}


def exchange_code_for_tokens(code: str, code_verifier: str) -> dict:
    """
    Exchange an authorization code for access + refresh tokens.
    Persists the result to Supabase. Returns the saved token dict.
    """
    response = requests.post(
        KROGER_TOKEN_URL,
        data={
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": REDIRECT_URI,
            "code_verifier": code_verifier,
            "client_id": CLIENT_ID,
        },
        auth=(CLIENT_ID, CLIENT_SECRET),
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        timeout=30,
    )
    if not response.ok:
        raise RuntimeError(
            f"Token exchange failed ({response.status_code}): {response.text}"
        )
    token_data = response.json()
    _save_tokens(token_data)
    return token_data


# ---------------------------------------------------------------------------
# Local CLI OAuth flow (legacy — still useful for first-time dev seeding)
# ---------------------------------------------------------------------------

class _CallbackHandler(BaseHTTPRequestHandler):
    """Captures the OAuth redirect when running the CLI flow locally."""

    auth_code: str | None = None
    error: str | None = None

    def do_GET(self):  # noqa: N802
        parsed = urllib.parse.urlparse(self.path)
        params = urllib.parse.parse_qs(parsed.query)
        if "code" in params:
            _CallbackHandler.auth_code = params["code"][0]
            self._send_success_page()
        elif "error" in params:
            _CallbackHandler.error = params.get("error_description", ["Unknown error"])[0]
            self._send_error_page(_CallbackHandler.error)
        else:
            self._send_error_page("Unexpected callback — no code or error parameter received.")

    def _send_success_page(self):
        body = b"""
        <html><body style="font-family:sans-serif;text-align:center;padding:60px">
        <h2>&#10003; SmartCart is authorized!</h2>
        <p>You can close this tab and return to the app.</p>
        </body></html>"""
        self.send_response(200)
        self.send_header("Content-Type", "text/html")
        self.end_headers()
        self.wfile.write(body)

    def _send_error_page(self, message: str):
        body = f"""
        <html><body style="font-family:sans-serif;text-align:center;padding:60px">
        <h2>&#10007; Authorization failed</h2>
        <p>{message}</p>
        </body></html>""".encode()
        self.send_response(400)
        self.send_header("Content-Type", "text/html")
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format, *args):  # noqa: A002
        pass


def run_local_authorization_flow() -> dict:
    """
    Local-only flow: opens browser, spins up a temp HTTP server on the
    redirect URI's port, captures the code, exchanges it for tokens.

    Only usable when REDIRECT_URI points to localhost. Used by the CLI;
    the Streamlit UI uses build_authorization_url + exchange_code_for_tokens.
    """
    parsed_redirect = urllib.parse.urlparse(REDIRECT_URI)
    if parsed_redirect.hostname not in ("localhost", "127.0.0.1"):
        raise RuntimeError(
            f"Local auth flow only works when KROGER_REDIRECT_URI points to "
            f"localhost. Got {REDIRECT_URI}. Use the in-app OAuth button instead."
        )

    auth = build_authorization_url()
    callback_port = parsed_redirect.port or 8501

    _CallbackHandler.auth_code = None
    _CallbackHandler.error = None
    server = HTTPServer(("127.0.0.1", callback_port), _CallbackHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()

    print(f"\n{'='*60}")
    print("KROGER AUTHORIZATION REQUIRED")
    print(f"{'='*60}")
    print("Opening your browser to authorize SmartCart with Kroger.")
    print(f"\nIf the browser doesn't open automatically, visit:\n{auth['url']}\n")
    webbrowser.open(auth["url"])

    timeout = 180
    start = time.time()
    while _CallbackHandler.auth_code is None and _CallbackHandler.error is None:
        if time.time() - start > timeout:
            server.shutdown()
            raise RuntimeError("Authorization timed out after 3 minutes.")
        time.sleep(0.5)
    server.shutdown()

    if _CallbackHandler.error:
        raise RuntimeError(f"Kroger authorization failed: {_CallbackHandler.error}")

    token_data = exchange_code_for_tokens(_CallbackHandler.auth_code, auth["code_verifier"])
    print("✓ Authorization successful. Tokens saved to Supabase.")
    return token_data


# ---------------------------------------------------------------------------
# Token refresh
# ---------------------------------------------------------------------------

def _refresh_access_token(token_data: dict) -> dict:
    """Use refresh_token to get a new access_token. Persists the result."""
    refresh_token = token_data.get("refresh_token")
    if not refresh_token:
        raise NeedsAuthorization("No refresh token available.")

    response = requests.post(
        KROGER_TOKEN_URL,
        data={
            "grant_type": "refresh_token",
            "refresh_token": refresh_token,
        },
        auth=(CLIENT_ID, CLIENT_SECRET),
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        timeout=30,
    )
    if not response.ok:
        raise NeedsAuthorization(
            f"Token refresh failed ({response.status_code}). Re-authorization required."
        )

    new_token_data = response.json()
    # Kroger sometimes omits refresh_token on refresh if unchanged
    if "refresh_token" not in new_token_data:
        new_token_data["refresh_token"] = refresh_token
    _save_tokens(new_token_data)
    return new_token_data


# ---------------------------------------------------------------------------
# Public interface
# ---------------------------------------------------------------------------

def get_valid_token() -> str:
    """
    Return a valid Kroger user access token.

    - Returns cached token if still valid.
    - Refreshes silently if expired but refresh_token works.
    - Raises NeedsAuthorization if no tokens exist or refresh fails.
      The UI catches that and shows the "Connect Kroger" button.
    """
    token_data = _load_tokens()
    if token_data is None:
        raise NeedsAuthorization("No tokens on file. User must authorize.")

    if _is_access_token_expired(token_data):
        token_data = _refresh_access_token(token_data)

    return token_data["access_token"]


def get_client_credentials_token() -> str:
    """
    Returns an access token using the client credentials grant.
    Used for unauthenticated endpoints like /v1/locations.
    Not persisted — fetched fresh each call.
    """
    if not CLIENT_ID or not CLIENT_SECRET:
        raise RuntimeError("KROGER_CLIENT_ID and KROGER_CLIENT_SECRET must be set in .env")

    response = requests.post(
        KROGER_TOKEN_URL,
        data={
            "grant_type": "client_credentials",
            "scope": "product.compact",
        },
        auth=(CLIENT_ID, CLIENT_SECRET),
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        timeout=30,
    )
    if not response.ok:
        raise RuntimeError(
            f"Client credentials token request failed ({response.status_code}): {response.text}"
        )
    return response.json()["access_token"]


def clear_stored_tokens() -> None:
    """Remove stored tokens. Next call to get_valid_token() will require re-auth."""
    if kv_delete(TOKENS_KEY):
        print("✓ Stored tokens cleared.")
    else:
        print("No stored tokens found.")


def token_status() -> dict:
    """Describe the current token state (for diagnostics / settings screen)."""
    token_data = _load_tokens()
    if token_data is None:
        return {"status": "not_authorized", "message": "No tokens on file. App needs authorization."}

    if _is_access_token_expired(token_data):
        return {"status": "expired", "message": "Access token expired. Will refresh on next use."}

    saved_at = token_data.get("saved_at", 0)
    expires_in = token_data.get("expires_in", 1800)
    remaining = int((saved_at + expires_in) - time.time())
    return {
        "status": "valid",
        "message": f"Token valid. Expires in ~{remaining // 60}m {remaining % 60}s.",
        "expires_in_seconds": remaining,
    }


# ---------------------------------------------------------------------------
# CLI entry point — run this file directly to authorize a local dev box
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import sys

    print("SmartCart — Kroger Authorization (local CLI)")
    print("-" * 40)
    status = token_status()
    print(f"Current status: {status['message']}")

    if "--reauth" in sys.argv:
        clear_stored_tokens()
        print("\nRe-authorizing...")
        run_local_authorization_flow()
    elif status["status"] != "valid":
        print("\nStarting authorization flow...")
        try:
            run_local_authorization_flow()
        except RuntimeError as e:
            print(f"\n✗ Error: {e}")
    else:
        print("\nAlready authorized. To re-auth, run: python kroger_auth.py --reauth")
