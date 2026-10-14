"""
streamlit_keepalive.py
----------------------
Open the app in a headless browser so Streamlit Community Cloud counts a
visit, and wake it first if it has already gone to sleep.

Community Cloud hibernates a free app after 12 hours without traffic, and
only a real browser session counts — a plain HTTP GET (curl) does not, and
neither do commits to the repo (reported not to work reliably). Run by
.github/workflows/streamlit-keepalive.yml every 6 hours.

It never logs in: reaching the password screen is proof the app is up and
the visit was counted, so no secret is needed in GitHub.

Usage:  python scripts/streamlit_keepalive.py https://sled44-smartcart.streamlit.app/
Exit 0 when the app's login screen (or any app screen) rendered; 1 otherwise.
"""

import re
import sys
import time

from playwright.sync_api import sync_playwright

# Sleeping apps show this on Streamlit's own page, outside the app iframe.
WAKE_BUTTON = re.compile(r"get this app back up", re.I)
# Text from screens/login.py — present on every fresh visit.
APP_READY_TEXT = "Household Login"
# A cold wake rebuilds the container and reinstalls requirements: minutes.
WAKE_TIMEOUT_S = 300


def app_rendered(page) -> bool:
    """True once the app's own UI is on screen. On streamlit.app the app runs
    inside an iframe of Streamlit's wrapper page, so check every frame."""
    for frame in page.frames:
        try:
            if frame.get_by_text(APP_READY_TEXT).count():
                return True
        except Exception:
            pass  # frame navigated away mid-check; next poll retries
    return False


def main(url: str) -> int:
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page()
        page.goto(url, wait_until="domcontentloaded", timeout=60_000)

        woke = False
        deadline = time.time() + WAKE_TIMEOUT_S
        while time.time() < deadline:
            if app_rendered(page):
                # Stay connected a little so the session is a real visit.
                time.sleep(10)
                browser.close()
                print(f"✓ app is up{' (woken from sleep)' if woke else ''}: {url}")
                return 0
            button = page.get_by_role("button", name=WAKE_BUTTON)
            if not woke and button.count():
                print("App was asleep, clicking the wake-up button…")
                button.first.click()
                woke = True
            time.sleep(5)

        page.screenshot(path="keepalive-failure.png", full_page=True)
        browser.close()
        print(
            f"::error::App didn't render within {WAKE_TIMEOUT_S}s "
            f"(woke={woke}). If Streamlit changed its sleep page, the wake "
            "button text or APP_READY_TEXT here needs updating. "
            "Screenshot saved as a workflow artifact."
        )
        return 1


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print(__doc__)
        raise SystemExit(2)
    raise SystemExit(main(sys.argv[1]))
