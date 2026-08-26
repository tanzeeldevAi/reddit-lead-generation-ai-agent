"""Posting through a browser you logged into yourself.

Two modes:

  profile (default) -- each site gets its own persistent browser profile under
      profiles/<site>. You log into that profile once, by hand, and it stays
      logged in. Site A's cookies physically cannot appear in site B's session,
      which is the whole point of one-account-per-site.

  cdp -- attach to a Chrome you launched with --remote-debugging-port. Useful
      when you want to post from the exact browser you use every day.

Either way there is no stored password, no cookie export, and no automated
login. The session is genuinely yours, so there is nothing to spoof.

Nothing is ever sent without an explicit confirmation unless you pass --yes.
"""
from __future__ import annotations

import random
import sys
import time
from contextlib import contextmanager

from playwright.sync_api import sync_playwright

from . import browser as br
from . import db, limits


def human_pause(base: float) -> None:
    """Sleep base seconds with +/-40% jitter, because humans are not metronomes."""
    time.sleep(max(0.4, base * random.uniform(0.6, 1.4)))


def type_like_human(locator, text: str) -> None:
    """Type in short bursts with variable delay rather than pasting instantly."""
    locator.click()
    human_pause(0.8)
    for chunk in text.split("\n"):
        if chunk:
            locator.type(chunk, delay=random.uniform(18, 55))
        locator.press("Shift+Enter")
        human_pause(0.35)


@contextmanager
def cdp_page(cdp: str = "http://localhost:9222"):
    """Attach to a Chrome the user launched with --remote-debugging-port."""
    pw = sync_playwright().start()
    try:
        b = pw.chromium.connect_over_cdp(cdp)
    except Exception as e:
        pw.stop()
        raise SystemExit(
            f"Could not attach to Chrome at {cdp}.\n"
            "Launch it first:\n"
            '  chrome.exe --remote-debugging-port=9222 --user-data-dir="C:/authority-chrome"\n'
            f"  ({e})")
    ctx = b.contexts[0] if b.contexts else b.new_context()
    page = ctx.new_page()
    try:
        yield page
    finally:
        try:
            page.close()
        except Exception:
            pass
        b.close()          # detaches, does not kill the user's Chrome
        pw.stop()


@contextmanager
def session(site: dict, mode: str = "profile", headless: bool = False):
    """A logged-in page for this site, from whichever backend was chosen."""
    if mode == "cdp":
        with cdp_page() as page:
            yield page
    else:
        pdir = site.get("profile_dir") or f"profiles/site-{site['id']}"
        with br.profile(pdir, headless=headless) as page:
            yield page


def whoami(page) -> str:
    """Which Reddit account is this session logged in as? '' if logged out.

    Checked before every post. Posting site A's draft from site B's session --
    or from a logged-out browser -- is the failure this prevents.
    """
    try:
        page.goto("https://www.reddit.com/", wait_until="domcontentloaded",
                  timeout=45000)
        page.wait_for_timeout(2500)
        return page.evaluate("""() => {
            const el = document.querySelector('shreddit-app');
            const u = el && el.getAttribute('user-logged-in') === 'true';
            if (!u) return '';
            const n = document.querySelector('faceplate-tracker[source="nav"] a[href^="/user/"]')
                   || document.querySelector('a[href^="/user/"][role="menuitem"]')
                   || document.querySelector('#expand-user-drawer-button');
            const m = (document.body.innerHTML.match(/"displayText":"([^"]{2,25})"/) || [])[1];
            if (n) {
                const href = n.getAttribute('href') || '';
                const s = href.match(/\\/user\\/([^\\/]+)/);
                if (s) return s[1];
            }
            return m || 'logged-in';
        }""")
    except Exception:
        return ""


def connect(site: dict, mode: str = "profile") -> str:
    """Open this site's browser profile so the user can log in by hand.

    Blocks until they confirm. Nothing is typed for them.
    """
    pdir = site.get("profile_dir") or f"profiles/site-{site['id']}"
    print(f"\nOpening a browser for site #{site['id']} "
          f"({site['name'] or site['url']})")
    print(f"  profile: {pdir}")
    print("\n  Log into Reddit in the window that opens, then come back here.")
    print("  This profile is used ONLY by this site.\n")

    with br.profile(pdir, headless=False) as page:
        page.goto("https://www.reddit.com/login/",
                  wait_until="domcontentloaded", timeout=60000)
        try:
            input("  Press Enter once you are logged in ... ")
        except EOFError:
            print("  no TTY; leaving the browser open is not possible here")
            return ""
        user = whoami(page)

    if user:
        print(f"\n  Logged in as: u/{user}")
        if not site.get("reddit_user"):
            db.update_site(site["id"], reddit_user=user)
            print(f"  Saved as this site's account.")
        elif site["reddit_user"].lstrip("u/") != user:
            print(f"  WARNING: site is configured for "
                  f"u/{site['reddit_user']} but this profile is u/{user}.")
    else:
        print("\n  Still logged out. Run connect again.")
    return user


# ---------------------------------------------------------------------------
# Reddit
# ---------------------------------------------------------------------------

def post_reddit_comment(page, thread_url: str, body: str, dwell: float) -> str:
    page.goto(thread_url, wait_until="domcontentloaded")
    human_pause(3)

    # Actually read the thread. This is not theatre: dwell time is a real signal
    # and it also gives you a chance to abort on a thread that turned hostile.
    print(f"    reading thread for ~{dwell:.0f}s ...")
    steps = max(int(dwell / 5), 1)
    for _ in range(steps):
        page.mouse.wheel(0, random.randint(250, 700))
        human_pause(5)
    page.mouse.wheel(0, -random.randint(300, 900))
    human_pause(2)

    box = None
    for sel in (
        'div[role="textbox"][contenteditable="true"]',
        'shreddit-composer div[contenteditable="true"]',
        'textarea[name="comment"]',
        'div[data-testid="comment-submission-form-richtext"] div[contenteditable="true"]',
    ):
        try:
            cand = page.locator(sel).first
            if cand.count() and cand.is_visible():
                box = cand
                break
        except Exception:
            continue

    if box is None:
        raise RuntimeError(
            "could not find the Reddit comment box. Are you logged in in that Chrome? "
            "Is the thread locked or archived?"
        )

    type_like_human(box, body)
    human_pause(2.5)

    for sel in (
        'button[slot="submit-button"]',
        'button:has-text("Comment")',
        'button[type="submit"]:has-text("Comment")',
    ):
        try:
            btn = page.locator(sel).first
            if btn.count() and btn.is_enabled():
                btn.click()
                break
        except Exception:
            continue
    else:
        raise RuntimeError("could not find the Comment submit button")

    human_pause(5)
    return page.url


# ---------------------------------------------------------------------------
# Quora
# ---------------------------------------------------------------------------

def post_quora_answer(page, question_url: str, body: str, dwell: float) -> str:
    page.goto(question_url, wait_until="domcontentloaded")
    human_pause(3)

    print(f"    reading question for ~{dwell:.0f}s ...")
    for _ in range(max(int(dwell / 5), 1)):
        page.mouse.wheel(0, random.randint(250, 700))
        human_pause(5)

    for sel in ('div:has-text("Answer") >> button', 'button:has-text("Answer")'):
        try:
            btn = page.locator(sel).first
            if btn.count() and btn.is_visible():
                btn.click()
                human_pause(3)
                break
        except Exception:
            continue

    box = None
    for sel in (
        'div[contenteditable="true"][role="textbox"]',
        'div.doc[contenteditable="true"]',
        'div[contenteditable="true"]',
    ):
        try:
            cand = page.locator(sel).first
            if cand.count() and cand.is_visible():
                box = cand
                break
        except Exception:
            continue

    if box is None:
        raise RuntimeError(
            "could not find the Quora answer editor. Are you logged in in that Chrome?"
        )

    type_like_human(box, body)
    human_pause(3)

    for sel in ('button:has-text("Post")', 'button:has-text("Submit")'):
        try:
            btn = page.locator(sel).first
            if btn.count() and btn.is_enabled():
                btn.click()
                break
        except Exception:
            continue
    else:
        raise RuntimeError("could not find the Quora Post button")

    human_pause(5)
    return page.url


# ---------------------------------------------------------------------------
# Orchestration
# ---------------------------------------------------------------------------

def run_one(site: dict, thread_id: str | None = None, auto_yes: bool = False,
            mode: str = "profile") -> bool:
    """Post the single best approved draft for one site.

    Site-scoped on purpose: limits, link ratio and the browser profile all
    belong to this site's own account. Posting site A's draft through site B's
    session is exactly the cross-contamination the per-site design prevents.
    """
    rows = ([db.get_thread(thread_id)] if thread_id
            else db.threads_by_status("approved", limit=25))
    rows = [r for r in rows if r and r["site_id"] == site["id"]]
    if not rows:
        print("nothing approved and waiting for this site. "
              "Run `draft` then `ingest` first.")
        return False

    for row in rows:
        draft = db.latest_draft(row["id"])
        kind = (draft["kind"] if draft else "comment") or "comment"

        ok, reason = limits.check(site, row["platform"], row["community"], kind)
        if not ok:
            print(f"  - {row['id']} blocked: {reason}")
            continue

        if not draft:
            print(f"  - {row['id']} has no draft body")
            continue

        linked, total, ratio = limits.link_ratio(site["id"], 30)
        print("\n" + "=" * 70)
        print(f"SITE     : #{site['id']} {site['name'] or site['url']}")
        print(f"ACCOUNT  : {site['reddit_user'] or '(not set!)'}")
        print(f"KIND     : {kind}")
        print(f"PLATFORM : {row['platform']}  ({row['community']})")
        print(f"THREAD   : {row['title'][:100]}")
        print(f"URL      : {row['url'] or '(new thread -- will be created)'}")
        print(f"SCORE    : {row['score']}")
        print(f"LINK     : {draft['link_url'] or '(none)'}")
        print(f"RATIO    : {linked}/{total} linked in 30d = {ratio:.0%}")
        print("-" * 70)
        print(draft["body"])
        print("=" * 70)

        if not auto_yes:
            try:
                ans = input("Send this? [y/N/s=skip thread] ").strip().lower()
            except EOFError:
                print("no TTY for confirmation; re-run with --yes to auto-send")
                return False
            if ans == "s":
                db.set_status(row["id"], "skipped")
                continue
            if ans != "y":
                print("not sent.")
                return False

        dwell = 25.0
        with session(site, mode=mode) as page:
            page.set_default_timeout(45000)

            # Identity check. The profile is per-site, but a profile can be
            # logged out or logged into the wrong account, and posting under
            # the wrong identity is not something you can undo.
            who = whoami(page)
            if not who:
                print(f"\n  This profile is not logged in.\n"
                      f"  Run:  python run.py connect --site {site['id']}")
                return False

            want = (site.get("reddit_user") or "").lstrip("u/")
            if want and who != "logged-in" and who.lower() != want.lower():
                print(f"\n  WRONG ACCOUNT. Site #{site['id']} expects u/{want} "
                      f"but this profile is logged in as u/{who}.")
                print("  Refusing to post. Fix with:")
                print(f"    python run.py connect --site {site['id']}")
                return False
            print(f"  posting as u/{who}")

            try:
                if row["platform"] == "reddit":
                    permalink = post_reddit_comment(page, row["url"],
                                                    draft["body"], dwell)
                else:
                    permalink = post_quora_answer(page, row["url"],
                                                  draft["body"], dwell)

                with db.conn() as cx:
                    cx.execute(
                        "INSERT INTO posts (site_id, thread_id, draft_id, "
                        "platform, kind, community, permalink, had_link, "
                        "posted_at) VALUES (?,?,?,?,?,?,?,?,?)",
                        (site["id"], row["id"], draft["id"], row["platform"],
                         kind, row["community"], permalink,
                         1 if draft["link_url"] else 0, time.time()))
                db.set_status(row["id"], "posted")
                db.approve_draft(draft["id"])
                print(f"\n  posted -> {permalink}")
                return True
            except Exception as e:
                print(f"\n  ! post failed: {e}", file=sys.stderr)
                return False

    print("no eligible thread right now (all blocked by rails).")
    return False
