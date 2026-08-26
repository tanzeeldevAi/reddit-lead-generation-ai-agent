"""Quora discovery.

Quora has no free public API, so we find questions the way a person would:
search-engine operators. DuckDuckGo's HTML endpoint needs no key and no account,
which keeps this layer free. We only read public search results and public
question titles -- nothing is scraped behind a login.
"""
from __future__ import annotations

import re
import time
from urllib.parse import parse_qs, unquote, urlparse

import requests
from bs4 import BeautifulSoup

from . import db

DDG = "https://html.duckduckgo.com/html/"
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "en-US,en;q=0.9",
}


def _clean_ddg_href(href: str) -> str:
    """DDG wraps results in a redirect; pull the real URL back out."""
    if not href:
        return ""
    if href.startswith("//"):
        href = "https:" + href
    parsed = urlparse(href)
    if "duckduckgo.com" in parsed.netloc and parsed.path.startswith("/l"):
        target = parse_qs(parsed.query).get("uddg", [""])[0]
        return unquote(target)
    return href


def search(query: str, limit: int) -> list[dict]:
    """Return public Quora question results for one query."""
    resp = requests.post(
        DDG,
        data={"q": f'site:quora.com "{query}"', "kl": "us-en"},
        headers=HEADERS,
        timeout=30,
    )
    resp.raise_for_status()
    soup = BeautifulSoup(resp.text, "html.parser")

    out: list[dict] = []
    for a in soup.select("a.result__a"):
        url = _clean_ddg_href(a.get("href", ""))
        if "quora.com/" not in url:
            continue
        # Skip profiles, topic hubs and answer permalinks -- we want questions.
        if any(seg in url for seg in ("/profile/", "/topic/", "/q/", "/unanswered/")):
            continue

        slug = urlparse(url).path.strip("/")
        if not slug or "/" in slug:  # real questions are a single path segment
            continue

        title = a.get_text(" ", strip=True)
        title = re.sub(r"\s*[-|]\s*Quora\s*$", "", title).strip()
        if not title:
            continue

        out.append({"url": url.split("?")[0], "slug": slug, "title": title})
        if len(out) >= limit:
            break
    return out


def to_row(hit: dict) -> dict:
    return {
        "platform": "quora",
        "native_id": hit["slug"][:180],
        "url": hit["url"],
        "title": hit["title"],
        "body": "",
        "author": "",
        "community": "quora",
        # Search results carry no timestamp; treat as neutral-age so recency
        # neither rewards nor punishes them.
        "created_utc": 0.0,
        "num_comments": 0,
        "upvotes": 0,
    }


def discover(cfg) -> dict[str, int]:
    queries = cfg.get_path("quora.queries", []) or []
    limit = int(cfg.get_path("quora.max_per_query", 20))

    seen = db.known_ids("quora")
    found = new = 0

    for q in queries:
        try:
            hits = search(q, limit)
        except Exception as e:
            print(f"  ! quora '{q}' -> {e}")
            continue

        for h in hits:
            found += 1
            if h["slug"][:180] in seen:
                continue
            if db.upsert_thread(to_row(h)):
                new += 1
                seen.add(h["slug"][:180])

        print(f"  quora :: '{q}' -> {len(hits)} hits")
        time.sleep(4)  # be polite to the free endpoint

    return {"found": found, "new": new}
