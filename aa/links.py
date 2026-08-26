"""Link tiering: which page may be linked on which thread.

Measured, not assumed. Every value page is fetched once and scored for how
heavily it is branded to the site's own vertical:

    UNIVERSAL  - reads fine to any small business. Linkable on broad threads.
    SEMI       - mostly generic, some vertical language. Linkable when the
                 thread is at least adjacent to the vertical.
    LOCKED     - saturated with vertical terms. Only linkable on threads
                 genuinely about that vertical, or on our own created threads.

This exists because of a real finding: every one of one client's 12 free
tools measured LOCKED (20-74 vertical terms per 1k words), including the
Citation Checker, which is functionally universal but says "limo" 45 times. A
plumber clicking that bounces, and the comment reads as spam. So the gate is
enforced rather than left to the drafting model's judgement.
"""
from __future__ import annotations

import re

from . import niche

# Density thresholds, terms-per-1000-words.
LOCKED_AT = 12.0
SEMI_AT = 4.0

TIER_RANK = {"universal": 0, "semi": 1, "locked": 2}


def vertical_pattern(terms: list[str]) -> re.Pattern:
    if not terms:
        return re.compile(r"(?!x)x")          # matches nothing
    body = "|".join(re.escape(t.strip()) for t in terms if t.strip())
    return re.compile(rf"\b({body})\b", re.I)


def vertical_terms(site: dict) -> list[str]:
    """Words that mark content as belonging to this site's vertical.
    Derived from the niche/audience text plus its own top terms."""
    text = f"{site.get('niche','')} {site.get('audience','')} {site.get('name','')}"
    words = re.findall(r"[a-z]{4,}", text.lower())
    generic = {
        "seo", "marketing", "agency", "google", "search", "local", "business",
        "businesses", "website", "websites", "online", "digital", "growth",
        "system", "built", "only", "vertical", "score", "audit", "minutes",
        "free", "your", "with", "that", "this", "from", "have", "more", "book",
        "booking", "service", "services", "company", "companies", "operators",
    }
    base = [w for w in dict.fromkeys(words) if w not in generic]
    # keep the distinctive ones -- these are the vertical's vocabulary
    return base[:12]


def classify_page(url: str, terms: list[str]) -> dict:
    """Fetch a page and measure its vertical density."""
    html = niche.fetch(url)
    if not html:
        return {"url": url, "tier": "unknown", "density": None,
                "words": 0, "error": "fetch failed"}

    text = re.sub(r"\s+", " ", niche.strip_html(html))
    words = len(text.split())
    if words < 50:
        return {"url": url, "tier": "unknown", "density": None,
                "words": words, "error": "too little text"}

    hits = len(vertical_pattern(terms).findall(text))
    density = hits / words * 1000.0
    tier = ("locked" if density > LOCKED_AT
            else "semi" if density > SEMI_AT else "universal")
    return {"url": url, "tier": tier, "density": round(density, 1),
            "words": words, "hits": hits}


def classify_site(site: dict, verbose: bool = True) -> list[dict]:
    """Tier every value page. Returns the updated value_pages list."""
    terms = vertical_terms(site)
    if verbose:
        print(f"  vertical terms: {', '.join(terms)}")

    pages = []
    for v in site.get("value_pages", []):
        res = classify_page(v["url"], terms)
        page = dict(v)
        page["tier"] = res["tier"]
        page["density"] = res["density"]
        pages.append(page)
        if verbose:
            d = f"{res['density']:.1f}" if res["density"] is not None else "  ? "
            print(f"  {res['tier'].upper():<10} {d:>6}/1k  {v['title'][:42]}")
    return pages


# --------------------------------------------------------------------------
# the gate
# --------------------------------------------------------------------------

def thread_affinity(text: str, terms: list[str]) -> float:
    """How much does this thread already live in our vertical? 0..1."""
    if not text:
        return 0.0
    words = max(len(text.split()), 1)
    hits = len(vertical_pattern(terms).findall(text))
    return min(hits / words * 1000.0 / LOCKED_AT, 1.0)


def allowed_tier(affinity: float) -> str:
    """The most vertical-specific page we may link given the thread's context."""
    if affinity >= 0.55:
        return "locked"        # thread is clearly about our vertical
    if affinity >= 0.15:
        return "semi"
    return "universal"


def choose_link(thread_text: str, site: dict, pages: list[dict] | None = None
                ) -> tuple[str, str]:
    """Pick a linkable page for this thread, or ('', reason) if none fits.

    Returning no link is a normal, frequent outcome -- most comments should
    carry none. The caller must not treat '' as a failure.
    """
    pages = pages if pages is not None else site.get("value_pages", [])
    terms = vertical_terms(site)
    aff = thread_affinity(thread_text, terms)
    ceiling = allowed_tier(aff)

    eligible = [p for p in pages
                if TIER_RANK.get(p.get("tier", "locked"), 2) <= TIER_RANK[ceiling]]
    if not eligible:
        return "", (f"no page at or below tier '{ceiling}' "
                    f"(thread affinity {aff:.2f}) -- comment with no link")

    # Prefer the most specific page we are still allowed to use: a semi page on
    # an adjacent thread beats a universal one, it is more useful to the asker.
    eligible.sort(key=lambda p: TIER_RANK.get(p.get("tier", "locked"), 2),
                  reverse=True)
    best = eligible[0]
    return best["url"], (f"tier={best.get('tier')} affinity={aff:.2f} "
                         f"ceiling={ceiling}")


def summarize(pages: list[dict]) -> dict[str, int]:
    out = {"universal": 0, "semi": 0, "locked": 0, "unknown": 0}
    for p in pages:
        out[p.get("tier", "unknown")] = out.get(p.get("tier", "unknown"), 0) + 1
    return out
