"""Reddit discovery through a real browser. No API app, no credentials.

Why not the API: Reddit's public .json endpoints now return 403 to unauthed
clients, and the OAuth script-app flow was not available to this account. The
web UI, however, serves search and thread pages to any logged-out browser --
which is all discovery needs. Posting is a separate concern (see post.py).

Important behaviour discovered in testing: Reddit search NEVER returns an empty
result set. When a query has few genuine matches it pads the page with
unrelated posts. So `search()` returns candidates only -- score.py is
responsible for rejecting the filler, and it must stay strict.
"""
from __future__ import annotations

import re
import time
from urllib.parse import quote

from . import browser, db

BASE = "https://www.reddit.com"

# Pull every /comments/ link on the page, dedup by post id.
_EXTRACT_RESULTS = """() => {
    const out = [], seen = new Set();
    document.querySelectorAll('a[href*="/comments/"]').forEach(a => {
        const href = a.getAttribute('href') || '';
        const m = href.match(/\\/r\\/([^\\/]+)\\/comments\\/([a-z0-9]+)/i);
        if (!m || seen.has(m[2])) return;
        const title = (a.innerText || '').trim();
        if (!title || title.length < 12) return;
        seen.add(m[2]);
        out.push({ native_id: m[2], community: m[1], title: title.slice(0, 300) });
    });
    return out;
}"""

_EXTRACT_THREAD = """() => {
    const h = document.querySelector('h1');
    const body = document.querySelector(
        'div[slot="text-body"], [data-post-click-location="text-body"]');
    const post = document.querySelector('shreddit-post');
    const comments = [];
    document.querySelectorAll('shreddit-comment').forEach(n => {
        const el = n.querySelector('div[slot="comment"]');
        const t = el ? el.innerText.trim() : '';
        if (t) comments.push({
            author: n.getAttribute('author') || '',
            score: parseInt(n.getAttribute('score') || '0', 10),
            body: t.slice(0, 1200),
        });
    });
    return {
        title: h ? h.innerText.trim() : '',
        body: body ? body.innerText.trim().slice(0, 6000) : '',
        author: post ? (post.getAttribute('author') || '') : '',
        upvotes: post ? parseInt(post.getAttribute('score') || '0', 10) : 0,
        num_comments: post ? parseInt(post.getAttribute('comment-count') || '0', 10) : comments.length,
        created: post ? (post.getAttribute('created-timestamp') || '') : '',
        comments: comments.slice(0, 25),
    };
}"""


def search_url(query: str, subreddit: str | None = None,
               sort: str = "relevance", window: str = "year") -> str:
    """sort=relevance matters. Testing showed sort=new on a site-wide search
    returns near-pure noise (r/vinyl for "limo marketing")."""
    q = quote(query)
    if subreddit:
        return (f"{BASE}/r/{subreddit}/search/?q={q}"
                f"&restrict_sr=1&sort={sort}&t={window}")
    return f"{BASE}/search/?q={q}&sort={sort}&t={window}"


def search(page, query: str, subreddit: str | None = None,
           window: str = "year", limit: int = 25) -> list[dict]:
    """One search. Returns raw candidates -- NOT vetted for relevance."""
    if not browser.goto(page, search_url(query, subreddit, window=window)):
        return []
    rows = page.evaluate(_EXTRACT_RESULTS)

    if subreddit:
        # A per-sub search can still surface other subs in "more results" rails.
        rows = [r for r in rows if r["community"].lower() == subreddit.lower()]

    for r in rows:
        r["platform"] = "reddit"
        r["url"] = f"{BASE}/r/{r['community']}/comments/{r['native_id']}/"
        r["query"] = query
    return rows[:limit]


def thread_detail(page, community: str, native_id: str) -> dict | None:
    """Full body + top comments. Needed before drafting -- replying to a title
    alone is exactly how a comment reads as generic and gets removed."""
    url = f"{BASE}/r/{community}/comments/{native_id}/"
    if not browser.goto(page, url, settle=2.0):
        return None
    page.mouse.wheel(0, 3000)          # comments lazy-load on scroll
    page.wait_for_timeout(2500)
    try:
        d = page.evaluate(_EXTRACT_THREAD)
    except Exception:
        return None
    d["url"] = url
    d["community"] = community
    d["native_id"] = native_id
    d["created_utc"] = _parse_ts(d.get("created", ""))
    return d


def _parse_ts(s: str) -> float:
    """created-timestamp is an ISO string like 2026-03-11T09:22:14.123Z."""
    if not s:
        return 0.0
    try:
        from datetime import datetime
        return datetime.fromisoformat(s.replace("Z", "+00:00")).timestamp()
    except (ValueError, TypeError):
        return 0.0


def discover(site: dict, window: str = "year", per_query: int = 25,
             headless: bool = True, verbose: bool = True) -> dict:
    """Run every (subreddit x keyword) probe for a site and store new threads.

    Returns counts. Threads land with status 'new'; scoring happens separately
    so a bad scorer can be re-run without re-crawling.
    """
    keywords = site.get("keywords") or []
    communities = site.get("communities") or []
    if not keywords:
        return {"error": "site has no keywords -- run niche detection first"}

    seen = db.known_ids("reddit")
    found = new = 0
    probes = [(c, k) for c in communities for k in keywords]

    with browser.fresh(headless=headless) as page:
        for i, (sub, kw) in enumerate(probes, 1):
            rows = search(page, kw, subreddit=sub, window=window, limit=per_query)
            fresh_rows = [r for r in rows if r["native_id"] not in seen]
            found += len(rows)

            for r in fresh_rows:
                seen.add(r["native_id"])
                if db.upsert_thread({
                    "site_id": site["id"], "platform": "reddit",
                    "native_id": r["native_id"], "url": r["url"],
                    "title": r["title"], "community": r["community"],
                    "status": "new", "discovered_at": time.time(),
                }):
                    new += 1

            if verbose:
                print(f"  [{i}/{len(probes)}] r/{sub:<20} {kw[:38]:<40} "
                      f"{len(rows):>2} found, {len(fresh_rows):>2} new")
            browser.human_pause(2.0, 4.5)

    return {"probes": len(probes), "found": found, "new": new}


def enrich(site_id: int, limit: int = 40, headless: bool = True,
           verbose: bool = True) -> int:
    """Fetch body + comments for threads that only have a title so far.

    Selected by MISSING BODY, not by status. Scoring moves threads out of 'new'
    into 'queued'/'skipped', so a status-based filter would silently stop
    enriching anything after the first score run.
    """
    with db.conn() as cx:
        rows = cx.execute(
            "SELECT * FROM threads WHERE site_id=? AND TRIM(COALESCE(body,''))='' "
            "AND status NOT IN ('posted','approved') "
            "ORDER BY score DESC LIMIT ?", (site_id, limit)).fetchall()
    if not rows:
        return 0

    done = 0
    with browser.fresh(headless=headless) as page:
        for r in rows:
            d = thread_detail(page, r["community"], r["native_id"])
            if d:
                with db.conn() as cx:
                    cx.execute(
                        "UPDATE threads SET body=?, author=?, upvotes=?, "
                        "num_comments=?, created_utc=? WHERE id=?",
                        (d["body"], d["author"], d["upvotes"], d["num_comments"],
                         d["created_utc"], r["id"]),
                    )
                done += 1
                if verbose:
                    print(f"  + {r['community']}/{r['native_id']}  "
                          f"{len(d['body'])} chars, {len(d['comments'])} comments")
            browser.human_pause(2.0, 4.0)
    return done


def subreddit_meta(page, sub: str) -> dict:
    """Existence + how alive a subreddit actually is.

    Reddit dropped the `subscribers` attribute from shreddit-subreddit-header.
    The old selector silently fell through to the first faceplate-number on the
    page, which is a POST VOTE COUNT -- that is where nonsense like
    "r/smallbusiness: 39 subscribers" came from.

    weekly-active-users is both still present and a better signal anyway: a
    500k-subscriber sub with 40 weekly posters is dead, and dead is what
    actually matters when choosing where to comment.
    """
    empty = {"name": sub, "exists": False, "subscribers": None,
             "weekly_active": None, "weekly_posts": None}
    if not browser.goto(page, f"{BASE}/r/{sub}/", settle=3.0):
        return empty
    try:
        d = page.evaluate("""() => {
            const el = document.querySelector('shreddit-subreddit-header');
            const body = document.body ? (document.body.innerText || '') : '';
            const banned = !!document.querySelector(
                'shreddit-forbidden, [data-testid="banned-page"]');
            const gone = /there aren.t any communities|community may have been banned|page not found/i.test(body);
            // "2,584,604 members" -- take the LARGEST match, the smaller ones
            // are sidebar recommendations for other communities.
            const nums = (body.match(/([\\d.,]+)\\s*members/gi) || [])
                .map(s => parseInt(s.replace(/[^\\d]/g, ''), 10))
                .filter(n => !isNaN(n));
            return {
                banned, gone,
                exists: !!el,
                members: nums.length ? Math.max(...nums) : null,
                active: el ? el.getAttribute('weekly-active-users') : null,
                posts: el ? el.getAttribute('weekly-contributions') : null,
            };
        }""")
    except Exception:
        return empty

    if d["banned"] or d["gone"] or not d["exists"]:
        return empty

    def num(v):
        try:
            return int(str(v).replace(",", ""))
        except (TypeError, ValueError):
            return None

    return {"name": sub, "exists": True, "subscribers": d["members"],
            "weekly_active": num(d["active"]), "weekly_posts": num(d["posts"])}
