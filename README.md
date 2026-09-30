# x-live-fetch

[![CI](https://github.com/fyscamera-alt/x-live-fetch/actions/workflows/ci.yml/badge.svg)](https://github.com/fyscamera-alt/x-live-fetch/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Python](https://img.shields.io/badge/python-3.8%2B-blue.svg)](https://www.python.org/)

English | [简体中文](README.zh-CN.md)

**Give it a keyword or a stock ticker, get real-time X (Twitter) content.**

A plug-and-play AI Agent Skill. Zero third-party dependencies (pure Python standard library),
zero config (one API key), no Twitter developer account, no approval wait.

```
User:  What is X saying about $NVDA right now?
Agent: → python scripts/fetch.py --ticker NVDA --hours 24
       → 40 hits · Top 15 by engagement · timeline · author ranking (digest.md + tweets.json)
```

---

## Why use it

- **Two modes**: on-demand search (REST) or true second-level streaming (WebSocket) — pick per scenario.
- **Built-in monitoring dashboard**: one command starts a local web page with full text, timestamps,
  likes / reposts / replies / quotes / views and author followers in real time; newly arrived tweets
  flash-highlight. Read-only, listens on localhost only, zero dependencies.
- **Cheap**: $0.15 / 1,000 tweets, free credit on signup, no monthly fee. Built-in cost discipline
  avoids every common "where did my balance go" trap.
- **Output you can use directly**: not just raw JSON — a Markdown digest with top-engagement tweets,
  timeline and author ranking.
- **Hard-won pitfalls baked in**: silently ignored time windows, WebSocket pings that require a
  masked pong, rule updates that reset the interval to the priciest tier when a field is omitted…
  all battle-tested and handled for you.

---

## What data you get

Every tweet is a full structured record (one element in `tweets.json`) — **not just the text**:

| Category | Fields |
|---|---|
| **Text** | `text` full text (newlines / emoji / links preserved) |
| **Timestamps** | `time` local-timezone readable (`2026-09-26 22:22`) · `created_at` raw UTC string · `ts` epoch ms |
| **Engagement** | **`like` · `rt` (repost) · `reply` · `quote` · `view` (impressions)** |
| **Author** | `author` handle · `author_name` display name · `followers` follower count |
| **Other** | `id` tweet ID · `url` canonical link · `lang` · `is_reply` / `is_rt` / `is_quote` flags |

The `digest.md` summary looks like this:

```markdown
## 🔥 Top engagement

### 1. @TrendSpider · 2.4k👍 412🔁 486.0k👁

> $NVDA Nvidia earnings preview: data center revenue seen at $41.2B, up 52% YoY.
> Whisper number above guidance. Watching 175 level closely.

`2026-09-26 22:22` · 312,400 followers · [source](https://x.com/TrendSpider/status/…)

## 🕐 Timeline (new → old)

- `09-26 22:22` **@TrendSpider** (312.4k) 2.4k👍 — $NVDA Nvidia earnings preview: data center
  revenue seen at $41.2B, up 52% YoY. [↗](https://x.com/TrendSpider/status/…)
- `09-26 19:40` **@retail_dad** (2.2k) 57👍 — 我的 $NVDA 仓位已经拿了两年，今天又加了一点。
  [↗](https://x.com/retail_dad/status/…) · repost

## 👥 Most active accounts

| Account | Tweets | Followers | Total likes |
|---|---:|---:|---:|
| @TrendSpider | 1 | 312.4k | 2.4k |
| @quant_flow | 1 | 142.0k | 1.1k |
```

**More in the raw responses**: `raw_pageN.json` keeps the API response verbatim — author bio /
`location` / `entities` hashtags / `conversationId` / `inReplyToUsername`, plus **the complete
nested object of the quoted or retweeted tweet** (with its own text and engagement) —
**re-process with different criteria without paying to fetch again**.

> ⚠️ One real difference: the WebSocket `fast_tweet` fast lane (authors with 5,000+ followers,
> sub-second) **only carries text and time — no engagement data**. For engagement-based ranking,
> use on-demand search or rule streaming.

---

## 30-second quick start

> Commands assume **the current directory is the skill root** (where `SKILL.md` lives).
> Run `cd` there first if you're elsewhere.

### 1. Get an API key

👉 **https://twitterapi.io?ref=fysc666**

Sign up (no credit card) → copy the API key from the dashboard home.
**New accounts get $0.1 in free credit** (= 10,000 credits ≈ 660 tweets) —
a query for a few hundred tweets costs tens of credits, enough to exercise this skill end to end.

### 2. Hand the key to the scripts

```bash
mkdir -p .secrets && echo "your-key" > .secrets/x-api.key
```

Or set the env var `export TWITTERAPI_IO_KEY="your-key"`, or pass `--key your-key` per command.
Lookup order: CLI flag > env var > key file.

### 3. Self-test + first run

```bash
python scripts/xapi.py balance                               # a new account shows 10,000 credits ≈ $0.1000
python scripts/fetch.py --ticker NVDA --hours 6 --pages 1     # first run, costs less than one cent
```

When it prints the digest and `out/nvda/digest.md` appears, you're done. Then vary the arguments:

```bash
python scripts/fetch.py --ticker NVDA --hours 24
python scripts/fetch.py "ai agents" --hours 12 --pages 2
```

---

## Two modes

### Mode A · On-demand search

```bash
python scripts/fetch.py --ticker NVDA --hours 24            # stock ticker (cashtag)
python scripts/fetch.py "openai" --hours 12 --pages 3        # keyword
python scripts/fetch.py --ticker TSLA --from elonmusk --lang en --min-likes 100
```

Output in `out/<query>/`: `digest.md` (human-readable) · `tweets.json` (deduplicated, structured) ·
`raw_pageN.json` (verbatim API responses).

### Mode B · Real-time streaming (WebSocket)

```bash
python scripts/rules.py create --tag nvda --value '$NVDA' --interval 300   # create rule (free)
python scripts/rules.py on --tag nvda                                      # activate (billing starts)
python scripts/stream.py --tag nvda --sec 600                              # receive for 10 minutes
python scripts/rules.py off --tag nvda                                     # deactivate when done
```

`stream.py --loop` is the resident mode with automatic backoff and reconnect.
**Run residents under your own system** (Task Scheduler / systemd / Docker / supervisor) —
not inside an agent's one-shot command.

Output: `out/x_live.jsonl` (one tweet per line, same fields as `tweets.json` plus `received_at`,
ready for downstream analysis).

---

## LLM interpretation (optional, no scoring)

Hand the fetched tweets to an LLM for a **content interpretation** — what this batch of tweets
is actually saying, who says it, where they disagree. Deliberately **no scores, no ratings, no
sentiment numbers** — just a structured write-up: overview / main narratives / disagreements /
notable quotes (attributed) / caveats (factual claims marked "unverified").

```bash
python scripts/interpret.py --input out/nvda/tweets.json       # interpret an existing fetch
python scripts/fetch.py --ticker NVDA --hours 24 --interpret   # fetch, then interpret in one go
```

Works with any OpenAI-compatible endpoint via `--api-base` + `--model`
(DeepSeek / Kimi / GLM / OpenAI / local Ollama). LLM key is separate from the twitterapi.io key
(`$X_LLM_API_KEY`, `.secrets/llm.key`, or `--key`). Token-billed by your LLM provider — a DeepSeek
run typically costs well under one cent. Output: `out/<query>/interpretation.md`.
Privacy note: tweet text is sent to whichever LLM provider you choose.

---

## Monitoring dashboard

Don't want to stare at Markdown in a terminal? One command starts a local web dashboard:

```bash
python scripts/dashboard.py            # → open http://127.0.0.1:8765
python scripts/dashboard.py --open     # also opens the browser for you
```

The left panel lists every fetch under `out/` (mode B's "⚡ live stream" included); the right panel
shows tweet cards: full text (folded past 600 chars), local time,
**👍 likes / 🔁 reposts / 💬 replies / 🔗 quotes / 👁 views**, author handle and follower count,
source link, rule tag and type flags. Polls for new tweets every 3 seconds and
**flash-highlights fresh arrivals** — effectively a live monitoring wall while mode B runs.

| | |
|---|---|
| Read-only | never writes or deletes anything; only reads `out/` |
| Localhost only | binds `127.0.0.1` by default, unreachable from LAN (`--host 0.0.0.0` exposes it — careful) |
| Path validation | query names go through a whitelist; `../`-style requests get a 400 |
| Zero deps | pure standard library, nothing to install |

> ⚠️ This is a resident service — run it **in your own terminal** (it stops when the window closes).
> A resident process launched from an agent's sandbox may be reaped as soon as the command ends.

---

## Install for your agent

This repo is a standard Skill: `SKILL.md` at the root with `name` + `description` frontmatter,
readable out of the box by Skills-aware agents (Claude Code / Cursor / Codex / Copilot /
Gemini CLI / WorkBuddy, etc.).

```bash
# Option 1: skills CLI (after publishing to GitHub)
npx skills add <your-name>/x-live-fetch

# Option 2: copy into your agent's skills directory
cp -r x-live-fetch ~/.claude/skills/      # Claude Code
```

Then just ask your agent "what is X saying about X right now" — it will read `SKILL.md` itself.

### WorkBuddy

Drop it into **`~/.workbuddy/skills/x-live-fetch/`** (user level, available in all projects).
Put it under a project's `.workbuddy/skills/` to scope it to that project only.

---

## Pricing

| Item | Price |
|---|---|
| Tweets | $0.15 / 1,000 (1 tweet = 15 credits) |
| User profiles | $0.18 / 1,000 |
| Follower IDs (bulk) | $0.0045 / 1,000 |
| Minimum charge per request | 15 credits (even for 0 results) |

**You pay for tweets retrieved, not for calls made.** A typical one-ticker one-day lookup costs
a few cents. Cost only accumulates when a rule stays active with the WebSocket open, so
**run `rules.py off` when you're done**.

Your balance **can go negative** (in arrears) — that's not an error, it genuinely means top-up time;
the API returns `HTTP 402 Credits is not enough`. Check with `python scripts/xapi.py balance` first.

---

## Layout

```
x-live-fetch/
├── SKILL.md                    # agent entry point (frontmatter: name + description + platform fields)
├── references/
│   └── api-reference.md        # twitterapi.io full API contract cheat sheet
├── scripts/
│   ├── xapi.py                 # shared client: key lookup / direct requests / balance / WS handshake
│   ├── fetch.py                # mode A: on-demand search + digest
│   ├── rules.py                # filter-rule management (CRUD / bulk on-off)
│   ├── stream.py               # mode B: WebSocket consumer
│   ├── interpret.py            # LLM content interpretation (no scoring, bring your own LLM key)
│   ├── dashboard.py            # monitoring dashboard (local read-only server)
│   └── selftest.py             # offline self-test
├── README.md / README.zh-CN.md
├── LICENSE
└── out/                        # runtime output (gitignored)
    ├── <query>/digest.md        #   human-readable digest
    ├── <query>/tweets.json     #   normalized structured tweets
    ├── <query>/interpretation.md # LLM content interpretation (after interpret.py)
    ├── <query>/raw_pageN.json  #   verbatim API responses (all fields)
    └── x_live.jsonl            #   mode B live stream
```

Requires **Python 3.8+**, no third-party dependencies.

---

## Development / self-test

```bash
python scripts/selftest.py
```

**No API key, no network, no cost.** 141 assertions cover query construction (time windows must
live in the query string), the fetch pipeline (dedup / sort / render / empty-result branches),
**the field contract (the table above is checked against the code line by line)**,
the LLM interpretation (tweet selection / no-scoring prompt / response parsing / output),
the dashboard (data assembly / HTTP endpoints / path-traversal protection / read-only guarantee,
against a real server over real requests), WebSocket frame codec (masking / coalescing /
fragment reassembly) and all four event types, plus `--tag` filter semantics.
Run it after any change; CI runs it too.

---

## Known pitfalls (already handled in code)

- Time windows `since_time/until_time` **must be inside the query string**; passing them as URL
  params gets them silently ignored.
- Stacking multiple `OR` phrases into one query **collapses recall to 0** — go broad in the query,
  filter on the client.
- Rule updates **must pass every field**; omitting `interval_seconds` resets it to the priciest tier.
- Deleting a rule requires HTTP **DELETE** (POST returns 405).
- One WebSocket connection **per key**; after a disconnect **wait at least 90 seconds** to reconnect.
- Client pong frames **must be masked** (RFC6455), or the session dies in 20 seconds.
- Tweet IDs are 64-bit snowflakes — **treat them as strings**, never as ints.
- Direct connection is the fastest default; proxies tend to 403, and WS can be blocked by Cloudflare.

---

## Notes

- This project is an **unofficial client wrapper for twitterapi.io**, not affiliated with the
  service, and not responsible for its availability or billing policies.
- Follow twitterapi.io's terms of service and X's platform rules; use this project for lawful
  purposes only.

## License

MIT
