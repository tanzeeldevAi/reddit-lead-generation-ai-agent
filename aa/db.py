"""SQLite store. Threads discovered -> drafts written -> posts sent -> results tracked."""
from __future__ import annotations

import json
import re
import sqlite3
import time
from contextlib import contextmanager
from typing import Any, Iterable

from .config import DB_PATH, ensure_dirs

SCHEMA = """
-- One row per client website. Each site owns its own account, niche and queue.
CREATE TABLE IF NOT EXISTS sites (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    url           TEXT NOT NULL UNIQUE,   -- https://example.com
    name          TEXT DEFAULT '',
    niche         TEXT DEFAULT '',        -- auto-detected, user-editable
    audience      TEXT DEFAULT '',        -- who we are talking to
    niche_source  TEXT DEFAULT 'auto',    -- auto | edited  (so UI can flag unreviewed)
    keywords      TEXT DEFAULT '[]',      -- json list, problem-space not industry
    communities   TEXT DEFAULT '[]',      -- json list of subreddits
    value_pages   TEXT DEFAULT '[]',      -- json list of {url,title,answers}
    reddit_user   TEXT DEFAULT '',        -- the ONE account tied to this site
    quora_user    TEXT DEFAULT '',
    profile_dir   TEXT DEFAULT '',        -- browser profile, keeps accounts isolated
    active        INTEGER DEFAULT 1,
    created_at    REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS threads (
    site_id       INTEGER DEFAULT 1,
    id            TEXT PRIMARY KEY,   -- platform:native_id
    platform      TEXT NOT NULL,      -- reddit | quora
    native_id     TEXT NOT NULL,
    url           TEXT NOT NULL,
    title         TEXT NOT NULL,
    body          TEXT DEFAULT '',
    author        TEXT DEFAULT '',
    community     TEXT DEFAULT '',    -- subreddit or quora topic
    created_utc   REAL DEFAULT 0,
    num_comments  INTEGER DEFAULT 0,
    upvotes       INTEGER DEFAULT 0,
    score         REAL DEFAULT 0,
    score_detail  TEXT DEFAULT '{}',
    matched_page  TEXT DEFAULT '',    -- which value_page this thread suits, if any
    status        TEXT DEFAULT 'new', -- new | queued | drafted | approved | posted | skipped
    discovered_at REAL NOT NULL,
    UNIQUE(platform, native_id)
);

CREATE TABLE IF NOT EXISTS drafts (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    thread_id   TEXT NOT NULL,
    kind        TEXT NOT NULL DEFAULT 'comment',  -- comment | thread | answer
    body        TEXT NOT NULL,
    link_url    TEXT DEFAULT '',      -- empty means no link in this draft
    engine      TEXT DEFAULT '',
    created_at  REAL NOT NULL,
    approved    INTEGER DEFAULT 0,
    FOREIGN KEY(thread_id) REFERENCES threads(id)
);

CREATE TABLE IF NOT EXISTS posts (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    site_id     INTEGER DEFAULT 1,
    thread_id   TEXT NOT NULL,
    draft_id    INTEGER,
    platform    TEXT NOT NULL,
    kind        TEXT DEFAULT 'comment',   -- comment | thread  (drives the report split)
    community   TEXT DEFAULT '',
    permalink   TEXT DEFAULT '',
    had_link    INTEGER DEFAULT 0,
    posted_at   REAL NOT NULL,
    upvotes     INTEGER DEFAULT 0,
    replies     INTEGER DEFAULT 0,
    last_checked REAL DEFAULT 0
);

"""

# Indexes are applied AFTER migrations -- some of them reference columns that
# only exist on an upgraded database.
INDEXES = """
CREATE INDEX IF NOT EXISTS idx_threads_status ON threads(status);
CREATE INDEX IF NOT EXISTS idx_threads_score  ON threads(score DESC);
CREATE INDEX IF NOT EXISTS idx_threads_site   ON threads(site_id, status);
CREATE INDEX IF NOT EXISTS idx_posts_time     ON posts(posted_at DESC);
CREATE INDEX IF NOT EXISTS idx_posts_site     ON posts(site_id, posted_at DESC);
"""

# Columns added after the first release. Applied on every init(), safe to re-run.
MIGRATIONS = [
    ("threads", "site_id", "INTEGER DEFAULT 1"),
    ("posts", "site_id", "INTEGER DEFAULT 1"),
    ("posts", "kind", "TEXT DEFAULT 'comment'"),
    ("drafts", "site_id", "INTEGER DEFAULT 1"),
]


def _migrate(cx) -> list[str]:
    """Add any missing columns to existing tables. Returns what changed."""
    applied = []
    for table, col, decl in MIGRATIONS:
        have = {r["name"] for r in cx.execute(f"PRAGMA table_info({table})")}
        if not have:
            continue  # table does not exist yet; SCHEMA just created it correctly
        if col not in have:
            cx.execute(f"ALTER TABLE {table} ADD COLUMN {col} {decl}")
            applied.append(f"{table}.{col}")
    return applied


@contextmanager
def conn():
    ensure_dirs()
    cx = sqlite3.connect(DB_PATH)
    cx.row_factory = sqlite3.Row
    try:
        yield cx
        cx.commit()
    finally:
        cx.close()


def init() -> list[str]:
    with conn() as cx:
        cx.executescript(SCHEMA)      # tables (no-op if they exist)
        applied = _migrate(cx)        # add columns missing on older databases
        cx.executescript(INDEXES)     # now safe to index the new columns
        return applied


# --------------------------------------------------------------------------
# sites
# --------------------------------------------------------------------------

def add_site(url: str, name: str, niche: str, audience: str, keywords: list[str],
             communities: list[str], value_pages: list[dict],
             reddit_user: str = "", quora_user: str = "") -> int:
    """Create a site. profile_dir keeps each account's browser session separate,
    which is what stops one account's cookies leaking into another's."""
    slug = re.sub(r"[^a-z0-9]+", "-", url.lower().split("//")[-1]).strip("-")
    with conn() as cx:
        cur = cx.execute(
            "INSERT INTO sites (url, name, niche, audience, keywords, communities, "
            "value_pages, reddit_user, quora_user, profile_dir, created_at) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (url, name, niche, audience, json.dumps(keywords), json.dumps(communities),
             json.dumps(value_pages), reddit_user, quora_user,
             f"profiles/{slug}", time.time()),
        )
        return cur.lastrowid


def update_site(site_id: int, **fields) -> None:
    """Update any site column. Lists/dicts are json-encoded automatically.
    Editing the niche flips niche_source to 'edited' so the UI stops flagging it."""
    if not fields:
        return
    if "niche" in fields or "audience" in fields:
        fields.setdefault("niche_source", "edited")
    vals, sets = [], []
    for k, v in fields.items():
        sets.append(f"{k}=?")
        vals.append(json.dumps(v) if isinstance(v, (list, dict)) else v)
    vals.append(site_id)
    with conn() as cx:
        cx.execute(f"UPDATE sites SET {', '.join(sets)} WHERE id=?", vals)


def get_site(site_id: int) -> dict | None:
    with conn() as cx:
        row = cx.execute("SELECT * FROM sites WHERE id=?", (site_id,)).fetchone()
    return _site_dict(row) if row else None


def list_sites(active_only: bool = False) -> list[dict]:
    sql = "SELECT * FROM sites"
    if active_only:
        sql += " WHERE active=1"
    sql += " ORDER BY id"
    with conn() as cx:
        return [_site_dict(r) for r in cx.execute(sql).fetchall()]


def _site_dict(row) -> dict:
    d = dict(row)
    for k in ("keywords", "communities", "value_pages"):
        try:
            d[k] = json.loads(d.get(k) or "[]")
        except json.JSONDecodeError:
            d[k] = []
    return d


def upsert_thread(row: dict[str, Any]) -> bool:
    """Insert a discovered thread. Returns True if it was new."""
    row = dict(row)
    row.setdefault("discovered_at", time.time())
    row["id"] = f"{row['platform']}:{row['native_id']}"
    cols = ", ".join(row.keys())
    marks = ", ".join("?" for _ in row)
    with conn() as cx:
        cur = cx.execute(
            f"INSERT OR IGNORE INTO threads ({cols}) VALUES ({marks})",
            list(row.values()),
        )
        return cur.rowcount > 0


def set_score(thread_id: str, score: float, detail: dict, matched_page: str, status: str) -> None:
    with conn() as cx:
        cx.execute(
            "UPDATE threads SET score=?, score_detail=?, matched_page=?, status=? WHERE id=?",
            (score, json.dumps(detail), matched_page, status, thread_id),
        )


def threads_by_status(status: str, limit: int = 100) -> list[sqlite3.Row]:
    with conn() as cx:
        return cx.execute(
            "SELECT * FROM threads WHERE status=? ORDER BY score DESC LIMIT ?",
            (status, limit),
        ).fetchall()


def get_thread(thread_id: str) -> sqlite3.Row | None:
    with conn() as cx:
        return cx.execute("SELECT * FROM threads WHERE id=?", (thread_id,)).fetchone()


def set_status(thread_id: str, status: str) -> None:
    with conn() as cx:
        cx.execute("UPDATE threads SET status=? WHERE id=?", (status, thread_id))


def add_draft(thread_id: str, body: str, link_url: str, kind: str, engine: str) -> int:
    with conn() as cx:
        cur = cx.execute(
            "INSERT INTO drafts (thread_id, kind, body, link_url, engine, created_at) "
            "VALUES (?,?,?,?,?,?)",
            (thread_id, kind, body, link_url, engine, time.time()),
        )
        return cur.lastrowid


def latest_draft(thread_id: str) -> sqlite3.Row | None:
    with conn() as cx:
        return cx.execute(
            "SELECT * FROM drafts WHERE thread_id=? ORDER BY id DESC LIMIT 1",
            (thread_id,),
        ).fetchone()


def approve_draft(draft_id: int) -> None:
    with conn() as cx:
        cx.execute("UPDATE drafts SET approved=1 WHERE id=?", (draft_id,))


def record_post(thread_id: str, draft_id: int | None, platform: str,
                community: str, permalink: str, had_link: bool) -> None:
    with conn() as cx:
        cx.execute(
            "INSERT INTO posts (thread_id, draft_id, platform, community, permalink, "
            "had_link, posted_at) VALUES (?,?,?,?,?,?,?)",
            (thread_id, draft_id, platform, community, permalink,
             1 if had_link else 0, time.time()),
        )


def posts_since(seconds_ago: float, platform: str | None = None,
                site_id: int | None = None) -> list[sqlite3.Row]:
    """Recent posts. ALWAYS pass site_id when enforcing limits -- each site has
    its own account, so one site's activity must not consume another's cap."""
    cutoff = time.time() - seconds_ago
    sql = "SELECT * FROM posts WHERE posted_at > ?"
    args: list[Any] = [cutoff]
    if platform:
        sql += " AND platform = ?"
        args.append(platform)
    if site_id is not None:
        sql += " AND site_id = ?"
        args.append(site_id)
    sql += " ORDER BY posted_at DESC"
    with conn() as cx:
        return cx.execute(sql, args).fetchall()


def last_post_time(site_id: int | None = None) -> float:
    sql = "SELECT MAX(posted_at) AS t FROM posts"
    args: list[Any] = []
    if site_id is not None:
        sql += " WHERE site_id = ?"
        args.append(site_id)
    with conn() as cx:
        row = cx.execute(sql, args).fetchone()
        return row["t"] or 0.0


def known_ids(platform: str) -> set[str]:
    with conn() as cx:
        rows = cx.execute(
            "SELECT native_id FROM threads WHERE platform=?", (platform,)
        ).fetchall()
        return {r["native_id"] for r in rows}


def reset_scores(site_id: int) -> int:
    """Send previously-scored threads back to 'new' so they can be re-scored
    after tuning. Never touches anything already drafted or posted."""
    with conn() as cx:
        cur = cx.execute(
            "UPDATE threads SET status='new', score=0, score_detail='{}' "
            "WHERE site_id=? AND status IN ('queued','skipped')", (site_id,))
        return cur.rowcount


def counts() -> dict[str, int]:
    with conn() as cx:
        rows = cx.execute(
            "SELECT status, COUNT(*) AS n FROM threads GROUP BY status"
        ).fetchall()
        return {r["status"]: r["n"] for r in rows}
