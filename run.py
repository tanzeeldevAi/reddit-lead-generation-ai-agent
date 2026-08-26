#!/usr/bin/env python3
"""authority-agent CLI (multi-site).

Everything is scoped to a site. List them with `sites`, then pass --site <id>.

    python run.py sites                       list sites
    python run.py add-site yourdomain.com   detect niche and add
    python run.py discover --site 1           find threads
    python run.py score --site 1              rank into the queue
    python run.py queue --site 1              see what is queued
    python run.py draft --site 1              write replies
    python run.py ingest --site 1             pull Claude-written replies in
    python run.py topics --site 1             mine thread topics
    python run.py propose --site 1            write thread prompts
    python run.py post --site 1               send ONE, with confirmation
    python run.py report --site 1             activity report
    python run.py status                      everything at a glance

Or the whole daily cycle:   python daily.py --site 1
Dashboard:                  python serve.py 8788
"""
from __future__ import annotations

import argparse
import json
import sys

from aa import (browser, config, db, doctor, draft, limits, links, niche,
                post, report, score, threads)


def need_site(args) -> dict:
    """Resolve --site. Accepts an id, a URL, a domain, or part of the name --
    people naturally paste the URL when asked to identify a site."""
    sites = db.list_sites()
    if not sites:
        sys.exit("No sites yet. Add one:  python run.py add-site <url>")

    raw = (getattr(args, "site", None) or "")
    raw = str(raw).strip().strip("/")
    if not raw:
        if len(sites) == 1:
            return sites[0]
        sys.exit("Several sites configured -- pass --site <id>. "
                 "Run `python run.py sites` to see them.")

    if raw.isdigit():
        s = db.get_site(int(raw))
        if s:
            return s
        sys.exit(f"No site #{raw}. Run `python run.py sites`.")

    # Not a number: match on URL/domain/name, case-insensitively.
    needle = raw.lower()
    for strip in ("https://", "http://", "www."):
        needle = needle.replace(strip, "")
    needle = needle.strip("/")

    for s in sites:
        host = s["url"].lower()
        for strip in ("https://", "http://", "www."):
            host = host.replace(strip, "")
        host = host.strip("/")
        if needle in (host, s["url"].lower()) or needle in (s["name"] or "").lower():
            return s

    print(f"No site matches '{raw}'. Sites configured:")
    for s in sites:
        print(f"  {s['id']}  {s['url']}   {s['name']}")
    sys.exit(1)


# ---------------------------------------------------------------------------

def cmd_init(args):
    applied = db.init()
    config.ensure_dirs()
    print(f"database  {config.DB_PATH}")
    print(f"drafts    {config.DRAFT_DIR}")
    print(f"migrations applied: {applied or 'none needed'}")


def cmd_sites(args):
    db.init()
    sites = db.list_sites()
    if not sites:
        print("No sites. Add one:  python run.py add-site <url>")
        return
    for s in sites:
        t = links.summarize(s["value_pages"])
        q = len([r for r in db.threads_by_status("queued", 500)
                 if r["site_id"] == s["id"]])
        flag = "  [niche unreviewed]" if s["niche_source"] == "auto" else ""
        print(f"\n#{s['id']}  {s['name'] or s['url']}{flag}")
        print(f"    {s['url']}")
        print(f"    {s['niche'][:74]}")
        print(f"    {len(s['keywords'])} keywords | {len(s['communities'])} communities "
              f"| {q} queued")
        print(f"    pages: {t['universal']} universal, {t['semi']} semi, "
              f"{t['locked']} locked")
        print(f"    account: {s['reddit_user'] or '(not set)'}")


def cmd_add_site(args):
    db.init()
    print(f"detecting niche for {args.url} ...")
    p = niche.detect(args.url)
    if p.get("error"):
        sys.exit(p["error"])
    print(niche.render(p))

    if not args.yes:
        if input("\nAdd this site? [y/N] ").strip().lower() not in ("y", "yes"):
            print("cancelled.")
            return

    sid = db.add_site(url=p["url"], name=p["name"], niche=p["niche"],
                      audience=p["audience"], keywords=p["keywords"],
                      communities=p["communities"], value_pages=p["value_pages"],
                      reddit_user=args.account or "")
    print(f"\nsite #{sid} created. Tiering pages ...")
    site = db.get_site(sid)
    pages = links.classify_site(site, verbose=True)
    db.update_site(sid, value_pages=pages)
    s = links.summarize(pages)
    print(f"\ntiers: {s}")
    if not s["universal"]:
        print("\nNOTE: no universal pages. Broad threads will get no-link "
              "comments until you publish something unbranded.")


def cmd_site_edit(args):
    db.init()
    site = need_site(args)
    fields = {}
    if args.niche:
        fields["niche"] = args.niche
    if args.audience:
        fields["audience"] = args.audience
    if args.account:
        fields["reddit_user"] = args.account
    if args.keywords:
        fields["keywords"] = [k.strip() for k in args.keywords.split(",") if k.strip()]
    if args.communities:
        fields["communities"] = [c.strip() for c in args.communities.split(",") if c.strip()]
    if not fields:
        sys.exit("Nothing to change. Try --niche / --audience / --account / "
                 "--keywords / --communities")
    db.update_site(site["id"], **fields)
    print(f"site #{site['id']} updated: {', '.join(fields)}")


def cmd_discover(args):
    db.init()
    site = need_site(args)
    from aa import reddit
    r = reddit.discover(site, window=args.window, headless=not args.show)
    print(f"\n{r}")
    n = reddit.enrich(site["id"], limit=args.enrich, headless=not args.show,
                      verbose=False)
    print(f"enriched {n}")


def cmd_score(args):
    db.init()
    site = need_site(args)
    if args.rescore:
        print(f"reset {db.reset_scores(site['id'])} threads")
    r = score.run(site, min_score=args.min_score, verbose=args.verbose)
    print(f"\n{r}")


def cmd_queue(args):
    db.init()
    site = need_site(args)
    rows = [r for r in db.threads_by_status("queued", 200)
            if r["site_id"] == site["id"]]
    if not rows:
        print("Queue is empty. Run discover, or lower --min-score on score.")
        return
    print(f"{len(rows)} queued\n")
    for r in rows:
        d = json.loads(r["score_detail"] or "{}")
        link = d.get("link") or "(no link)"
        print(f"{r['score']:>6.1f}  r/{r['community']:<16} {r['title'][:56]}")
        print(f"        {r['url']}")
        print(f"        matched \"{d.get('best_keyword','')}\"  ->  {link}")


def cmd_draft(args):
    db.init()
    site = need_site(args)
    r = draft.run_for_site(site, limit=args.limit, engine=args.engine)
    print(f"\n{r}")
    if args.engine == "claude" and r["drafted"]:
        print(f"\nPrompt files: {config.DRAFT_DIR / 'pending'}")
        print("Ask Claude Code to answer them into drafts/ready/, then:")
        print(f"  python run.py ingest --site {site['id']}")


def cmd_ingest(args):
    db.init()
    print(draft.ingest_ready())


def cmd_topics(args):
    db.init()
    site = need_site(args)
    tops = threads.mine_topics(site["id"], min_mentions=args.min_mentions,
                               limit=args.limit)
    if not tops:
        print("No repeated themes yet. Discover more threads first.")
        return
    for t in tops:
        note = "" if t["comments_measured"] else "  (comment counts unmeasured)"
        print(f"\ngap {t['gap_score']:<6} x{t['mentions']} asks | "
              f"avg {t['avg_comments']} comments{note}")
        print(f"  theme: \"{t['theme']}\"  ->  r/{t['top_sub']}")
        for e in t["examples"][:3]:
            print(f"    - [{e['comments']:>2}c] {e['title'][:62]}")


def cmd_propose(args):
    db.init()
    site = need_site(args)
    ok, why = limits.check(site, "reddit", "", "thread")
    if not ok and not args.force:
        sys.exit(f"Blocked: {why}\nUse --force to write the prompts anyway.")
    props = threads.propose(site, limit=args.limit,
                            check_rules=not args.skip_rules)
    if not props:
        print("No topics to propose yet.")
        return
    print(f"\n{len(props)} proposed:")
    for p in props:
        print(f"\n  r/{p['sub']}  \"{p['theme']}\"  ({p['mentions']} asks)")
        if p["rules_flags"]:
            print(f"    MOD RULES: {', '.join(p['rules_flags'])}")
        if p["link_blocked"]:
            print(f"    no link: {p['link_blocked']}")
        elif p["link"]:
            print(f"    link: {p['link']}")
        print(f"    prompt: {p['prompt_file']}")
    print("\nAsk Claude Code to write these, save into drafts/threads/ready/, then:")
    print(f"  python run.py ingest-threads --site {site['id']}")


def cmd_ingest_threads(args):
    db.init()
    site = need_site(args)
    print(threads.ingest_threads(site["id"]))


def cmd_connect(args):
    db.init()
    site = need_site(args)
    post.connect(site)


def cmd_whoami(args):
    db.init()
    for s in ([need_site(args)] if args.site else db.list_sites()):
        want = s["reddit_user"] or "(not set)"
        try:
            with post.session(s, mode="profile", headless=True) as page:
                who = post.whoami(page)
        except Exception as e:
            who, want = "", f"{want}  (browser error: {e})"
        state = f"u/{who}" if who else "LOGGED OUT"
        mark = ""
        if who and s["reddit_user"] and who.lower() != s["reddit_user"].lstrip("u/").lower():
            mark = "   <-- MISMATCH"
        print(f"#{s['id']:<3} {(s['name'] or s['url'])[:34]:<36} "
              f"configured: {want:<18} profile: {state}{mark}")
        if not who:
            print(f"      python run.py connect --site {s['id']}")


def cmd_post(args):
    db.init()
    site = need_site(args)
    post.run_one(site, thread_id=args.thread, auto_yes=args.yes,
                 mode=args.mode)


def cmd_report(args):
    db.init()
    if args.site:
        s = db.get_site(args.site)
        print(report.render_text(s["id"], s["name"] or s["url"]))
    else:
        print(report.render_text(None, "all sites"))


def cmd_status(args):
    db.init()
    c = db.counts()
    print("Threads by status:")
    for k in ("new", "queued", "drafted", "approved", "posted", "skipped"):
        print(f"  {k:<10} {c.get(k, 0)}")

    for s in db.list_sites():
        ok, have, need = limits.warmed_up(s)
        linked, total, ratio = limits.link_ratio(s["id"], 30)
        print(f"\n#{s['id']} {s['name'] or s['url']}")
        print(f"   posts all-time: {have}"
              + ("" if ok else f"   WARM-UP {have}/{need} -- links blocked"))
        print(f"   30d link ratio: {linked}/{total} = {ratio:.0%}")
        allowed, why = limits.check(s, "reddit", "", "comment")
        print(f"   can comment now: {allowed}" + (f"  ({why})" if why else ""))


def cmd_check_env(args):
    doctor.run()


def cmd_check_subs(args):
    db.init()
    site = need_site(args)
    from aa import reddit
    print(f"{'subreddit':<24}{'subscribers':>13}   status")
    print("-" * 54)
    bad = []
    with browser.fresh(headless=True) as page:
        for sub in site["communities"]:
            m = reddit.subreddit_meta(page, sub)
            n = f"{m['subscribers']:,}" if m["subscribers"] else "-"
            status = "ok" if m["exists"] else "MISSING / PRIVATE"
            if not m["exists"]:
                bad.append(sub)
            print(f"{sub:<24}{n:>13}   {status}")
            browser.human_pause(1.5, 3.0)
    if bad:
        print(f"\n{len(bad)} problem(s): {', '.join(bad)}")
        print("Remove them:  python run.py site-edit --site "
              f"{site['id']} --communities \"...\"")


# ---------------------------------------------------------------------------

def main():
    ap = argparse.ArgumentParser(description="authority-agent")
    ap.add_argument("--site", default=None, help="site id, url, or name")

    # Shared so `--site` works on either side of the subcommand. Without this,
    # `run.py post --site 1` is an error while `run.py --site 1 post` works,
    # which is a needless trap.
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--site", default=None, help="site id, url, or name")

    sub = ap.add_subparsers(dest="cmd", required=True)

    def P(name, **kw):
        return sub.add_parser(name, parents=[common], **kw)

    P("init")
    P("sites")
    P("status")
    P("check-env")
    P("check-subs")
    P("ingest")

    a = P("add-site")
    a.add_argument("url")
    a.add_argument("--account", default="", help="reddit username for this site")
    a.add_argument("--yes", action="store_true")

    e = P("site-edit")
    for f in ("niche", "audience", "account", "keywords", "communities"):
        e.add_argument(f"--{f}", default=None)

    d = P("discover")
    d.add_argument("--window", default="year",
                   choices=["day", "week", "month", "year", "all"])
    d.add_argument("--enrich", type=int, default=40)
    d.add_argument("--show", action="store_true", help="show the browser")

    s = P("score")
    s.add_argument("--min-score", type=float, default=40.0)
    s.add_argument("--rescore", action="store_true", help="re-score everything")
    s.add_argument("--verbose", action="store_true")

    P("queue")

    dr = P("draft")
    dr.add_argument("--limit", type=int, default=10)
    dr.add_argument("--engine", choices=["claude", "groq"], default="claude")

    t = P("topics")
    t.add_argument("--limit", type=int, default=10)
    t.add_argument("--min-mentions", type=int, default=2)

    pr = P("propose")
    pr.add_argument("--limit", type=int, default=3)
    pr.add_argument("--skip-rules", action="store_true")
    pr.add_argument("--force", action="store_true")

    P("ingest-threads")

    P("connect")
    P("whoami")

    p = P("post")
    p.add_argument("--thread", default=None)
    p.add_argument("--yes", action="store_true")
    p.add_argument("--mode", choices=["profile", "cdp"], default="profile",
                   help="profile = this site's own browser (default); "
                        "cdp = attach to Chrome on port 9222")

    P("report")

    args = ap.parse_args()

    handlers = {
        "init": cmd_init, "sites": cmd_sites, "add-site": cmd_add_site,
        "site-edit": cmd_site_edit, "discover": cmd_discover, "score": cmd_score,
        "queue": cmd_queue, "draft": cmd_draft, "ingest": cmd_ingest,
        "topics": cmd_topics, "propose": cmd_propose,
        "ingest-threads": cmd_ingest_threads, "post": cmd_post,
        "connect": cmd_connect, "whoami": cmd_whoami,
        "report": cmd_report, "status": cmd_status,
        "check-env": cmd_check_env, "check-subs": cmd_check_subs,
    }
    try:
        handlers[args.cmd](args)
    except KeyboardInterrupt:
        print("\ninterrupted.")
        sys.exit(130)


if __name__ == "__main__":
    main()


