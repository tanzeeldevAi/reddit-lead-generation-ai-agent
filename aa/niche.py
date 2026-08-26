"""Work out what a site is about from its URL alone.

Produces a *proposal* -- niche, audience, problem keywords, subreddits, linkable
pages -- which the dashboard shows for review. Nothing is ever used until the
user has seen it, because a wrong niche silently poisons every later stage.

Design note: the keywords it proposes are PROBLEM-space, not industry-space.
Testing on Reddit showed industry terms ("limo", "limousine", "black car
service") return pure noise, while the problem those businesses have
("google business profile ranking", "service area business") returns real
threads. See docs in README.
"""
from __future__ import annotations

import re
from collections import Counter
from html import unescape
from urllib.parse import urljoin, urlparse

import requests

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36")

# Pages worth checking beyond the homepage, in priority order.
PROBE_PATHS = ["/", "/services/", "/free-tools/", "/tools/", "/resources/",
               "/about/", "/blog/", "/what-we-do/"]

STOP = set("""
a an and are as at be by for from has have how i if in is it its of on or that the
to was were what when where which who will with you your our we us this these those
can could should would about more most other into than then them they their there
here just like get got new now one two all any some own very also but not no yes do
does did done make made use used using need needs want wants help helps free best
top good great service services business businesses company companies page pages
site website home contact click here read learn find out see view more info
""".split())

# Signals that map a page's language to the problem space it lives in.
# Each entry: (regex over site text) -> (problem keywords, candidate subreddits)
PROBLEM_MAP = [
    (r"\b(local seo|google business profile|gbp|google maps|map pack|3.?pack|citations?)\b",
     ["google business profile ranking", "google maps ranking local business",
      "local seo service area business", "gbp optimization"],
     ["SEO", "smallbusiness", "GoogleMyBusiness", "juststart"]),
    (r"\b(seo|search engine|ranking|serp|backlinks?|keywords?)\b",
     ["how to rank local business google", "seo for small business",
      "why is my site not ranking"],
     ["SEO", "bigseo", "smallbusiness", "juststart"]),
    (r"\b(web ?site design|web design|landing page|conversion|cro)\b",
     ["small business website not converting", "website redesign worth it"],
     ["web_design", "smallbusiness", "Entrepreneur"]),
    (r"\b(google ads|ppc|paid ads|adwords|meta ads|facebook ads)\b",
     ["google ads wasting money small business", "ppc for local business"],
     ["PPC", "smallbusiness", "digital_marketing"]),
    (r"\b(reviews?|reputation|testimonials?)\b",
     ["how to get more google reviews", "review automation small business"],
     ["smallbusiness", "GoogleMyBusiness"]),
    (r"\b(booking|reservation|dispatch|scheduling)\b",
     ["online booking system small business", "missed calls losing bookings"],
     ["smallbusiness", "sweatystartup"]),
]


def fetch(url: str, timeout: int = 25) -> str:
    try:
        r = requests.get(url, headers={"User-Agent": UA}, timeout=timeout)
        if r.status_code != 200:
            return ""
        # requests falls back to latin-1 when a server omits the charset, which
        # turns UTF-8 punctuation into mojibake ("Â·"). Trust the document.
        if not r.encoding or r.encoding.lower() in ("iso-8859-1", "latin-1"):
            r.encoding = r.apparent_encoding or "utf-8"
        return r.text
    except requests.RequestException:
        return ""


def _tidy(s: str) -> str:
    s = re.sub(r"\s+", " ", s.replace("\xa0", " ")).strip()
    # drop a trailing "…2026 · 8 min read" style tail from blog cards
    s = re.sub(r"\s*(19|20)\d{2}\s*[·|•\-–]+.*$", "", s)
    s = re.sub(r"\s*[·|•]\s*\d+\s*min.*$", "", s, flags=re.I)
    return s.strip(" -–|·/")


def _is_junk(s: str) -> bool:
    """Card markup often prefixes a number/eyebrow ("/04 BULK") before the real
    title. Those make useless link labels."""
    if len(s) < 4:
        return True
    if re.fullmatch(r"[\W\d]+", s):            # digits/punctuation only
        return True
    if re.match(r"^/?\d{1,3}\b", s):           # leading index like "04 ..."
        return True
    if len(s.split()) < 2 and s.isupper():     # lone eyebrow word "BULK"
        return True
    return False


def label_from_url(url: str) -> str:
    slug = [p for p in urlparse(url).path.split("/") if p]
    if not slug:
        return ""
    return re.sub(r"[-_]+", " ", slug[-1]).strip().title()


def clean_label(text: str, url: str = "") -> str:
    """Anchor text is frequently several lines of card markup. Pick the most
    title-like line rather than blindly taking the first, and fall back to the
    URL slug when every line is junk."""
    raw = unescape(re.sub(r"<[^>]+>", "\n", text))
    lines = [_tidy(l) for l in raw.split("\n")]
    good = [l for l in lines if l and not _is_junk(l)]
    if not good:
        return label_from_url(url)

    # A card title is short, has a couple of words, and does not read as a
    # sentence. Descriptions are long and often end in a full stop. Titles also
    # come first in DOM order, so take the earliest line that looks like one.
    for l in good:
        words = len(l.split())
        if 2 <= words <= 9 and len(l) <= 60 and not l.endswith("."):
            return l[:70]
    return good[0][:70]


def strip_html(html: str) -> str:
    html = re.sub(r"<(script|style|noscript)[^>]*>.*?</\1>", " ", html,
                  flags=re.S | re.I)
    return unescape(re.sub(r"<[^>]+>", " ", html))


def tag(html: str, pattern: str) -> list[str]:
    return [unescape(re.sub(r"<[^>]+>", "", m)).strip()
            for m in re.findall(pattern, html, flags=re.S | re.I)]


def meta(html: str, name: str) -> str:
    m = re.search(
        rf'<meta[^>]+(?:name|property)=["\'][^"\']*{name}[^"\']*["\'][^>]+'
        rf'content=["\']([^"\']+)', html, flags=re.I)
    if not m:
        m = re.search(
            rf'<meta[^>]+content=["\']([^"\']+)["\'][^>]+(?:name|property)='
            rf'["\'][^"\']*{name}', html, flags=re.I)
    return unescape(m.group(1)).strip() if m else ""


def internal_links(html: str, base: str) -> list[tuple[str, str]]:
    host = urlparse(base).netloc
    out, seen = [], set()
    for href, text in re.findall(r'<a[^>]+href=["\']([^"\']+)["\'][^>]*>(.*?)</a>',
                                 html, flags=re.S | re.I):
        full = urljoin(base, href)
        if urlparse(full).netloc != host:
            continue
        full = full.split("#")[0].rstrip("/") + "/"
        label = clean_label(text, full)
        if not label or len(label) > 70 or full in seen:
            continue
        seen.add(full)
        out.append((full, label))
    return out


def top_terms(text: str, n: int = 25) -> list[str]:
    words = re.findall(r"[a-z][a-z'-]{2,}", text.lower())
    freq = Counter(w for w in words if w not in STOP and len(w) > 3)
    return [w for w, _ in freq.most_common(n)]


def detect(url: str) -> dict:
    """Crawl a few pages and propose a full site profile."""
    if not url.startswith("http"):
        url = "https://" + url
    base = url.rstrip("/")

    pages: dict[str, str] = {}
    for path in PROBE_PATHS:
        html = fetch(urljoin(base + "/", path.lstrip("/")))
        if html and len(html) > 500:
            pages[path] = html
        if len(pages) >= 4:
            break

    if not pages:
        return {"error": f"Could not fetch {base}. Check the URL is reachable.",
                "url": base}

    home = pages.get("/") or next(iter(pages.values()))
    title = (tag(home, r"<title[^>]*>(.*?)</title>") or [""])[0]
    desc = meta(home, "description")
    h1s = tag(home, r"<h1[^>]*>(.*?)</h1>")[:3]
    h2s = tag(home, r"<h2[^>]*>(.*?)</h2>")[:12]

    all_text = " ".join(strip_html(h) for h in pages.values())
    all_text = re.sub(r"\s+", " ", all_text)

    # --- problem-space mapping -------------------------------------------
    keywords, communities = [], []
    low = all_text.lower()
    for pattern, kws, subs in PROBLEM_MAP:
        if re.search(pattern, low):
            keywords += kws
            communities += subs
    keywords = list(dict.fromkeys(keywords))
    communities = list(dict.fromkeys(communities))

    # --- linkable pages: prefer tools/guides, they are safest to link -----
    links = internal_links(home, base)
    for extra in ("/free-tools/", "/tools/", "/resources/"):
        if extra in pages:
            links += internal_links(pages[extra], base)
    value_pages = []
    seen = set()
    for full, label in links:
        p = urlparse(full).path.lower()
        if not re.search(r"(tool|guide|calculator|generator|checker|resource|blog|how-to)", p):
            continue
        if full in seen or len(label) < 4:
            continue
        seen.add(full)
        value_pages.append({"url": full, "title": label,
                            "answers": "", "auto": True})
        if len(value_pages) >= 12:
            break

    niche, audience = _phrase(title, desc, h1s, all_text)

    return {
        "url": base,
        "name": title.split("|")[0].split("-")[0].strip()[:60] or urlparse(base).netloc,
        "niche": niche,
        "audience": audience,
        "keywords": keywords,
        "communities": communities,
        "value_pages": value_pages,
        "evidence": {
            "title": title, "description": desc,
            "h1": h1s, "h2": h2s[:8],
            "pages_crawled": list(pages.keys()),
            "top_terms": top_terms(all_text),
        },
        "confidence": _confidence(keywords, value_pages, desc),
    }


def _phrase(title: str, desc: str, h1s: list[str], text: str) -> tuple[str, str]:
    """Best-effort one-liners. Deliberately simple -- the user reviews these."""
    niche = desc or (h1s[0] if h1s else "") or title
    niche = re.sub(r"\s+", " ", niche).strip()[:180]

    aud = ""
    m = re.search(
        r"\bfor ([a-z][a-z ]{3,40}?(?:companies|operators|businesses|owners|"
        r"clinics|shops|agencies|professionals|services))\b", text, flags=re.I)
    if m:
        aud = m.group(1).strip()
    return niche, aud


def _confidence(keywords: list, pages: list, desc: str) -> str:
    score = len(keywords) + len(pages) + (2 if desc else 0)
    return "high" if score >= 10 else "medium" if score >= 5 else "low"


def render(p: dict) -> str:
    """Console preview -- this is what the user checks before it goes live."""
    if p.get("error"):
        return f"ERROR: {p['error']}"
    L = ["=" * 64, f"NICHE PROPOSAL -- {p['url']}", "=" * 64,
         f"confidence: {p['confidence'].upper()}", "",
         f"name:     {p['name']}", f"niche:    {p['niche']}",
         f"audience: {p['audience'] or '(not detected -- please fill in)'}", ""]
    L += [f"KEYWORDS ({len(p['keywords'])})  -- problem-space, not industry"]
    for k in p["keywords"]:
        L.append(f"   - {k}")
    L += ["", f"SUBREDDITS ({len(p['communities'])})"]
    L.append("   " + ", ".join("r/" + c for c in p["communities"]))
    L += ["", f"LINKABLE PAGES ({len(p['value_pages'])})"]
    for v in p["value_pages"][:12]:
        L.append(f"   - {v['title'][:44]:<46} {v['url']}")
    ev = p["evidence"]
    L += ["", "EVIDENCE", f"   crawled: {', '.join(ev['pages_crawled'])}",
          f"   title:   {ev['title'][:70]}",
          f"   terms:   {', '.join(ev['top_terms'][:14])}",
          "", "=" * 64,
          "Review this. Anything wrong -> edit it in the dashboard or with:",
          "   python run.py site-edit <id> --niche \"...\" --audience \"...\"",
          "=" * 64]
    return "\n".join(L)
