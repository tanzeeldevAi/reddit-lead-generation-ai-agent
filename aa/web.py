"""Dashboard. Stdlib only -- no Flask, nothing to install on the VPS.

Serves a single page plus a small JSON API. Every mutating call is POST.

Long jobs (niche detection, discovery) run on a background thread and report
progress through /api/jobs, so the browser never sits on a 60-second request.
"""
from __future__ import annotations

import json
import threading
import time
import traceback
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from . import db, links, niche, report, score

HERE = Path(__file__).parent
JOBS: dict[str, dict] = {}
_lock = threading.Lock()


# ---------------------------------------------------------------------------
# background jobs
# ---------------------------------------------------------------------------

def start_job(name: str, fn, *args, **kwargs) -> str:
    job_id = f"{name}-{int(time.time()*1000)}"
    with _lock:
        JOBS[job_id] = {"id": job_id, "name": name, "state": "running",
                        "log": [], "result": None, "started": time.time()}

    def log(msg):
        with _lock:
            JOBS[job_id]["log"].append(str(msg))

    def run():
        try:
            res = fn(log, *args, **kwargs)
            with _lock:
                JOBS[job_id].update(state="done", result=res)
        except Exception as e:
            with _lock:
                JOBS[job_id].update(state="error", result=str(e))
                JOBS[job_id]["log"].append(traceback.format_exc()[-1500:])

    threading.Thread(target=run, daemon=True).start()
    return job_id


def job_detect(log, url: str):
    log(f"fetching {url} ...")
    p = niche.detect(url)
    if p.get("error"):
        raise RuntimeError(p["error"])
    log(f"niche detected, confidence {p['confidence']}")
    log(f"{len(p['keywords'])} keywords, {len(p['communities'])} communities, "
        f"{len(p['value_pages'])} pages")
    return p


def job_add_site(log, proposal: dict):
    log("creating site ...")
    site_id = db.add_site(
        url=proposal["url"], name=proposal.get("name", ""),
        niche=proposal.get("niche", ""), audience=proposal.get("audience", ""),
        keywords=proposal.get("keywords", []),
        communities=proposal.get("communities", []),
        value_pages=proposal.get("value_pages", []),
        reddit_user=proposal.get("reddit_user", ""))
    site = db.get_site(site_id)
    log(f"site #{site_id} created. Tiering {len(site['value_pages'])} pages ...")
    pages = links.classify_site(site, verbose=False)
    for p in pages:
        log(f"  {p.get('tier','?').upper():<10} {p.get('density')}/1k  {p['title'][:40]}")
    db.update_site(site_id, value_pages=pages)
    summary = links.summarize(pages)
    log(f"tiers: {summary}")
    if summary.get("universal", 0) == 0:
        log("NOTE: no universal pages. Broad threads will get no-link comments "
            "until you publish something unbranded.")
    return {"site_id": site_id, "tiers": summary}


def job_discover(log, site_id: int, window: str):
    from . import reddit
    site = db.get_site(site_id)
    if not site:
        raise RuntimeError("site not found")
    log(f"discovering for {site['name']} ({window}) ...")

    keywords, communities = site["keywords"], site["communities"]
    seen = db.known_ids("reddit")
    total_new = 0
    from . import browser
    with browser.fresh(headless=True) as page:
        probes = [(c, k) for c in communities for k in keywords]
        for i, (sub, kw) in enumerate(probes, 1):
            rows = reddit.search(page, kw, subreddit=sub, window=window, limit=25)
            fresh_rows = [r for r in rows if r["native_id"] not in seen]
            for r in fresh_rows:
                seen.add(r["native_id"])
                if db.upsert_thread({
                        "site_id": site_id, "platform": "reddit",
                        "native_id": r["native_id"], "url": r["url"],
                        "title": r["title"], "community": r["community"],
                        "status": "new", "discovered_at": time.time()}):
                    total_new += 1
            log(f"[{i}/{len(probes)}] r/{sub} \"{kw[:32]}\" -> "
                f"{len(rows)} found, {len(fresh_rows)} new")
            browser.human_pause(2.0, 4.0)

    log(f"enriching bodies ...")
    n = reddit.enrich(site_id, limit=40, headless=True, verbose=False)
    log(f"enriched {n}")

    res = score.run(db.get_site(site_id), min_score=40.0)
    log(f"scored: {res}")
    return {"new": total_new, "enriched": n, **res}


def job_rescore(log, site_id: int, min_score: float):
    n = db.reset_scores(site_id)
    log(f"reset {n} threads")
    res = score.run(db.get_site(site_id), min_score=min_score)
    log(f"{res}")
    return res


# ---------------------------------------------------------------------------
# API
# ---------------------------------------------------------------------------

def api(path: str, body: dict) -> dict:
    if path == "/api/state":
        sites = db.list_sites()
        for s in sites:
            s["stats"] = report.summary(s["id"])
            s["queue"] = len([r for r in db.threads_by_status("queued", 500)
                              if r["site_id"] == s["id"]])
            s["tiers"] = links.summarize(s["value_pages"])
        return {"sites": sites}

    if path == "/api/detect":
        return {"job": start_job("detect", job_detect, body["url"])}

    if path == "/api/site/add":
        return {"job": start_job("add", job_add_site, body["proposal"])}

    if path == "/api/site/update":
        db.update_site(int(body["site_id"]), **body["fields"])
        return {"ok": True}

    if path == "/api/discover":
        return {"job": start_job("discover", job_discover,
                                 int(body["site_id"]),
                                 body.get("window", "year"))}

    if path == "/api/rescore":
        return {"job": start_job("rescore", job_rescore, int(body["site_id"]),
                                 float(body.get("min_score", 40)))}

    if path == "/api/queue":
        sid = int(body["site_id"])
        rows = []
        for r in db.threads_by_status("queued", 200):
            if r["site_id"] != sid:
                continue
            d = json.loads(r["score_detail"] or "{}")
            draft = db.latest_draft(r["id"])
            rows.append({
                "id": r["id"], "title": r["title"], "url": r["url"],
                "community": r["community"], "score": r["score"],
                "keyword": d.get("best_keyword", ""),
                "link": d.get("link", ""), "link_reason": d.get("link_reason", ""),
                "comments": r["num_comments"], "body": (r["body"] or "")[:600],
                "draft": draft["body"] if draft else "",
                "draft_id": draft["id"] if draft else None,
            })
        return {"threads": rows}

    if path == "/api/report":
        sid = body.get("site_id")
        sid = int(sid) if sid else None
        return {"summary": report.summary(sid),
                "by_day": report.by_day(30, sid),
                "by_community": report.by_community(sid)}

    if path == "/api/job":
        with _lock:
            return JOBS.get(body["id"], {"state": "missing"})

    return {"error": f"unknown endpoint {path}"}


# ---------------------------------------------------------------------------
# server
# ---------------------------------------------------------------------------

class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass                        # keep the console clean for job output

    def _send(self, code: int, body: bytes, ctype: str):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        p = urlparse(self.path).path
        if p in ("/", "/index.html"):
            html = (HERE / "dashboard.html").read_bytes()
            return self._send(200, html, "text/html; charset=utf-8")
        if p.startswith("/api/"):
            q = parse_qs(urlparse(self.path).query)
            body = {k: v[0] for k, v in q.items()}
            try:
                out = api(p, body)
            except Exception as e:
                out = {"error": str(e)}
            return self._send(200, json.dumps(out).encode(), "application/json")
        self._send(404, b"not found", "text/plain")

    def do_POST(self):
        length = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(length) if length else b"{}"
        try:
            body = json.loads(raw or b"{}")
        except json.JSONDecodeError:
            body = {}
        try:
            out = api(urlparse(self.path).path, body)
        except Exception as e:
            out = {"error": str(e), "trace": traceback.format_exc()[-800:]}
        self._send(200, json.dumps(out).encode(), "application/json")


def serve(port: int = 8787, host: str = "127.0.0.1"):
    db.init()
    srv = ThreadingHTTPServer((host, port), Handler)
    print(f"\n  Authority Agent dashboard\n  http://localhost:{port}\n")
    print("  Ctrl+C to stop\n")
    srv.serve_forever()


if __name__ == "__main__":
    serve()
