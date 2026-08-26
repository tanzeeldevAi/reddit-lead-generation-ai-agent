"""Thread creation -- the agent posting its own threads, not just replying.

Two reasons this exists separately from commenting:

1. It is the channel where a vertical-specific link legitimately belongs. In a
   comment we are a guest on someone else's question, so a limo-branded page is
   off-topic unless they asked about limos. In our own thread we set the
   framing, so the link is on-topic by construction.

2. Threads carry far more weight than comments for authority, and far more risk.
   One badly judged self-post gets an account banned from a subreddit
   permanently, where a weak comment just gets downvoted. So the rules here are
   deliberately stricter: longer cooldowns, mandatory rule-checking, and no
   posting into a subreddit we have never commented in.

Topics are derived from what people ARE asking, not invented. A thread that
answers a question three different people asked last month lands; a thread about
whatever we felt like writing does not.
"""
from __future__ import annotations

import json
import re
import time
from collections import Counter

from . import browser, db, limits, links
from .config import DRAFT_DIR

STOP = {
    "the", "a", "an", "and", "or", "but", "if", "to", "of", "in", "on", "for",
    "with", "is", "are", "was", "be", "my", "your", "our", "i", "you", "we",
    "it", "this", "that", "how", "do", "does", "can", "should", "what", "why",
    "get", "got", "any", "have", "has", "not", "no", "so", "at", "from", "by",
    "as", "me", "am", "will", "would", "there", "their", "about", "when",
    "who", "which", "them", "they", "been", "were", "just", "like", "really",
    "anyone", "someone", "need", "want", "help", "please", "thanks", "advice",
}

# Subreddits that ban or heavily restrict self-promotion. We can comment here,
# but posting our own thread needs a human to confirm it fits the rules.
NEEDS_REVIEW = {"seo", "bigseo", "marketing", "digital_marketing", "entrepreneur",
                "smallbusiness", "startups", "webdev", "web_design"}


def words(text: str) -> list[str]:
    return [w for w in re.findall(r"[a-z][a-z'-]{2,}", (text or "").lower())
            if w not in STOP and len(w) > 3]


# ---------------------------------------------------------------------------
# topic mining
# ---------------------------------------------------------------------------

def mine_topics(site_id: int, min_mentions: int = 2, limit: int = 12,
                max_doc_freq: float = 0.30) -> list[dict]:
    """Find questions the community keeps asking.

    Signal: the same theme across several separate threads. A theme appearing
    once is noise.

    Two filters that matter:

    * Themes appearing in more than `max_doc_freq` of all threads are dropped.
      We FOUND these threads by searching our own keywords, so "local business"
      and "google business" appear almost everywhere by construction. They are
      the query coming back at us, not a topic.

    * avg_comments is computed only over enriched threads. An un-enriched
      thread has num_comments=0, which would otherwise fake a wide-open gap
      on every theme.
    """
    with db.conn() as cx:
        rows = cx.execute(
            "SELECT title, body, community, num_comments, upvotes, url "
            "FROM threads WHERE site_id=? AND status IN ('queued','skipped','drafted')",
            (site_id,)).fetchall()

    if not rows:
        return []

    total = len(rows)
    themes: Counter = Counter()
    examples: dict[str, list] = {}

    for r in rows:
        ws = words(r["title"])
        grams = [f"{a} {b}" for a, b in zip(ws, ws[1:])]
        for g in set(grams):
            themes[g] += 1
            examples.setdefault(g, []).append(r)

    out = []
    for theme, n in themes.most_common(200):
        if n < min_mentions:
            continue
        if n / total > max_doc_freq:
            continue                     # too generic -- it is our own query
        ex = examples[theme]

        enriched = [e for e in ex if (e["body"] or "").strip()]
        if enriched:
            avg_comments = sum(int(e["num_comments"] or 0)
                               for e in enriched) / len(enriched)
            measured = True
        else:
            avg_comments, measured = 0.0, False

        subs = Counter(e["community"] for e in ex)
        out.append({
            "theme": theme,
            "mentions": n,
            "avg_comments": round(avg_comments, 1),
            "comments_measured": measured,
            "enriched": len(enriched),
            # Unmeasured themes are ranked on demand alone, not a fake gap.
            "gap_score": round(n / (1 + avg_comments), 2) if measured else round(n * 0.5, 2),
            "top_sub": subs.most_common(1)[0][0],
            "subs": dict(subs),
            "examples": [{"title": e["title"], "url": e["url"],
                          "comments": e["num_comments"]} for e in ex[:4]],
        })

    out.sort(key=lambda t: (t["gap_score"], t["mentions"]), reverse=True)
    return out[:limit]


# ---------------------------------------------------------------------------
# subreddit rules
# ---------------------------------------------------------------------------

def fetch_rules(page, sub: str) -> dict:
    """Read a subreddit's posted rules. We cannot reliably parse intent from
    them, so this surfaces them for the human rather than auto-deciding."""
    if not browser.goto(page, f"https://www.reddit.com/r/{sub}/about/rules/", settle=2.5):
        return {"sub": sub, "rules": [], "error": "could not load"}
    try:
        rules = page.evaluate("""() => {
            const out = [];
            document.querySelectorAll('div[data-testid="rule"], .rule, li').forEach(n => {
                const t = (n.innerText||'').trim();
                if (t.length > 15 && t.length < 600) out.push(t.split('\\n')[0]);
            });
            return [...new Set(out)].slice(0, 25);
        }""")
    except Exception:
        rules = []

    flags = []
    joined = " ".join(rules).lower()
    for pat, label in [
        (r"no self.?promot|no promotion|no advertis", "bans self-promotion"),
        (r"no link|links? are not allowed|no blog", "restricts links"),
        (r"9\s*[:1]\s*1|90/10|1.?in.?10", "has a self-promo ratio rule"),
        (r"minimum.{0,20}karma|account age", "has karma/age requirements"),
        (r"must be approved|contact.{0,15}mod|mod approval", "needs mod approval"),
    ]:
        if re.search(pat, joined):
            flags.append(label)

    return {"sub": sub, "rules": rules[:12], "flags": flags,
            "needs_review": sub.lower() in NEEDS_REVIEW or bool(flags)}


# ---------------------------------------------------------------------------
# drafting a thread
# ---------------------------------------------------------------------------

def build_thread_prompt(site: dict, topic: dict, link_url: str,
                        rules: dict | None = None) -> str:
    ex = "\n".join(f"  - {e['title']} ({e['comments']} comments)"
                   for e in topic["examples"])
    rule_note = ""
    if rules and rules.get("flags"):
        rule_note = ("\nSUBREDDIT RESTRICTIONS (respect these exactly)\n"
                     + "\n".join(f"  - {f}" for f in rules["flags"]))

    if link_url:
        link_note = (
            f"You MAY link exactly one resource: {link_url}\n"
            "Put it at the END, after the post already stands on its own. The post\n"
            "must be worth reading by someone who never clicks it. If the link\n"
            "cannot be introduced naturally, leave it out."
        )
    else:
        link_note = ("Do NOT include any link, URL, or brand name. This post is "
                     "pure contribution.")

    return "\n".join([
        f"Write an original post for r/{topic['top_sub']}.",
        "",
        "WHO YOU ARE",
        f"{site.get('niche','')}",
        f"You work with: {site.get('audience','')}",
        "",
        "WHY THIS TOPIC",
        f"{topic['mentions']} separate people asked about \"{topic['theme']}\" "
        f"recently, and the existing threads averaged only "
        f"{topic['avg_comments']} comments. Nobody has answered it properly.",
        "",
        "THE QUESTIONS PEOPLE ACTUALLY ASKED",
        ex,
        rule_note,
        "",
        "YOUR TASK",
        "Write the post that would have saved all of those people their time.",
        "Lead with the specific thing most people get wrong. Give the actual",
        "steps, settings, or numbers. Include one concrete example with real",
        "figures. If there is a common piece of advice that is wrong, say so and",
        "explain why.",
        "",
        "FORMAT",
        "First line: the post title, prefixed with 'TITLE: '.",
        "Then a blank line, then the post body.",
        "Body between 200 and 400 words. Short paragraphs. At most one short list.",
        "",
        "TONE",
        "Plain, direct, first person. Someone who has done this work and is",
        "tired of seeing it done badly. No marketing language. No hype.",
        "",
        "LINK POLICY",
        link_note,
        "",
        "HARD RULES",
        "- No em-dashes.",
        "- Do not open with a question.",
        '- Do not write "in today\'s digital landscape" or similar filler.',
        "- Do not mention being an AI or a tool.",
        "- Do not ask people to DM you.",
    ])


def propose(site: dict, limit: int = 5, check_rules: bool = True,
            verbose: bool = True) -> list[dict]:
    """Mine topics, pick the best, and write thread prompts for review.

    Nothing is posted here. Output lands in drafts/threads/ for a human.
    """
    topics = mine_topics(site["id"], limit=limit * 3)
    if not topics:
        return []

    may_link_now, link_reason = limits.may_link(site)
    out = []

    rules_cache: dict[str, dict] = {}
    page_ctx = None
    if check_rules:
        page_ctx = browser.fresh(headless=True)
        page = page_ctx.__enter__()

    try:
        for t in topics[:limit]:
            sub = t["top_sub"]
            rules = {}
            if check_rules:
                if sub not in rules_cache:
                    rules_cache[sub] = fetch_rules(page, sub)
                    browser.human_pause(2.0, 3.5)
                rules = rules_cache[sub]

            # Thread link: our own framing, so a vertical page is fair game --
            # but only if the account has earned the right to link at all.
            link_url = ""
            if may_link_now:
                pages = site.get("value_pages", [])
                # In our own thread we may use the most specific page we have.
                ranked = sorted(pages, key=lambda p: links.TIER_RANK.get(
                    p.get("tier", "locked"), 2), reverse=True)
                link_url = ranked[0]["url"] if ranked else ""

            prompt = build_thread_prompt(site, t, link_url, rules)
            slug = re.sub(r"[^a-z0-9]+", "-", t["theme"])[:40]
            path = DRAFT_DIR / "threads" / f"site{site['id']}-{slug}.md"
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(
                f"<!-- site_id: {site['id']} -->\n"
                f"<!-- subreddit: {sub} -->\n"
                f"<!-- theme: {t['theme']} -->\n"
                f"<!-- link_url: {link_url} -->\n"
                f"<!-- needs_review: {rules.get('needs_review', True)} -->\n\n"
                f"{prompt}\n\n---\nWRITE THE POST BELOW THIS LINE\n---\n\n",
                encoding="utf-8")

            item = {
                "theme": t["theme"], "sub": sub, "mentions": t["mentions"],
                "gap_score": t["gap_score"], "avg_comments": t["avg_comments"],
                "link": link_url, "link_blocked": "" if may_link_now else link_reason,
                "rules_flags": rules.get("flags", []),
                "needs_review": rules.get("needs_review", True),
                "prompt_file": str(path),
                "examples": t["examples"],
            }
            out.append(item)
            if verbose:
                warn = (" [MOD RULES: " + ", ".join(item["rules_flags"]) + "]"
                        if item["rules_flags"] else "")
                print(f"  r/{sub:<16} \"{t['theme']}\"  "
                      f"{t['mentions']} asks, gap {t['gap_score']}{warn}")
    finally:
        if page_ctx:
            page_ctx.__exit__(None, None, None)

    return out


def ingest_threads(site_id: int) -> dict:
    """Pull written threads out of drafts/threads/ready/ into the DB as drafts."""
    ready = DRAFT_DIR / "threads" / "ready"
    ready.mkdir(parents=True, exist_ok=True)
    n = 0
    for path in ready.glob("*.md"):
        raw = path.read_text(encoding="utf-8")
        sub = re.search(r"<!--\s*subreddit:\s*(.+?)\s*-->", raw)
        link = re.search(r"<!--\s*link_url:\s*(.*?)\s*-->", raw)
        parts = raw.split("---")
        body = (parts[-1] if len(parts) > 1 else raw).strip()
        if len(body) < 120:
            print(f"  ! {path.name} too short, skipping")
            continue

        m = re.match(r"TITLE:\s*(.+)", body)
        if not m:
            print(f"  ! {path.name} has no 'TITLE:' line, skipping")
            continue
        title = m.group(1).strip()
        post_body = body[m.end():].strip()

        native = f"own-{int(time.time())}-{n}"
        tid = f"reddit:{native}"
        db.upsert_thread({
            "site_id": site_id, "platform": "reddit", "native_id": native,
            "url": "", "title": title, "body": post_body,
            "community": sub.group(1) if sub else "", "status": "approved",
            "discovered_at": time.time(),
        })
        link_url = link.group(1) if link else ""
        db.add_draft(tid, post_body, link_url if link_url in post_body else "",
                     "thread", "claude")
        path.unlink()
        n += 1
        print(f"  + thread ready: {title[:60]}")
    return {"ingested": n}
