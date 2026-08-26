"""Relevance scoring -- decides which threads are worth your account's reputation.

A reply only builds authority if the thread genuinely needed what we know.

The hard part is not ranking good threads, it is REJECTING bad ones. Reddit's
search never returns an empty result set: when a query has few genuine matches
it silently pads the page with unrelated posts. Measured directly -- searching
"limousine" in r/Entrepreneur returned car-wash and food-bank threads, and
"limo" in r/smallbusiness returned "Cactus. Khakis.". Nothing upstream filters
those out, so this module has to, via a hard keyword gate that runs BEFORE the
soft numeric score. Without it the agent drafts limo-SEO replies onto car-wash
threads.
"""
from __future__ import annotations

import math
import re
import time

from . import db, links

STOP = {
    "the", "a", "an", "and", "or", "but", "if", "to", "of", "in", "on", "for",
    "with", "is", "are", "was", "were", "be", "been", "my", "your", "our", "i",
    "you", "we", "it", "this", "that", "how", "do", "does", "can", "should",
    "what", "why", "get", "got", "any", "have", "has", "not", "no", "so", "at",
    "from", "by", "as", "me", "am", "will", "would", "there", "their", "about",
}

QUESTION_HINTS = (
    "how do", "how can", "how to", "what is", "what's", "why is", "why does",
    "anyone know", "any advice", "need help", "help with", "recommend",
    "suggestions", "struggling", "stuck", "confused", "best way", "should i",
    "is it worth", "does anyone", "advice on", "tips for",
)

# A thread must contain at least this many distinct content words drawn from
# the site's keyword set before it is even considered. This is the filler gate.
MIN_KEYWORD_HITS = 2
MIN_RELEVANCE = 0.22


def stem(w: str) -> str:
    """Crude singularisation so "businesses" matches "business". Good enough --
    the alternative is a stemming dependency for four suffixes."""
    for suf in ("ies", "es", "s"):
        if w.endswith(suf) and len(w) - len(suf) >= 4:
            return w[: -len(suf)] + ("y" if suf == "ies" else "")
    return w


def tokens(text: str) -> set[str]:
    words = re.findall(r"[a-z0-9']+", (text or "").lower())
    return {stem(w) for w in words if w not in STOP and len(w) > 2}


def keyword_terms(keywords: list[str]) -> set[str]:
    """Flatten multi-word keywords into the distinctive words inside them."""
    out: set[str] = set()
    for k in keywords:
        out |= {w for w in tokens(k) if len(w) > 3}
    return out


def keyword_fit(text: str, keyword: str) -> float:
    """How well does ONE keyword match this thread? 0..1.

    Scored per keyword rather than against the union of every keyword. The
    union version fails badly: a thread matching one keyword perfectly still
    only overlaps ~4 of 40 pooled words, which reads as 0.10 and gets rejected.
    """
    kw_terms = {w for w in tokens(keyword) if len(w) > 3}
    if not kw_terms:
        return 0.0
    if keyword.lower() in (text or "").lower():
        return 1.0                       # exact phrase present
    hits = tokens(text) & kw_terms
    # A single shared word is coincidence ("business" appears everywhere).
    if len(hits) < 2:
        return 0.0

    # Weight by how specific the keyword is. "ppc for local business" reduces
    # to just {local, business}, so any thread mentioning a local business
    # matched it at 1.0 and claimed to be about PPC. A keyword carrying more
    # distinctive words is stronger evidence when it matches.
    specificity = min(len(kw_terms) / 4.0, 1.0)
    return (len(hits) / len(kw_terms)) * (0.55 + 0.45 * specificity)


def best_keyword(text: str, keywords: list[str]) -> tuple[str, float]:
    best, score_ = "", 0.0
    for k in keywords:
        f = keyword_fit(text, k)
        if f > score_:
            best, score_ = k, f
    return best, score_


def gate(text: str, keywords: list[str]) -> tuple[bool, dict]:
    """Hard pass/fail before any scoring. A thread must match at least one
    keyword on two or more of its distinctive words."""
    kw, fit = best_keyword(text, keywords)
    n_matching = sum(1 for k in keywords if keyword_fit(text, k) > 0)
    return fit > 0.0, {"best_keyword": kw, "best_fit": round(fit, 3),
                       "keywords_matched": n_matching}


def relevance(text: str, keywords: list[str]) -> float:
    """0..1 topical fit. Driven by the single best-matching keyword, with a
    small bonus when several keywords match -- that signals a thread sitting
    squarely in our topic rather than glancing off it."""
    if not keywords:
        return 0.0
    _, best = best_keyword(text, keywords)
    if best <= 0.0:
        return 0.0
    breadth = sum(1 for k in keywords if keyword_fit(text, k) > 0)
    return min(best + 0.05 * (breadth - 1), 1.0)


def recency_factor(created_utc: float, half_life_hours: float = 336.0) -> float:
    """1.0 for brand new, halving every half_life. 0.5 when age is unknown."""
    if not created_utc:
        return 0.5
    age_h = max((time.time() - created_utc) / 3600.0, 0.0)
    return math.pow(0.5, age_h / max(half_life_hours, 1.0))


def engagement_factor(num_comments: int, max_comments: int = 80) -> float:
    """Sweet spot: some traction, not already answered to death."""
    if num_comments > max_comments:
        return 0.1
    if num_comments <= 2:
        return 1.0
    if num_comments <= 15:
        return 0.8
    if num_comments <= 40:
        return 0.5
    return 0.3


def score_thread(row, site: dict) -> tuple[float, dict, str]:
    text = f"{row['title']}\n{row['body'] or ''}"
    keywords = site.get("keywords") or []

    # Community gate. Threads found under an older targeting list stay in the
    # database, so without this, dropping a subreddit leaves its threads sitting
    # in the queue forever. Re-scoring now re-filters them automatically.
    allowed = {c.lower() for c in (site.get("communities") or [])}
    community = (row["community"] or "").lower()
    if allowed and community and community not in allowed:
        return 0.0, {"rejected": f"r/{row['community']} is not targeted"}, ""

    passed, why = gate(text, keywords)
    if not passed:
        return 0.0, {"rejected": "failed keyword gate", **why}, ""

    rel = relevance(text, keywords)
    if rel < MIN_RELEVANCE:
        return 0.0, {"rejected": f"relevance {rel:.2f} < {MIN_RELEVANCE}",
                     "relevance": round(rel, 3), **why}, ""

    rec = recency_factor(row["created_utc"])
    eng = engagement_factor(int(row["num_comments"] or 0))
    is_question = ("?" in (row["title"] or "")) or any(
        h in text.lower() for h in QUESTION_HINTS)
    q_bonus = 1.0 if is_question else 0.55

    link_url, link_reason = links.choose_link(text, site)

    total = 100.0 * (rel ** 0.85) * (0.45 + 0.30 * rec + 0.25 * eng) * q_bonus

    detail = {
        "relevance": round(rel, 3), "recency": round(rec, 3),
        "engagement": round(eng, 3), "is_question": is_question,
        "link": link_url, "link_reason": link_reason, **why,
    }
    return round(total, 2), detail, link_url


def run(site: dict, min_score: float = 40.0, verbose: bool = False) -> dict:
    """Score this site's unscored threads and promote the good ones."""
    rows = [r for r in db.threads_by_status("new", limit=3000)
            if r["site_id"] == site["id"]]

    queued = skipped = gated = 0
    for row in rows:
        total, detail, page = score_thread(row, site)
        if detail.get("rejected"):
            gated += 1
            db.set_score(row["id"], total, detail, page, "skipped")
        elif total >= min_score:
            queued += 1
            db.set_score(row["id"], total, detail, page, "queued")
            if verbose:
                print(f"  QUEUE {total:>6.1f}  {row['title'][:58]}")
        else:
            skipped += 1
            db.set_score(row["id"], total, detail, page, "skipped")
            if verbose:
                print(f"  low   {total:>6.1f}  {row['title'][:58]}")

    return {"scored": len(rows), "queued": queued,
            "below_min": skipped, "rejected_by_gate": gated}
