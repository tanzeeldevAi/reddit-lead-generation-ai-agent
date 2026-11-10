<div align="center">

# Authority Agent — Find Real Reddit Threads, Write Real Answers

**Give it your website. It finds the Reddit threads where people have exactly the problem you
solve, and drafts a reply that is genuinely useful — not an advert.**

Most "Reddit marketing bots" get accounts banned in a week because they drop links on anything
that matches a keyword. This one is built the other way round: it rejects hard, earns the right
to link before it links, and shows you every draft before anything is posted.

[![Python](https://img.shields.io/badge/Python-3.10%2B-3776AB?logo=python&logoColor=white)](https://python.org)
[![Tests](https://github.com/tanzeeldevAi/reddit-lead-generation-ai-agent/actions/workflows/tests.yml/badge.svg)](https://github.com/tanzeeldevAi/reddit-lead-generation-ai-agent/actions/workflows/tests.yml)
[![No API keys](https://img.shields.io/badge/Reddit%20API-not%20required-FF4500?logo=reddit&logoColor=white)](#requirements)
[![Developed by Tanzeel](https://img.shields.io/badge/Developed%20by-Tanzeel-6C3EF5)](https://github.com/tanzeeldevAi)
[![License](https://img.shields.io/badge/License-MIT-green.svg)](LICENSE)
[![PRs Welcome](https://img.shields.io/badge/PRs-welcome-brightgreen.svg)](#contributing)

</div>

---

## What is this?

You know your customers are asking about your problem on Reddit right now. You also know that
showing up and posting a link gets you removed, downvoted, and eventually banned.

Authority Agent does the slow, careful version of this properly:

1. You give it your website URL.
2. It reads the site, works out what problems you actually solve, and builds search terms from
   the **problem**, not your industry name.
3. Every night it searches Reddit and Quora, scores what it finds, and **throws most of it away**.
4. For the handful that survive, it drafts a reply in your voice — specific, useful, usually with
   no link at all.
5. You open a dashboard, read each draft, and approve the ones you like.

```
Your website  ──►  niche detection  ──►  search  ──►  score (rejects hard)
                                                            │
                    you approve  ◄──  draft  ◄──────────────┘
```

> **Nothing is posted without you clicking approve.** There is no fire-and-forget mode, on
> purpose.

---

## Why it doesn't get you banned

This is the actual engineering, and it's most of the value:

| | |
|---|---|
| 🚫 **The scorer's job is rejecting** | Reddit search never returns nothing — it pads results with junk. A hard keyword gate runs *before* any numeric score, so unrelated threads are dropped outright, not ranked low |
| 🎯 **Problem keywords, not industry** | Measured: searching an industry name returns pure noise. Searching the problem your customer types returns real threads from real owners |
| ⏳ **Warm-up before any link** | A new account whose first replies all carry links reads as promotional no matter how good the writing is. It must post useful, link-free replies first |
| 📊 **Rolling link ratio** | At most 1 in 5 replies over 30 days may carry a link, enforced in Python — not left to the model's judgement |
| 🔗 **Free tools only, never service pages** | A free resource earns thanks. A `/services/` page is an advert and gets reported |
| 🏷️ **Pages are tiered by how "branded" they are** | Each page is fetched and measured. One saturated with your industry's vocabulary is only linkable on threads genuinely about that industry |
| 🐢 **Human pacing** | Caps per day, minimum gaps between actions, per-subreddit cooldowns, dwell time on the page |
| 👤 **One account per site** | Each site gets its own isolated browser profile, so sessions never bleed into each other |

---

## Requirements

Before you start, make sure you have:

- **[Python 3.10 or newer](https://python.org/downloads)** — on Windows, tick
  **"Add Python to PATH"** while installing
- **A Reddit account** you're willing to use — ideally one with some existing history
- **A Claude subscription** (Claude Pro/Max) with the
  [Claude Code CLI](https://docs.claude.com/en/docs/claude-code) signed in,
  **or** a free [Groq](https://console.groq.com) key

> **There is no Reddit API app to create and no Reddit credentials to store.** Discovery reads
> Reddit's public web pages through a real browser. Posting uses a browser profile you log into by
> hand, once. Your Reddit password never touches this tool.

---

## Install — step by step

It takes about **15 minutes**. Everything used here is free.

### Step 1 — Download

```bash
git clone https://github.com/tanzeeldevAi/reddit-lead-generation-ai-agent.git
cd reddit-lead-generation-ai-agent
```

No git? Click the green **Code** button above → **Download ZIP** → unzip it.

### Step 2 — Install

```bash
pip install -r requirements.txt
python -m playwright install chromium
```

That second line downloads the browser it drives. It's a one-time ~150 MB download.

### Step 3 — Check it works before touching anything live

```bash
python smoke_test.py
```

This seeds four fake threads and runs the whole pipeline — scoring, link policy, drafting, safety
rails — **completely offline**. Nothing is searched and nothing is posted.

You should see one thread queued and three rejected, including a laptop-buying question that has
nothing to do with the example niche. That rejection is the point.

Then check the link policy specifically:

```bash
python test_linkpolicy.py
```

Expected output is **NO → YES → NO**: a new account can't link, an account that's been useful can,
and an account that's linked too often recently can't again.

### Step 4 — Add your site

```bash
python run.py init
python run.py add-site yourdomain.com --account your_reddit_username
```

This reads your website, works out your niche and audience, finds your linkable pages and measures
how vertical-specific each one is.

### Step 5 — Tune the config

Open **`config.yaml`** and set:

- `site.what_we_do` — one or two plain sentences, the way you'd say it out loud
- `value_pages` — your **free tools and guides only**. Never a service or pricing page.
  Leaving this empty is fine and is the safest way to start: the agent then writes pure-help
  replies with no link at all
- `reddit.subreddits` and `reddit.queries` — where your customers actually post

Then confirm those subreddits exist and see their sizes:

```bash
python run.py check-subs
```

### Step 6 — Connect your Reddit account

```bash
python run.py connect
```

A browser window opens on the Reddit login page. **You log in by hand**, then come back to the
terminal and press Enter. That session is saved to an isolated profile for this site only.

Check it took:

```bash
python run.py whoami
```

### Step 7 — Run a cycle

```bash
python daily.py
```

About ten minutes. It searches, scores, and writes drafts. **It posts nothing.**

Then open the dashboard:

```bash
python serve.py 8788
```

Go to <http://localhost:8788>, read the queue, and approve what you like.

**That's it — you're running.** 🎉

> On Windows you can skip the commands entirely: double-click **`START.bat`** for a menu that
> covers all of the above.

---

## Commands

| Command | What it does |
|---|---|
| `python run.py init` | Create the database and folders |
| `python run.py add-site <url>` | Detect a site's niche and add it |
| `python run.py sites` | List your sites |
| `python run.py discover` | Search Reddit and Quora for new threads |
| `python run.py score` | Score what was found and queue the good ones |
| `python run.py queue` | Show what's waiting |
| `python run.py draft` | Write replies for the queue |
| `python run.py connect` | Log a Reddit account in (once per site) |
| `python run.py whoami` | Check which account is logged in |
| `python run.py post` | Show one draft and ask before sending |
| `python run.py report` | What the agent has actually done |
| `python run.py check-subs` | Verify your subreddits exist, see sizes |
| `python daily.py` | The whole cycle: discover, score, draft |
| `python serve.py 8788` | The dashboard |

---

## Drafting: two engines

Set `draft.engine` in `config.yaml`:

- **`claude`** (default) — writes a prompt file per thread into `drafts/pending/`. You run those
  through the Claude CLI on your existing subscription. Best quality, no API key, no per-draft bill.
- **`groq`** — calls [Groq's free tier](https://console.groq.com) directly over HTTP. Fully
  automatic. Put `GROQ_API_KEY` in `.env` (copy `.env.example` to `.env` first).

---

## Troubleshooting

<details>
<summary><b>Everything gets rejected and the queue is empty</b></summary>

Usually correct behaviour on day one — the gate is deliberately strict. If it stays empty:

1. Your `reddit.queries` are probably industry terms. Change them to the **problem** your customer
   would type. "limo marketing" finds nothing; "google business profile not showing" finds people.
2. Lower `scoring.min_score` from 40 to about 30 and see what comes through.
3. Run `python run.py check-subs` to confirm your subreddits are real and active.
</details>

<details>
<summary><b>Drafts never contain a link</b></summary>

That's the warm-up gate doing its job, and it's usually right. Three things must all be true before
a link is allowed: the thread strongly matches a value page, the account is past
`limits.warmup_posts`, and the 30-day link ratio is still under `limits.max_link_ratio`.

Run `python test_linkpolicy.py` to see the three states demonstrated.
</details>

<details>
<summary><b>"config not found: config.yaml"</b></summary>

You're running from the wrong folder. `cd` into the project directory first.
</details>

<details>
<summary><b>The browser doesn't open / Playwright errors</b></summary>

You skipped `python -m playwright install chromium` in Step 2. Run it.
</details>

<details>
<summary><b>`run.py whoami` says nobody is logged in</b></summary>

The login in Step 6 didn't complete. Run `python run.py connect` again, log in **fully** (including
any 2FA), wait for the Reddit home feed to load, and only then press Enter in the terminal.
</details>

<details>
<summary><b>Reddit search returns obviously unrelated posts</b></summary>

Known and expected — Reddit pads thin result sets with filler. That's exactly why the hard keyword
gate exists. If filler is reaching your queue, your keyword list is too broad.
</details>

---

## How it works

Read **[HOW-IT-WORKS.md](HOW-IT-WORKS.md)** for the plain-English walkthrough — no code knowledge
needed.

In short:

- **`aa/niche.py`** reads your site and derives problem-space keywords
- **`aa/reddit.py`** / **`aa/quora.py`** search through a real browser (Reddit's `.json` endpoints
  now 403 unauthenticated clients; the web UI serves anyone)
- **`aa/score.py`** runs the hard keyword gate first, then a soft numeric score
- **`aa/links.py`** fetches each of your pages and measures its vertical density to tier it
- **`aa/draft.py`** builds the prompt and enforces the link decision in Python
- **`aa/limits.py`** holds every rail: daily caps, gaps, cooldowns, warm-up, link ratio
- **`aa/post.py`** posts, only after you approve

There's also a small set of genuinely universal HTML tools in `unbranded-tools/` you can host as
your own free resources — the kind of page that is safe to link.

---

## Please use this responsibly

This automates participation in communities run by volunteers who are tired of being marketed at.
The rails exist because they should, not to be tuned around.

If your reply wouldn't be worth reading with the link removed, don't post it.

---

## Contributing

Issues and pull requests are welcome — especially more platforms, better niche detection, and
smarter rejection.

## License

[MIT](LICENSE) — free to use, change and share. If it helps you, a ⭐ on the repo is appreciated.

---

<div align="center">

**Built by [@tanzeeldevAi](https://github.com/tanzeeldevAi)**

*reddit lead generation · reddit marketing · social listening · content marketing automation
AI agent · Claude AI · Groq · Playwright · Python · lead generation tool · community marketing
quora automation · organic marketing · inbound leads · SaaS marketing · local SEO*

</div>
