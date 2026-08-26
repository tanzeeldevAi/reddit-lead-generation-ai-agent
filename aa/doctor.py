"""Credential + environment check. Tells you exactly what is wrong and how to fix it.

Never prints a secret in full -- values are masked so this is safe to screenshot.
"""
from __future__ import annotations

import os

import requests

from .config import ROOT, env

REDDIT_VARS = [
    ("REDDIT_CLIENT_ID", "the short string under the app name at reddit.com/prefs/apps"),
    ("REDDIT_CLIENT_SECRET", "the value labelled 'secret' on the same app card"),
    ("REDDIT_USERNAME", "your Reddit username, no 'u/' prefix"),
    ("REDDIT_PASSWORD", "that account's password"),
]


def mask(val: str, full: bool = False) -> str:
    """Mask a secret. `full` hides every character -- used for passwords, where
    there is no reason to ever echo part of it back."""
    if not val:
        return "(empty)"
    if full or len(val) <= 4:
        return f"{'*' * len(val)}  ({len(val)} chars)"
    return f"{val[:2]}{'*' * (len(val) - 4)}{val[-2:]}  ({len(val)} chars)"


def check_file() -> bool:
    path = ROOT / ".env"
    if not path.exists():
        print(f"  .env NOT FOUND at {path}")
        print("  -> copy .env.example to .env and fill it in")
        return False
    print(f"  .env found at {path}")
    return True


def check_vars() -> tuple[bool, list[str]]:
    missing = []
    print("\nReddit credentials:")
    for name, hint in REDDIT_VARS:
        val = os.environ.get(name, "")
        if not val:
            missing.append(name)
            print(f"  {name:<24} MISSING  <- {hint}")
        else:
            print(f"  {name:<24} {mask(val, full=(name == 'REDDIT_PASSWORD'))}")

    # Common paste mistakes worth catching before we hit the API
    warns = []
    user = os.environ.get("REDDIT_USERNAME", "")
    if user.startswith("u/") or user.startswith("/u/"):
        warns.append("REDDIT_USERNAME has a 'u/' prefix -- remove it")
    for name, _ in REDDIT_VARS:
        v = os.environ.get(name, "")
        if v and (v[0] in "\"'" or v[-1] in "\"'"):
            warns.append(f"{name} has quotes around it -- remove them")
        if v != v.strip():
            warns.append(f"{name} has leading/trailing spaces -- remove them")

    groq = os.environ.get("GROQ_API_KEY", "")
    print(f"\nGroq (optional):\n  GROQ_API_KEY             "
          f"{mask(groq) if groq else '(not set -- fine, engine is claude)'}")

    if warns:
        print("\nProblems found:")
        for w in warns:
            print(f"  ! {w}")

    return (not missing and not warns), missing


def check_reddit_auth() -> bool:
    print("\nTesting Reddit login...")
    try:
        cid = env("REDDIT_CLIENT_ID", required=True)
        secret = env("REDDIT_CLIENT_SECRET", required=True)
        user = env("REDDIT_USERNAME", required=True)
        pw = env("REDDIT_PASSWORD", required=True)
    except SystemExit as e:
        print(f"  {e}")
        return False

    resp = requests.post(
        "https://www.reddit.com/api/v1/access_token",
        auth=(cid, secret),
        data={"grant_type": "password", "username": user, "password": pw},
        headers={"User-Agent": f"windows:authority-agent:v1.0 (by /u/{user})"},
        timeout=30,
    )

    if resp.status_code == 200 and "access_token" in resp.json():
        print("  LOGIN OK -- Reddit discovery is ready")
        return True

    print(f"  LOGIN FAILED (HTTP {resp.status_code})")
    body = resp.text[:200]

    if resp.status_code == 401:
        print("  Most likely causes, in order:")
        print("    1. The app is not type 'script'. Delete it and recreate as script.")
        print("    2. CLIENT_ID / CLIENT_SECRET swapped or mistyped.")
        print("    3. 2FA is ON for this account -- password grant cannot work with 2FA.")
        print("       Turn 2FA off for this account, or run Quora-only:")
        print("         python run.py discover --platform quora")
    elif resp.status_code == 400:
        print("  Usually a wrong username or password. Check for typos and stray quotes.")
    elif resp.status_code == 429:
        print("  Rate limited. Wait a few minutes and try again.")
    else:
        print(f"  Response: {body}")

    return False


def run() -> bool:
    print("=" * 62)
    print("ENVIRONMENT CHECK")
    print("=" * 62)

    if not check_file():
        return False

    ok, missing = check_vars()
    if missing:
        print(f"\n{len(missing)} value(s) still blank. Fill them in .env, then re-run:")
        print("  python run.py check-env")
        return False
    if not ok:
        print("\nFix the problems above, then re-run: python run.py check-env")
        return False

    auth_ok = check_reddit_auth()

    print("\n" + "=" * 62)
    if auth_ok:
        print("ALL GOOD. Next:  python run.py check-subs")
    else:
        print("Reddit not working yet. Quora-only still works:")
        print("  python run.py discover --platform quora")
    print("=" * 62)
    return auth_ok
