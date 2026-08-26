# How This Agent Works

A plain-English walkthrough. No code knowledge needed.

---

## 1. What it does

You give it a website URL. It then:

1. Works out what the site is about
2. Goes to Reddit and finds threads where people have that exact problem
3. Throws away the ones that only *look* relevant
4. Writes a genuinely helpful reply for each
5. Decides whether that reply is allowed to contain a link
6. Waits for you to approve before anything is posted
7. Keeps a record of everything it did

It also writes its **own** threads, based on questions people keep asking.

---

## 2. The big picture

```
   You type a URL
        |
        v
   [ 1. UNDERSTAND ]  what is this site about?
        |
        v
   [ 2. SEARCH ]      find threads on Reddit
        |
        v
   [ 3. FILTER ]      bin the junk   <-- most important step
        |
        v
   [ 4. WRITE ]       draft a helpful reply
        |
        v
   [ 5. LINK RULES ]  is a link allowed here?
        |
        v
   [ 6. YOU APPROVE ] nothing posts without this
        |
        v
   [ 7. TRACK ]       how many, where, what happened
```

---

## 3. Step by step

### Step 1 — Understand the site

You give it `yourdomain.com`. It downloads a few pages (home, services, free
tools, about) and pulls out what the business does, who its customers are, and
which pages are worth linking to.

**The important trick here: it does NOT search for your industry.**

We tested this. Searching Reddit for "limo" and "limousine" returned car
washes, vinyl records, and a thread called "Cactus. Khakis." There simply is
not enough limo talk on Reddit.

So instead it searches for **the problem you solve**:

| Bad (industry) | Good (problem) |
|---|---|
| limo | google business profile ranking |
| limousine | how to rank local business google |
| black car service | seo for small business |

A limo owner asking "why is my Google listing not showing up" uses the
*second* set of words, not the first.

### Step 2 — Search Reddit

It opens a normal browser window (invisible, in the background) and searches
Reddit the way you would.

**No API key. No Reddit developer app. No password.** Reddit blocked their free
data feed, so we just read the website directly.

It searches every combination — 6 subreddits x 15 keywords = 90 searches —
then opens each thread it found and reads the full post and comments.

### Step 3 — Filter (the most important step)

Here is the thing nobody expects: **Reddit search never returns nothing.**

If it cannot find good matches, it fills the page with random unrelated posts.
So roughly 60% of what comes back is garbage.

Without this step, the agent would write limo-SEO advice on a thread about
junk removal. That is how accounts get banned.

So before anything else, each thread must pass a hard test:

> Does this thread contain at least **two** of the important words from one of
> our keywords?

One word is a coincidence ("business" appears everywhere). Two or more is a
real match.

Real run on your account: **398 threads found, 243 thrown away, 125 kept.**

Threads that survive get a score out of 100 based on:

- How well it matches (counts most)
- How recent it is
- How many comments it already has — few is good, our reply gets seen;
  hundreds means we are too late
- Whether it is actually a question

### Step 4 — Write the reply

The agent builds a detailed instruction sheet for each thread containing the
full post, the comments, who you are, and strict rules:

- Answer the actual question
- Be specific, name the exact setting or number
- Never start with "Great question"
- No em-dashes, no bullet-point walls (they read as AI)
- If you have nothing useful to say, write SKIP

Claude then writes the reply from that sheet. **This costs nothing** — it uses
your existing Claude Code session, not a paid API.

### Step 5 — Link rules

This is where most people get banned, so it is enforced in code, not left to
the AI. An AI told "link when relevant" will link every single time.

**Every page on your site was measured** for how heavily it is branded to your
industry:

| Type | Meaning | Where it can be linked |
|---|---|---|
| universal | any business could use it | anywhere relevant |
| semi | mostly generic | related threads |
| locked | full of industry words | only industry threads, or your own posts |

All 12 of one client's free tools measured **locked** — even the Citation
Checker, which any business could use, says "limo" 45 times. A plumber clicking
that would bounce straight off.

So right now broad threads get **helpful comments with no link**. That is
correct, not a failure.

**Two separate gates must both open before any link goes out:**

1. **Warm-up** — the account must have posted 15 genuinely helpful comments
   first. An account whose first comments all carry links looks promotional no
   matter how good the writing is.
2. **Ratio** — no more than 25% of your comments in the last 30 days may carry
   a link.

### Step 6 — You approve

The agent **never posts on its own.** It fills a queue and stops.

When you do post, it:

- Shows you the full reply
- Asks yes or no
- Checks the browser is logged into the right account, and refuses if not
- Types the text out slowly, like a person, instead of pasting instantly

### Step 7 — Track

Every post is recorded. The Report tab shows threads vs comments for the last
24 hours, 7 days, 30 days and all time, plus which subreddits you posted in and
your current link ratio.

---

## 4. Writing its own threads

Comments are guest appearances on someone else's question. Your own thread is
where an industry-specific link genuinely belongs, because you set the topic.

The agent does **not** invent topics. It looks at everything it has found and
asks: what do several different people keep asking that nobody answered well?

Real example from your data:

```
"missed calls"     8 people asked                  <- gap, nobody answered well
"google business"  11 asked, avg 20 comments       <- saturated, skip it
```

It also reads each subreddit's rules and warns you when self-promotion is
restricted.

Threads have much stricter limits than comments: **one per subreddit per
week**, because two self-posts in one week is the pattern moderators ban for.

---

## 5. The files

| File | What it does |
|---|---|
| `niche.py` | reads your website, works out the niche and keywords |
| `reddit.py` | searches Reddit through a browser |
| `score.py` | throws away junk, scores the rest |
| `links.py` | measures pages, decides if a link is allowed |
| `draft.py` | builds the writing instructions |
| `threads.py` | finds topics for your own posts |
| `limits.py` | the safety rules |
| `post.py` | actually posts, after you approve |
| `report.py` | the numbers |
| `schedule.py` | the daily routine |
| `web.py` | the dashboard |

Everything is stored in one file: `data/agent.db`. You can copy it, back it up,
or delete it to start fresh.

---

## 6. Multiple sites

One install handles many websites. **Each site gets its own Reddit account and
its own separate browser profile.**

That separation is real, not cosmetic — the profiles are different folders on
disk, so site A's login physically cannot appear in site B's session. Daily
limits are counted per site too, so a busy site cannot eat another site's
budget.

---

## 7. Safety limits

| Limit | Default | Why |
|---|---|---|
| Comments per day | 4 | |
| Own threads per day | 1 | |
| Gap between actions | 35 min | |
| Same subreddit again | 20 hours | |
| Own thread, same subreddit | 7 days | mods ban for this pattern |
| Comments before any link | 15 | earn the right to link |
| Max comments carrying links | 25% over 30 days | |

**If a limit blocks you, wait. Do not raise it.** These numbers are the
difference between an aged account and a banned one.

---

## 8. How to run it

Double-click **START.bat**.

```
1. Open dashboard          <- start here
2. Run daily cycle         <- finds threads, writes drafts (~10 min)
3. Show the queue
4. Activity report
5. Site status
7. Connect Reddit account  <- log in, once per site
8. Check which account is logged in
9. Post one reply          <- asks before sending
```

Keep the black window open while you use the dashboard. Closing it stops the
server, and the dashboard will show a red "server not responding" banner.

For the VPS, one line in cron runs it daily:

```
15 9 * * * cd /opt/authority-agent && python3 daily.py >> logs/daily.log 2>&1
```

---

## 9. What it does NOT do

Being straight about the limits:

- **It does not post by itself.** You approve every single one.
- **It does not evade bot detection.** No proxies, no fake fingerprints. It
  uses your real account at human speed. That is not a limitation, it is the
  reason it works.
- **It does not find limo operators specifically.** They are not on Reddit in
  any number. You get small business owners generally, plus domain authority.
  For limo owners specifically you need Facebook operator groups and the NLA.
- **It does not handle your password.** You log in yourself, in a real browser.
- **The dashboard has no login.** Fine on your laptop. On a VPS, put it behind
  an SSH tunnel or a firewall first.

---

## 10. The one thing to remember

The reason this works is not clever automation. It is that the replies are
genuinely useful, and most of them carry no link at all.

The automation only solves the boring part: finding the right thread. The part
that builds authority is being worth reading.
