"""Daily run. One entry point for cron on the VPS.

    python daily.py              -> every active site
    python daily.py --site 1     -> one site
    python daily.py --dry-run    -> do everything except write drafts

Stages, in order:

    discover -> enrich -> score -> propose threads -> draft comments

It deliberately stops before posting. Discovery and drafting are safe to
automate; publishing under your account is not, so the last step stays manual
until you have watched a few batches and trust the output. When you are ready,
`post.py` is the piece that sends -- this scheduler only fills the queue.

Pacing: sites are processed one at a time with a gap between them. Running six
sites' discovery in parallel from one VPS IP is the single most obvious
automation signal you could produce.
"""
from __future__ import annotations

import random
import time
import traceback
from datetime import datetime, timezone

from . import browser, db, draft, limits, reddit, score, threads


def log(msg: str, prefix: str = "") -> None:
    ts = datetime.now(timezone.utc).strftime("%H:%M:%S")
    print(f"[{ts}] {prefix}{msg}", flush=True)


def run_site(site: dict, window: str = "month", dry_run: bool = False,
             max_probes: int | None = None) -> dict:
    """One site's full daily cycle."""
    sid, name = site["id"], site["name"] or site["url"]
    log(f"=== {name} (site #{sid}) ===")

    stats = {"site_id": sid, "name": name}

    keywords = site.get("keywords") or []
    communities = site.get("communities") or []
    if not keywords or not communities:
        log("  no keywords or communities configured, skipping")
        return {**stats, "error": "not configured"}

    # ---- discover -------------------------------------------------------
    probes = [(c, k) for c in communities for k in keywords]
    random.shuffle(probes)          # vary the order day to day
    if max_probes:
        probes = probes[:max_probes]

    seen = db.known_ids("reddit")
    found = new = 0
    try:
        with browser.fresh(headless=True) as page:
            for i, (sub, kw) in enumerate(probes, 1):
                rows = reddit.search(page, kw, subreddit=sub,
                                     window=window, limit=25)
                fresh_rows = [r for r in rows if r["native_id"] not in seen]
                found += len(rows)
                for r in fresh_rows:
                    seen.add(r["native_id"])
                    if db.upsert_thread({
                            "site_id": sid, "platform": "reddit",
                            "native_id": r["native_id"], "url": r["url"],
                            "title": r["title"], "community": r["community"],
                            "status": "new", "discovered_at": time.time()}):
                        new += 1
                if i % 5 == 0 or i == len(probes):
                    log(f"  discover {i}/{len(probes)}  +{new} new")
                browser.human_pause(2.0, 4.5)
    except Exception as e:
        log(f"  discovery failed: {e}")
        stats["discover_error"] = str(e)

    stats.update(found=found, new=new)

    # ---- enrich ---------------------------------------------------------
    try:
        enriched = reddit.enrich(sid, limit=40, headless=True, verbose=False)
        log(f"  enriched {enriched}")
        stats["enriched"] = enriched
    except Exception as e:
        log(f"  enrich failed: {e}")
        stats["enrich_error"] = str(e)

    # ---- score ----------------------------------------------------------
    res = score.run(db.get_site(sid), min_score=40.0)
    log(f"  scored {res['scored']} -> {res['queued']} queued, "
        f"{res['rejected_by_gate']} rejected as filler")
    stats.update(res)

    # ---- account health -------------------------------------------------
    ok, have, need = limits.warmed_up(db.get_site(sid))
    linked, total, ratio = limits.link_ratio(sid, 30)
    log(f"  account: {have} posts all-time"
        + ("" if ok else f" (warm-up {have}/{need}, no links yet)")
        + f" | 30d link ratio {ratio:.0%} ({linked}/{total})")
    stats.update(warmed_up=ok, posts=have, link_ratio=round(ratio, 3))

    if dry_run:
        log("  dry run, stopping before drafts")
        return stats

    # ---- propose threads ------------------------------------------------
    try:
        allowed, why = limits.check(db.get_site(sid), "reddit", "", "thread")
        if allowed:
            props = threads.propose(db.get_site(sid), limit=3,
                                    check_rules=True, verbose=False)
            log(f"  {len(props)} thread topics proposed")
            for p in props:
                flag = " [needs review]" if p["needs_review"] else ""
                log(f"     r/{p['sub']}: \"{p['theme']}\" "
                    f"({p['mentions']} asks){flag}")
            stats["thread_proposals"] = len(props)
        else:
            log(f"  no thread today: {why}")
            stats["thread_proposals"] = 0
    except Exception as e:
        log(f"  thread proposal failed: {e}")
        stats["thread_error"] = str(e)

    # ---- draft comments -------------------------------------------------
    try:
        cap = int(limits.cfg_val(db.get_site(sid), "reddit_comments_per_day"))
        d = draft.run_for_site(db.get_site(sid), limit=cap * 2)
        log(f"  drafts: {d}")
        stats.update(drafts=d)
    except Exception as e:
        log(f"  drafting failed: {e}")
        stats["draft_error"] = str(e)

    return stats


def run_all(site_id: int | None = None, window: str = "month",
            dry_run: bool = False, max_probes: int | None = None) -> list[dict]:
    db.init()
    sites = ([db.get_site(site_id)] if site_id
             else db.list_sites(active_only=True))
    sites = [s for s in sites if s]
    if not sites:
        log("no active sites")
        return []

    log(f"daily run: {len(sites)} site(s)")
    out = []
    for i, s in enumerate(sites):
        try:
            out.append(run_site(s, window=window, dry_run=dry_run,
                                max_probes=max_probes))
        except Exception:
            log(f"  site {s['id']} crashed:\n{traceback.format_exc()[-900:]}")
            out.append({"site_id": s["id"], "error": "crashed"})

        # Gap between sites. Different accounts, same IP -- back-to-back bursts
        # are the giveaway.
        if i < len(sites) - 1:
            wait = random.uniform(240, 600)
            log(f"  waiting {wait/60:.1f} min before the next site")
            time.sleep(wait)

    log("=" * 52)
    for s in out:
        log(f"  {s.get('name','?'):<34} "
            f"+{s.get('new',0):>3} new, {s.get('queued',0):>3} queued"
            + (f"  ERROR: {s['error']}" if s.get("error") else ""))
    return out
