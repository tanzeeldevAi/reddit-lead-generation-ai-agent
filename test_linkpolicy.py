#!/usr/bin/env python3
"""Verify the link policy: warm-up gate, then rolling ratio cap. Posts nothing.

Expected output is NO -> YES -> NO:

  1. A brand new account may not link, however relevant the thread. It has to
     earn that by being useful for free first (limits.warmup_posts).
  2. After enough link-free helpful replies, linking is allowed.
  3. Once too many recent replies carried a link, it is refused again until the
     rolling 30-day ratio comes back under limits.max_link_ratio.

Run:  python test_linkpolicy.py
"""
from aa import config, db, draft, limits, score
import smoke_test


def show(label):
    print(f"\n--- {label} ---")
    for row in db.threads_by_status("queued", limit=10):
        ok, _url = draft.should_link(cfg, row)
        print(f"  {row['native_id']:<34} link={'YES' if ok else 'NO'}")


cfg = config.load()
db.init()
site = smoke_test.ensure_site(cfg)

for r in smoke_test.FAKE:
    db.upsert_thread({**r, "site_id": site["id"]})
score.run(site, min_score=cfg.get_path("scoring.min_score", 40))

show("WARM-UP ACTIVE (0 posts, need %d)" % cfg.get_path("limits.warmup_posts", 10))

for i in range(cfg.get_path("limits.warmup_posts", 10)):
    db.record_post("reddit:t_hi", None, "reddit", f"sub{i}", f"http://x/{i}", False)
show("AFTER ENOUGH LINK-FREE HELPFUL POSTS")

for i in range(5):
    db.record_post("reddit:t_hi", None, "reddit", f"s{i}", f"http://y/{i}", True)
linked, total, ratio = limits.link_ratio(site["id"])
cap = cfg.get_path("limits.max_link_ratio", 0.25)
show(f"AFTER 5 LINKED POSTS (ratio {linked}/{total} = {ratio:.0%}, cap {cap:.0%})")

print("\nExpected: NO -> YES -> NO")
