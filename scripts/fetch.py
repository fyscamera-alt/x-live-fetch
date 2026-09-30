# -*- coding: utf-8 -*-
"""按关键词或股票代码，抓 X 上最新的推文 → Markdown 摘要 + JSON 落盘。

    python scripts/fetch.py --ticker NVDA --hours 24
    python scripts/fetch.py "ai agents" --hours 12 --pages 2
    python scripts/fetch.py --ticker TSLA --from elonmusk,WholeMarsBlog --min-likes 100

产物（默认写到 out/<查询>/）：
    digest.md      人看的摘要
    tweets.json    去重、按时间倒序的结构化数据
    raw_pageN.json 原始响应（改口径重分析时不用重新花钱）

计费：按**抓回来的条数**计费，1 条 = 15 credits ≈ $0.00015；每次调用最低扣 15 credits。
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import xapi  # noqa: E402


# --------------------------------------------------------------------------
# 查询构造
# --------------------------------------------------------------------------
def base_label(a) -> str:
    """给人看的短标签（不含时间窗等附属条件）。"""
    if a.ticker:
        return "$" + a.ticker.lstrip("$").strip().upper()
    if a.query:
        return a.query
    return " ".join(a.words)


def build_query(a) -> str:
    """把参数拼成 advanced_search 的 query。

    注意：时间窗必须写在 query **字符串里**（since_time/until_time），
    当成 URL 参数传会被平台静默忽略，直接给你最新 20 条看起来却很正常。
    """
    parts = [base_label(a).strip()]

    if a.from_users:
        users = [u.strip().lstrip("@") for u in a.from_users.split(",") if u.strip()]
        if users:
            parts.append("(" + " OR ".join("from:" + u for u in users) + ")")
    if a.lang:
        parts.append("lang:" + a.lang.strip())
    if a.min_likes:
        parts.append("min_faves:%d" % a.min_likes)

    hours = a.hours if a.days is None else a.days * 24
    if hours and hours > 0:
        now = int(time.time())
        parts.append("since_time:%d until_time:%d" % (now - int(hours * 3600), now))

    return " ".join(p for p in parts if p)


def normalize(tw: dict) -> dict:
    a = tw.get("author") or {}
    return {
        "id": str(tw.get("id") or ""),
        "url": tw.get("url") or "",
        "time": xapi.human_time(tw),
        "created_at": tw.get("createdAt") or "",
        "ts": xapi.epoch_ms(tw),
        "text": (tw.get("text") or "").strip(),
        "author": a.get("userName") or "",
        "author_name": a.get("name") or "",
        "followers": a.get("followers") or 0,
        "like": tw.get("likeCount") or 0,
        "rt": tw.get("retweetCount") or 0,
        "reply": tw.get("replyCount") or 0,
        "quote": tw.get("quoteCount") or 0,
        "view": tw.get("viewCount") or 0,
        "lang": tw.get("lang") or "",
        "is_reply": bool(tw.get("isReply")),
        "is_rt": bool(tw.get("retweeted_tweet")),
        "is_quote": bool(tw.get("quoted_tweet")),
        "rule_tag": tw.get("rule_tag") or "",
    }


# --------------------------------------------------------------------------
# 抓取
# --------------------------------------------------------------------------
def fetch(query: str, pages: int, limit: int, key: str, proxy: str | None):
    seen: dict[str, dict] = {}
    raw_pages = []
    cursor = ""
    for page in range(max(1, pages)):
        d = xapi.request(
            "/twitter/tweet/advanced_search",
            params={"query": query, "queryType": "Latest", "cursor": cursor},
            key=key,
            proxy=proxy,
        )
        raw_pages.append(d)

        for tw in d.get("tweets") or []:
            rec = normalize(tw)
            if rec["id"]:
                seen.setdefault(rec["id"], rec)

        if limit and len(seen) >= limit:
            break
        if not d.get("has_next_page"):
            break
        cursor = d.get("next_cursor") or ""
        if not cursor:
            break
        time.sleep(0.4)

    tweets = sorted(seen.values(), key=lambda t: t["ts"], reverse=True)
    if limit:
        tweets = tweets[:limit]
    return tweets, raw_pages


# --------------------------------------------------------------------------
# 渲染
# --------------------------------------------------------------------------
def render_digest(label: str, query: str, tweets: list[dict], pages_used: int,
                  balance: int | None, args) -> str:
    hours = args.hours if args.days is None else args.days * 24
    window = f"最近 {hours:g} 小时" if hours else "最新（不限时间窗）"
    credits = max(len(tweets) * xapi.CREDITS_PER_TWEET, xapi.MIN_CREDITS_PER_CALL)

    L = [
        f"# X 实时内容 · {label}",
        "",
        f"- **抓取时间**：{time.strftime('%Y-%m-%d %H:%M:%S')}",
        f"- **时间窗**：{window}",
        f"- **查询表达式**：`{query}`",
        f"- **命中**：{len(tweets)} 条（去重后）· 翻页 {pages_used} 页",
        f"- **本次预估消耗**：{credits:,} credits ≈ {xapi.usd(credits)}",
    ]
    if balance is not None:
        L.append(f"- **账户余额**：{xapi.fmt_balance(balance)}")
    L.append("")

    if not tweets:
        L += [
            "> 没有抓到任何推文。**先别下「没人讨论」的结论** —— 换个更短的词复验一次：",
            "> 把多个 OR 词组叠进一个 query，召回率会塌到接近 0。用 1~3 个简单词即可。",
            "",
        ]
        return "\n".join(L)

    # —— 高互动 Top ——
    hot = sorted(tweets, key=lambda t: (t["like"] + t["rt"] * 3), reverse=True)[:15]
    L += ["## 🔥 高互动 Top", ""]
    for i, t in enumerate(hot, 1):
        L += [
            f"### {i}. @{t['author']} · {xapi.num(t['like'])}👍 {xapi.num(t['rt'])}🔁 "
            f"{xapi.num(t['view'])}👁",
            "",
            f"> {xapi.shorten(t['text'], 600)}",
            "",
            f"`{t['time']}` · 粉丝 {t['followers']:,} · [原推]({t['url']})",
            "",
        ]

    # —— 时间线 ——
    L += ["## 🕐 时间线（新 → 旧）", ""]
    for t in tweets:
        flags = []
        if t["is_rt"]:
            flags.append("转推")
        if t["is_quote"]:
            flags.append("引用")
        if t["is_reply"]:
            flags.append("回复")
        tail = (" · " + "/".join(flags)) if flags else ""
        L.append(
            f"- `{t['time'][5:]}` **@{t['author']}** ({xapi.num(t['followers'])}) "
            f"{xapi.num(t['like'])}👍 — {xapi.shorten(t['text'], 180)}"
            f" [↗]({t['url']}){tail}"
        )
    L.append("")

    # —— 发言账号排行 ——
    by_author: dict[str, dict] = {}
    for t in tweets:
        a = by_author.setdefault(
            t["author"], {"n": 0, "followers": t["followers"], "like": 0}
        )
        a["n"] += 1
        a["like"] += t["like"]
    top = sorted(by_author.items(), key=lambda kv: kv[1]["n"], reverse=True)[:20]
    L += ["## 👥 发言账号 Top", "", "| 账号 | 条数 | 粉丝 | 累计赞 |", "|---|---:|---:|---:|"]
    for name, a in top:
        L.append(f"| @{name} | {a['n']} | {xapi.num(a['followers'])} | {xapi.num(a['like'])} |")
    L.append("")
    return "\n".join(L)


# --------------------------------------------------------------------------
# main
# --------------------------------------------------------------------------
def main():
    xapi.setup_stdout()
    p = argparse.ArgumentParser(
        description="按关键词 / 股票代码抓取 X 上最新的推文",
        epilog=f"还没有 API Key？去 {xapi.SIGNUP_URL} 注册。",
    )
    p.add_argument("words", nargs="*", help="关键词（多个词会空格拼接）")
    p.add_argument("--query", help="直接给完整 query（可用 from: / lang: 等高级语法）")
    p.add_argument("--ticker", help="股票代码，自动按 cashtag 搜（$NVDA）")
    p.add_argument("--hours", type=float, default=24, help="时间窗小时数，默认 24；0 = 不限")
    p.add_argument("--days", type=float, default=None, help="时间窗天数（等价于 --hours 24*N）")
    p.add_argument("--pages", type=int, default=1, help="翻页数，每页最多 20 条，默认 1")
    p.add_argument("--limit", type=int, default=0, help="最多保留多少条，默认不限")
    p.add_argument("--from", dest="from_users", help="只看这些账号，逗号分隔")
    p.add_argument("--lang", help="语言过滤，如 en / zh / ja")
    p.add_argument("--min-likes", type=int, default=0, help="最少点赞数（⚠️ 找询价/求购类会全灭）")
    p.add_argument("--no-rt", action="store_true", help="剔除纯转推")
    p.add_argument("--out", default="out", help="输出根目录，默认 out/")
    p.add_argument("--key", help="API Key（也可用环境变量或密钥文件）")
    p.add_argument("--proxy", help="显式代理，如 http://127.0.0.1:7890（默认直连）")
    p.add_argument("--quiet", action="store_true", help="只写文件，不打印摘要")
    p.add_argument("--interpret", action="store_true",
                   help="抓完顺手让 LLM 解读内容（需 OpenAI 兼容服务的 Key，见 scripts/interpret.py --help）")
    a = p.parse_args()

    if not (a.ticker or a.query or a.words):
        p.error("至少给一个关键词 / --ticker / --query")

    key = xapi.load_key(a.key)
    label = base_label(a)
    query = build_query(a)

    tweets, raw_pages = fetch(query, a.pages, a.limit, key, a.proxy)
    if a.no_rt:
        tweets = [t for t in tweets if not t["is_rt"]]

    outdir = xapi.ensure_dir(os.path.join(a.out, xapi.slug(label)))
    for i, d in enumerate(raw_pages, 1):
        with open(os.path.join(outdir, f"raw_page{i}.json"), "w", encoding="utf-8") as f:
            json.dump(d, f, ensure_ascii=False, indent=1)
    with open(os.path.join(outdir, "tweets.json"), "w", encoding="utf-8") as f:
        json.dump(
            {"label": label, "query": query,
             "fetched_at": time.strftime("%Y-%m-%d %H:%M:%S"),
             "count": len(tweets), "tweets": tweets},
            f,
            ensure_ascii=False,
            indent=1,
        )

    balance = xapi.get_balance(key, a.proxy)
    digest = render_digest(label, query, tweets, len(raw_pages), balance, a)
    with open(os.path.join(outdir, "digest.md"), "w", encoding="utf-8") as f:
        f.write(digest)

    if not a.quiet:
        print(digest)
    print(f"\n---\n产物目录: {outdir}/  (digest.md / tweets.json / raw_page*.json)")
    if a.interpret:
        try:
            import interpret  # noqa: E402

            ok, msg = interpret.run(os.path.join(outdir, "tweets.json"), quiet=a.quiet)
            print(("LLM 解读 · " if ok else "跳过 LLM 解读 · ") + msg, flush=True)
        except Exception as e:  # noqa: BLE001
            print(f"跳过 LLM 解读 · {type(e).__name__}: {e}", flush=True)
    if not a.quiet:
        print(
            "想看实时画面？另开一个终端跑  python scripts/dashboard.py\n"
            "  然后浏览器打开 http://127.0.0.1:8765 —— 抓到的每条推文都会在页面上显示，可以边抓边看。"
        )


if __name__ == "__main__":
    main()
