#!/usr/bin/env python3
"""Offline smoke test. Seeds fake threads and runs scoring + drafting + rails.

Touches NO live platform and sends nothing. Safe to run any time.
    python smoke_test.py
"""
from __future__ import annotations

import time

from aa import config, db, draft, limits, score

FAKE = [
    # Should score HIGH: on-topic question, fresh, few comments
    dict(platform="reddit", native_id="t_hi", url="https://reddit.com/r/x/1",
         title="How do I fix my Google Business Profile not showing in the map pack?",
         body="Set up GBP two months ago, verified, but we never appear in the map pack "
              "for our own city. Categories are set. What am I missing?",
         author="someone", community="smallbusiness",
         created_utc=time.time() - 3600, num_comments=3, upvotes=12),

    # Should score MID: on-topic but old and busy
    dict(platform="reddit", native_id="t_mid", url="https://reddit.com/r/x/2",
         title="Local SEO citations, which directories actually matter in 2026?",
         body="Been told to submit NAP to 200 directories. Feels like spam. "
              "Which listings are worth it?",
         author="someone", community="SEO",
         created_utc=time.time() - 86400 * 9, num_comments=55, upvotes=40),

    # Should be SKIPPED: totally off-topic
    dict(platform="reddit", native_id="t_low", url="https://reddit.com/r/x/3",
         title="What laptop should I buy for video editing under $1200?",
         body="Looking at the M3 Air vs a Lenovo. Mostly Premiere.",
         author="someone", community="smallbusiness",
         created_utc=time.time() - 7200, num_comments=6, upvotes=9),

    # Quora-shaped, no timestamp
    dict(platform="quora", native_id="How-do-I-rank-on-Google-Maps",
         url="https://www.quora.com/How-do-I-rank-on-Google-Maps",
         title="How do I rank on Google Maps for my local business?",
         body="", author="", community="quora",
         created_utc=0, num_comments=0, upvotes=0),
]


def ensure_site(cfg) -> dict:
    """Return a site row, creating one from config.yaml if the DB is empty.

    Everything downstream is site-aware, so the tests need a site to exist.
    Built directly in the DB rather than through `run.py add-site`, which would
    fetch the live website to detect its niche. This stays fully offline.
    """
    sites = db.list_sites()
    if sites:
        return sites[0]

    # Value pages are tiered universal/semi/locked by links.classify_site(),
    # which fetches each page to measure how vertical-specific its wording is.
    # That needs the network, so the fixture marks them universal by hand.
    # A real site gets its tiers measured during `run.py add-site`.
    pages = [{**p, "tier": "universal"} for p in cfg.get("value_pages", [])]

    site_id = db.add_site(
        url=cfg.get_path("site.url", "https://example.com"),
        name=cfg.get_path("site.name", "Test Site"),
        niche="local services",
        audience="small business owners",
        keywords=[k for p in pages for k in p.get("keywords", [])],
        communities=cfg.get_path("reddit.subreddits", []),
        value_pages=pages,
    )
    return db.get_site(site_id)


def main():
    cfg = config.load()
    db.init()
    site = ensure_site(cfg)

    print("=" * 68)
    print("1. SEEDING")
    for row in FAKE:
        fresh = db.upsert_thread({**row, "site_id": site["id"]})
        print(f"   {'new ' if fresh else 'dup '} {row['native_id']}")

    print("\n" + "=" * 68)
    print("2. SCORING")
    r = score.run(site, min_score=cfg.get_path("scoring.min_score", 40))
    print(f"   scored={r['scored']} queued={r['queued']} "
          f"below_min={r['below_min']} rejected_by_gate={r['rejected_by_gate']}")
    for row in db.threads_by_status("queued", limit=10):
        print(f"   {row['score']:6.1f}  [{row['community']}] {row['title'][:56]}")
        print(f"           page={row['matched_page'] or '(none)'}")
    print("   --- below threshold ---")
    for row in db.threads_by_status("skipped", limit=10):
        print(f"   {row['score']:6.1f}  {row['title'][:56]}")

    print("\n" + "=" * 68)
    print("3. LINK POLICY (enforced in Python, not by the model)")
    for row in db.threads_by_status("queued", limit=10):
        ok, url = draft.should_link(cfg, row)
        print(f"   {row['native_id']:<32} link={'YES ' + url if ok else 'NO'}")

    print("\n" + "=" * 68)
    print("4. PROMPT BUILD (claude engine, writes files, calls nothing)")
    d = draft.run_for_site(site, limit=10, engine=cfg.get_path("draft.engine", "claude"))
    print(f"   drafted={d['drafted']} skipped={d['skipped']}")
    pending = list((config.DRAFT_DIR / "pending").glob("*.md"))
    print(f"   {len(pending)} prompt files in drafts/pending/")

    print("\n" + "=" * 68)
    print("5. SAFETY RAILS")
    for plat, comm in (("reddit", "smallbusiness"), ("quora", "quora")):
        ok, why = limits.check(site, plat, comm)
        print(f"   {plat}/{comm}: {'ALLOWED' if ok else 'BLOCKED -> ' + why}")

    linked, total, ratio = limits.link_ratio(site["id"])
    print(f"   link ratio 30d: {linked}/{total} = {ratio:.0%}")

    print("\n" + "=" * 68)
    print("SMOKE TEST DONE. Nothing was posted.")
    print("Sample prompt below:\n")
    if pending:
        print(pending[0].read_text(encoding="utf-8")[:1400])


if __name__ == "__main__":
    main()
