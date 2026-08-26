"""Activity reporting: how many threads created and comments posted, per period.

Everything reads from the posts table, which is the only record of what actually
went live. Drafts that were never approved are deliberately not counted -- the
report answers "what did we publish", not "what did we generate".
"""
from __future__ import annotations

import time
from datetime import datetime, timedelta, timezone

from .db import conn

DAY = 86400.0
PERIODS = {"daily": DAY, "weekly": 7 * DAY, "monthly": 30 * DAY}


def totals(site_id: int | None = None, since: float | None = None) -> dict:
    """Counts split by kind. `since` is a unix timestamp; None means all time."""
    sql = ("SELECT kind, COUNT(*) n, SUM(had_link) linked, "
           "SUM(upvotes) ups, SUM(replies) reps FROM posts WHERE 1=1")
    args: list = []
    if site_id is not None:
        sql += " AND site_id=?"
        args.append(site_id)
    if since is not None:
        sql += " AND posted_at>=?"
        args.append(since)
    sql += " GROUP BY kind"

    out = {"threads": 0, "comments": 0, "linked": 0, "upvotes": 0, "replies": 0}
    with conn() as cx:
        for r in cx.execute(sql, args).fetchall():
            key = "threads" if r["kind"] == "thread" else "comments"
            out[key] += r["n"]
            out["linked"] += r["linked"] or 0
            out["upvotes"] += r["ups"] or 0
            out["replies"] += r["reps"] or 0
    out["total"] = out["threads"] + out["comments"]
    out["link_ratio"] = round(out["linked"] / out["total"], 3) if out["total"] else 0.0
    return out


def summary(site_id: int | None = None) -> dict:
    """The three headline periods plus all-time."""
    now = time.time()
    d = {name: totals(site_id, now - secs) for name, secs in PERIODS.items()}
    d["all_time"] = totals(site_id, None)
    return d


def by_day(days: int = 30, site_id: int | None = None) -> list[dict]:
    """One row per calendar day (UTC), newest first. Days with no activity are
    included as zeros so a chart does not silently skip them."""
    now = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
    start = now - timedelta(days=days - 1)

    sql = ("SELECT date(posted_at,'unixepoch') d, kind, COUNT(*) n "
           "FROM posts WHERE posted_at>=?")
    args: list = [start.timestamp()]
    if site_id is not None:
        sql += " AND site_id=?"
        args.append(site_id)
    sql += " GROUP BY d, kind"

    buckets: dict[str, dict] = {}
    with conn() as cx:
        for r in cx.execute(sql, args).fetchall():
            b = buckets.setdefault(r["d"], {"threads": 0, "comments": 0})
            b["threads" if r["kind"] == "thread" else "comments"] += r["n"]

    rows = []
    for i in range(days):
        day = (start + timedelta(days=i)).strftime("%Y-%m-%d")
        b = buckets.get(day, {"threads": 0, "comments": 0})
        rows.append({"date": day, **b, "total": b["threads"] + b["comments"]})
    rows.reverse()
    return rows


def by_community(site_id: int | None = None, since: float | None = None) -> list[dict]:
    """Where the activity actually landed -- useful for spotting a sub that is
    silently eating every comment."""
    sql = ("SELECT community, platform, COUNT(*) n, SUM(had_link) linked, "
           "SUM(upvotes) ups FROM posts WHERE 1=1")
    args: list = []
    if site_id is not None:
        sql += " AND site_id=?"
        args.append(site_id)
    if since is not None:
        sql += " AND posted_at>=?"
        args.append(since)
    sql += " GROUP BY community, platform ORDER BY n DESC"
    with conn() as cx:
        return [
            {"community": r["community"] or "(none)", "platform": r["platform"],
             "posts": r["n"], "linked": r["linked"] or 0, "upvotes": r["ups"] or 0}
            for r in cx.execute(sql, args).fetchall()
        ]


def render_text(site_id: int | None = None, site_name: str = "all sites") -> str:
    """Console version of the report."""
    s = summary(site_id)
    lines = [
        "=" * 58,
        f"ACTIVITY REPORT -- {site_name}",
        "=" * 58,
        f"{'period':<12}{'threads':>9}{'comments':>10}{'total':>8}{'w/link':>8}",
        "-" * 58,
    ]
    for label in ("daily", "weekly", "monthly", "all_time"):
        t = s[label]
        name = {"daily": "last 24h", "weekly": "last 7d",
                "monthly": "last 30d", "all_time": "all time"}[label]
        lines.append(f"{name:<12}{t['threads']:>9}{t['comments']:>10}"
                     f"{t['total']:>8}{t['linked']:>8}")

    mo = s["monthly"]
    lines += [
        "-" * 58,
        f"30d link ratio: {mo['link_ratio']:.0%}  "
        f"(healthy is under 30%)",
        f"30d engagement: {mo['upvotes']} upvotes, {mo['replies']} replies",
    ]

    comms = by_community(site_id, time.time() - 30 * DAY)
    if comms:
        lines += ["", "WHERE (last 30d)", "-" * 58]
        for c in comms[:12]:
            lines.append(f"  {c['platform']:<8} {c['community']:<26} "
                         f"{c['posts']:>3} posts  {c['upvotes']:>4} ups")

    recent = [r for r in by_day(14, site_id) if r["total"]]
    if recent:
        lines += ["", "RECENT DAYS", "-" * 58]
        for r in recent[:14]:
            lines.append(f"  {r['date']}   threads {r['threads']}   "
                         f"comments {r['comments']}")
    return "\n".join(lines)
