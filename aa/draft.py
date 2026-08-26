"""Drafting layer.

Two free engines:
  engine: claude  -> writes a prompt file per thread into drafts/pending/.
                     Claude Code reads them, writes the reply into drafts/ready/,
                     then `run.py ingest` pulls them into the DB. Costs nothing
                     beyond the Claude Code session you already have open.
  engine: groq    -> calls Groq's free API tier over plain HTTP (no SDK).

The link decision is made HERE in Python, not by the model. A model asked to
"link when relevant" will link every single time. The ratio rule is the thing
that keeps the account from reading as promotional, so it is enforced in code.
"""
from __future__ import annotations

import json
import re
import time

import requests

from . import db
from .config import DRAFT_DIR, env

GROQ_URL = "https://api.groq.com/openai/v1/chat/completions"


# ---------------------------------------------------------------------------
# Link policy
# ---------------------------------------------------------------------------

def should_link(cfg, thread_row) -> tuple[bool, str]:
    """Decide whether THIS reply may carry a link.

    Rules, all must pass:
      1. The thread must actually match a value page strongly (>= 0.55).
      2. The account must be past its warm-up (earned the right to link).
      3. We must be under the rolling link ratio for the last 30 days.
    """
    page = thread_row["matched_page"] or ""
    if not page:
        return False, ""

    try:
        detail = json.loads(thread_row["score_detail"] or "{}")
    except json.JSONDecodeError:
        detail = {}
    if float(detail.get("relevance", 0)) < 0.55:
        return False, ""

    # Warm-up. An account whose first comments all carry links reads as
    # promotional no matter how good the writing is. Earn the right to link by
    # being useful for free first. This is the rule people skip, and it is the
    # one that actually gets accounts shadowbanned.
    warmup = int(cfg.get_path("limits.warmup_posts", 10))
    all_time = db.posts_since(365 * 86400)
    if len(all_time) < warmup:
        return False, ""

    max_ratio = float(cfg.get_path("limits.max_link_ratio", 0.25))
    recent = db.posts_since(30 * 86400)
    if recent:
        linked = sum(1 for p in recent if p["had_link"])
        # Ratio AFTER we add this one must still be within budget
        if (linked + 1) / (len(recent) + 1) > max_ratio:
            return False, ""

    return True, page


# ---------------------------------------------------------------------------
# Prompt construction
# ---------------------------------------------------------------------------

def build_prompt(cfg, thread_row, link_url: str) -> str:
    site = cfg.get_path("site", {}) or {}
    tone = cfg.get_path("draft.tone", "Plain and direct.")
    max_words = int(cfg.get_path("draft.max_words", 180))
    platform = thread_row["platform"]

    if link_url:
        page = next(
            (p for p in (cfg.get_path("value_pages", []) or [])
             if p.get("url") == link_url),
            {},
        )
        page_note = (
            "You MAY include exactly one link, but only if it genuinely helps:\n"
            f"  URL: {link_url}\n"
            f"  That page answers: {page.get('answers', '')}\n"
            'Introduce it the way a person would ("I wrote up the full checklist here '
            "if it's useful\") and only AFTER you have already answered the question in "
            "the comment itself. If the link would feel bolted on, leave it out "
            "entirely and say so."
        )
    else:
        page_note = (
            "Do NOT include any link, URL, or brand name. This reply is pure help. "
            "This is deliberate -- most replies must carry nothing."
        )

    where = (f"r/{thread_row['community']}" if platform == "reddit"
             else "a Quora question")

    return "\n".join([
        f"You are replying on {platform} as a real practitioner, not a marketer.",
        "",
        "WHO YOU ARE",
        site.get("what_we_do", ""),
        "",
        "THE THREAD",
        f"Community: {where}",
        f"Title: {thread_row['title']}",
        f"Body: {(thread_row['body'] or '(no body text -- answer the title)')[:2500]}",
        "",
        "YOUR TASK",
        "Write a reply that would be genuinely useful even if you had no website at all.",
        "Answer the actual question that was asked. Be specific: name the exact setting,",
        "step, or number where you can. If the asker has a wrong assumption, correct it",
        "kindly. If you would not have anything useful to say, reply with exactly SKIP.",
        "",
        "TONE",
        tone,
        "",
        "LENGTH",
        f"Under {max_words} words.",
        "",
        "LINK POLICY",
        page_note,
        "",
        "HARD RULES",
        '- Never open with "Great question" or any variation.',
        "- No em-dashes. No bullet-point walls. No headings.",
        "- Do not mention being an AI, an agent, or a tool.",
        '- Do not say "as someone who works in this space".',
        "- Write the reply text ONLY. No preamble, no quotes around it, no sign-off.",
    ])


# ---------------------------------------------------------------------------
# Engines
# ---------------------------------------------------------------------------

def draft_via_groq(cfg, prompt: str) -> str:
    key = env("GROQ_API_KEY", required=True)
    model = cfg.get_path("draft.groq_model", "llama-3.3-70b-versatile")
    resp = requests.post(
        GROQ_URL,
        headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
        json={
            "model": model,
            "messages": [{"role": "user", "content": prompt}],
            "temperature": 0.85,
            "max_tokens": 700,
        },
        timeout=90,
    )
    if resp.status_code != 200:
        raise RuntimeError(f"groq {resp.status_code}: {resp.text[:300]}")
    return resp.json()["choices"][0]["message"]["content"].strip()


def write_prompt_file(thread_row, prompt: str, link_url: str) -> str:
    """Claude-engine path: drop a prompt file for Claude Code to answer."""
    safe = re.sub(r"[^a-zA-Z0-9_-]", "_", thread_row["id"])[:80]
    path = DRAFT_DIR / "pending" / f"{safe}.md"
    path.write_text(
        f"<!-- thread_id: {thread_row['id']} -->\n"
        f"<!-- link_url: {link_url} -->\n"
        f"<!-- url: {thread_row['url']} -->\n\n"
        f"{prompt}\n\n"
        f"---\nWRITE YOUR REPLY BELOW THIS LINE, then save into drafts/ready/ "
        f"with the same filename.\n---\n\n",
        encoding="utf-8",
    )
    return str(path)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def clean_reply(text: str) -> str:
    text = (text or "").strip()
    # Models love wrapping output in quotes or code fences
    text = re.sub(r"^```[a-z]*\n|\n```$", "", text).strip()
    if len(text) > 1 and text[0] == '"' and text[-1] == '"':
        text = text[1:-1].strip()
    text = text.replace("—", ", ")  # strip em-dashes, they read as AI
    return text


def run(cfg, limit: int = 10) -> dict[str, int]:
    engine = cfg.get_path("draft.engine", "claude")
    rows = db.threads_by_status("queued", limit=limit)

    made = skipped = 0
    for row in rows:
        link_ok, link_url = should_link(cfg, row)
        prompt = build_prompt(cfg, row, link_url if link_ok else "")

        if engine == "groq":
            try:
                reply = clean_reply(draft_via_groq(cfg, prompt))
            except Exception as e:
                print(f"  ! draft failed for {row['id']}: {e}")
                continue

            if reply.strip().upper().startswith("SKIP") or len(reply) < 40:
                db.set_status(row["id"], "skipped")
                skipped += 1
                print(f"  - skipped {row['id']} (model had nothing useful)")
                continue

            has_link = bool(link_url) and link_url in reply
            db.add_draft(row["id"], reply, link_url if has_link else "", "comment", "groq")
            db.set_status(row["id"], "drafted")
            made += 1
            print(f"  + drafted {row['id']} ({'link' if has_link else 'no link'})")
            time.sleep(2)

        else:  # claude engine
            path = write_prompt_file(row, prompt, link_url if link_ok else "")
            db.set_status(row["id"], "drafted")
            made += 1
            print(f"  + prompt written {path}")

    return {"drafted": made, "skipped": skipped}


# ---------------------------------------------------------------------------
# Site-aware path (multi-site). Supersedes run() above, which reads config.yaml
# and therefore only ever knew about one site.
# ---------------------------------------------------------------------------

def build_prompt_for_site(site: dict, row, link_url: str) -> str:
    max_words = 180
    platform = row["platform"]

    if link_url:
        page = next((p for p in site.get("value_pages", [])
                     if p.get("url") == link_url), {})
        page_note = (
            "You MAY include exactly one link, but only if it genuinely helps:\n"
            f"  URL: {link_url}\n"
            f"  What it is: {page.get('title', '')}\n"
            'Introduce it the way a person would ("I put the full checklist here '
            'if it helps") and only AFTER you have already answered the question '
            "in the comment itself. If the link would feel bolted on, leave it "
            "out entirely."
        )
    else:
        page_note = (
            "Do NOT include any link, URL, or brand name. This reply is pure "
            "help. This is deliberate -- most replies must carry nothing."
        )

    where = f"r/{row['community']}" if platform == "reddit" else "a Quora question"

    return "\n".join([
        f"You are replying on {platform} as a real practitioner, not a marketer.",
        "",
        "WHO YOU ARE",
        site.get("niche", ""),
        f"You work with: {site.get('audience', '')}",
        "",
        "THE THREAD",
        f"Community: {where}",
        f"Title: {row['title']}",
        f"Body: {(row['body'] or '(no body text -- answer the title)')[:2500]}",
        "",
        "YOUR TASK",
        "Write a reply that would be genuinely useful even if you had no website at all.",
        "Answer the actual question that was asked. Be specific: name the exact setting,",
        "step, or number where you can. If the asker has a wrong assumption, correct it",
        "kindly. If you would not have anything useful to say, reply with exactly SKIP.",
        "",
        "TONE",
        "Plain, direct, first person. Someone who has actually done this work.",
        "",
        "LENGTH",
        f"Under {max_words} words.",
        "",
        "LINK POLICY",
        page_note,
        "",
        "HARD RULES",
        '- Never open with "Great question" or any variation.',
        "- No em-dashes. No bullet-point walls. No headings.",
        "- Do not mention being an AI, an agent, or a tool.",
        '- Do not say "as someone who works in this space".',
        "- Write the reply text ONLY. No preamble, no quotes around it, no sign-off.",
    ])


def run_for_site(site: dict, limit: int = 10, engine: str = "claude") -> dict:
    """Draft comments for a site's queued threads.

    The link decision is made in code, never by the model. Two independent
    gates must BOTH pass: the account must be allowed to link at all
    (warm-up + ratio, from limits), and a page must actually fit this specific
    thread (tier vs thread affinity, from links).
    """
    from . import limits, links

    rows = [r for r in db.threads_by_status("queued", limit=200)
            if r["site_id"] == site["id"]][:limit]

    account_may_link, why_not = limits.may_link(site)
    made = skipped = linked = 0

    for row in rows:
        link_url = ""
        if account_may_link:
            text = f"{row['title']}\n{row['body'] or ''}"
            link_url, _ = links.choose_link(text, site)

        prompt = build_prompt_for_site(site, row, link_url)

        if engine == "groq":
            try:
                reply = clean_reply(draft_via_groq_simple(prompt))
            except Exception as e:
                print(f"  ! draft failed for {row['id']}: {e}")
                continue
            if reply.strip().upper().startswith("SKIP") or len(reply) < 40:
                db.set_status(row["id"], "skipped")
                skipped += 1
                continue
            has_link = bool(link_url) and link_url in reply
            db.add_draft(row["id"], reply, link_url if has_link else "",
                         "comment", "groq")
            db.set_status(row["id"], "drafted")
            made += 1
            linked += bool(has_link)
            time.sleep(2)
        else:
            write_prompt_file(row, prompt, link_url)
            db.set_status(row["id"], "drafted")
            made += 1
            linked += bool(link_url)

    return {"drafted": made, "skipped": skipped, "with_link": linked,
            "link_blocked": "" if account_may_link else why_not}


def draft_via_groq_simple(prompt: str, model: str = "llama-3.3-70b-versatile") -> str:
    key = env("GROQ_API_KEY", required=True)
    resp = requests.post(
        GROQ_URL,
        headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
        json={"model": model, "messages": [{"role": "user", "content": prompt}],
              "temperature": 0.85, "max_tokens": 700},
        timeout=90)
    if resp.status_code != 200:
        raise RuntimeError(f"groq {resp.status_code}: {resp.text[:300]}")
    return resp.json()["choices"][0]["message"]["content"].strip()


def ingest_ready() -> dict[str, int]:
    """Pull Claude-written replies out of drafts/ready/ into the DB."""
    ready = DRAFT_DIR / "ready"
    n = 0
    for path in ready.glob("*.md"):
        raw = path.read_text(encoding="utf-8")

        tid = re.search(r"<!--\s*thread_id:\s*(.+?)\s*-->", raw)
        link = re.search(r"<!--\s*link_url:\s*(.*?)\s*-->", raw)
        if not tid:
            print(f"  ! {path.name} has no thread_id header, skipping")
            continue

        parts = raw.split("---")
        body = clean_reply(parts[-1] if len(parts) > 1 else raw)
        if len(body) < 40:
            print(f"  ! {path.name} body too short, skipping")
            continue

        link_url = link.group(1) if link else ""
        has_link = bool(link_url) and link_url in body
        db.add_draft(tid.group(1), body, link_url if has_link else "", "comment", "claude")
        db.set_status(tid.group(1), "approved")
        path.unlink()
        n += 1
        print(f"  + ingested {tid.group(1)}")

    return {"ingested": n}
