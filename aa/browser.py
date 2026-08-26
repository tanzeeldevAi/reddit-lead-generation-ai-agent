"""Shared Playwright helpers.

Two modes, deliberately separated:

  fresh()   -- a clean throwaway browser used for READING only (discovery).
               No login, no cookies, nothing to compromise. Safe on a VPS.

  profile() -- a persistent profile tied to ONE site's account, used for
               posting. Each site gets its own directory so account A's
               cookies can never appear in account B's session.

  attach()  -- connect over CDP to a Chrome the user already has open. This is
               the safest posting path: real machine, real IP, real profile.
"""
from __future__ import annotations

import random
import time
from contextlib import contextmanager
from pathlib import Path

from playwright.sync_api import sync_playwright

from .config import ROOT

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36")


def human_pause(lo: float = 1.2, hi: float = 3.4) -> None:
    """Real reading time between actions. Not an evasion trick -- the agent
    genuinely should not fire requests faster than a person could read."""
    time.sleep(random.uniform(lo, hi))


@contextmanager
def fresh(headless: bool = True):
    """Anonymous browser for discovery. Never log in with this."""
    with sync_playwright() as p:
        b = p.chromium.launch(headless=headless)
        ctx = b.new_context(user_agent=UA, viewport={"width": 1366, "height": 900},
                            locale="en-US")
        try:
            yield ctx.new_page()
        finally:
            ctx.close()
            b.close()


@contextmanager
def profile(profile_dir: str, headless: bool = False):
    """Persistent per-site profile for posting. Log in once, stays logged in."""
    path = Path(ROOT) / profile_dir
    path.mkdir(parents=True, exist_ok=True)
    with sync_playwright() as p:
        ctx = p.chromium.launch_persistent_context(
            str(path), headless=headless, user_agent=UA,
            viewport={"width": 1366, "height": 900}, locale="en-US")
        try:
            yield ctx.pages[0] if ctx.pages else ctx.new_page()
        finally:
            ctx.close()


@contextmanager
def attach(port: int = 9222):
    """Attach to the user's already-running Chrome over CDP.

    Start Chrome first:
      chrome.exe --remote-debugging-port=9222 --user-data-dir="C:\\chrome-aa"
    """
    with sync_playwright() as p:
        browser = p.chromium.connect_over_cdp(f"http://localhost:{port}")
        ctx = browser.contexts[0] if browser.contexts else browser.new_context()
        page = ctx.pages[0] if ctx.pages else ctx.new_page()
        try:
            yield page
        finally:
            browser.close()  # detaches; does not kill the user's Chrome


def goto(page, url: str, settle: float = 3.0, timeout: int = 60000) -> bool:
    """Navigate and let client-side rendering settle. Returns False on failure
    rather than raising, so one dead URL cannot kill a whole discovery run."""
    try:
        page.goto(url, wait_until="domcontentloaded", timeout=timeout)
        page.wait_for_timeout(int(settle * 1000))
        return True
    except Exception:
        return False
