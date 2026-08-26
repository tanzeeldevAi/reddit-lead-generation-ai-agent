"""Safety rails. These are the difference between an aged account and a banned one.

Every rule here is about staying inside normal human behaviour, not about hiding
from detection. If a rule blocks you, the answer is to wait, not to raise the cap.

All limits are PER SITE, because each site runs its own account. Counting them
globally would let one busy site silently consume another site's daily budget.
"""
from __future__ import annotations

import time

from . import db

DEFAULTS = {
    "reddit_comments_per_day": 4,
    "reddit_threads_per_day": 1,
    "quora_answers_per_day": 3,
    "min_minutes_between_actions": 35,
    "subreddit_cooldown_hours": 20,
    "thread_cooldown_days": 7,      # per-subreddit gap between OUR OWN threads
    "warmup_posts": 15,             # helpful comments before any link is allowed
    "max_link_ratio": 0.25,
}


def cfg_val(site: dict, key: str):
    lim = (site or {}).get("limits") or {}
    return lim.get(key, DEFAULTS[key])


def check(site: dict, platform: str, community: str,
          kind: str = "comment") -> tuple[bool, str]:
    """Return (allowed, reason_if_blocked) for one intended action."""
    site_id = site["id"]

    key = {
        ("reddit", "comment"): "reddit_comments_per_day",
        ("reddit", "thread"): "reddit_threads_per_day",
        ("quora", "answer"): "quora_answers_per_day",
        ("quora", "comment"): "quora_answers_per_day",
    }.get((platform, kind), "reddit_comments_per_day")
    cap = int(cfg_val(site, key))

    today = [p for p in db.posts_since(86400, platform, site_id)
             if (p["kind"] or "comment") == kind]
    if len(today) >= cap:
        return False, f"daily {kind} cap reached for {platform} ({len(today)}/{cap})"

    gap_min = float(cfg_val(site, "min_minutes_between_actions"))
    last = db.last_post_time(site_id)
    if last:
        waited = (time.time() - last) / 60.0
        if waited < gap_min:
            return False, (f"too soon, {gap_min - waited:.0f} min left of the "
                           f"{gap_min:.0f} min gap")

    if community:
        # Our own threads need a much longer gap than comments. Two self-posts
        # in the same subreddit inside a week is the pattern moderators ban for.
        if kind == "thread":
            days = float(cfg_val(site, "thread_cooldown_days"))
            recent = db.posts_since(days * 86400, platform, site_id)
            if any((r["community"] or "").lower() == community.lower()
                   and (r["kind"] or "") == "thread" for r in recent):
                return False, f"already posted a thread in {community} within {days:.0f}d"
        else:
            cool_h = float(cfg_val(site, "subreddit_cooldown_hours"))
            recent = db.posts_since(cool_h * 3600, platform, site_id)
            if any((r["community"] or "").lower() == community.lower()
                   for r in recent):
                return False, f"{community} is in cooldown ({cool_h:.0f}h)"

    return True, ""


def warmed_up(site: dict) -> tuple[bool, int, int]:
    """Has this account earned the right to link yet?"""
    need = int(cfg_val(site, "warmup_posts"))
    have = len(db.posts_since(365 * 86400, None, site["id"]))
    return have >= need, have, need


def link_ratio(site_id: int, days: int = 30) -> tuple[int, int, float]:
    posts = db.posts_since(days * 86400, None, site_id)
    linked = sum(1 for p in posts if p["had_link"])
    return linked, len(posts), (linked / len(posts) if posts else 0.0)


def may_link(site: dict) -> tuple[bool, str]:
    """Global link permission, independent of whether a page fits the thread."""
    ok, have, need = warmed_up(site)
    if not ok:
        return False, f"warm-up: {have}/{need} helpful posts so far"

    max_ratio = float(cfg_val(site, "max_link_ratio"))
    linked, total, _ = link_ratio(site["id"], 30)
    if total and (linked + 1) / (total + 1) > max_ratio:
        return False, (f"link ratio would hit "
                       f"{(linked+1)/(total+1):.0%}, cap is {max_ratio:.0%}")
    return True, ""
