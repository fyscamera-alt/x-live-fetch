# -*- coding: utf-8 -*-
"""LLM 推文解读 —— 把抓到的推文交给大模型做「内容解读」，输出一篇文章。

    python scripts/interpret.py --input out/nvda/tweets.json
    python scripts/interpret.py --label $NVDA
    python scripts/fetch.py --ticker NVDA --hours 24 --interpret   # 抓完顺手解读

明确不做什么：**不做评分**（不打分、不评级、不输出情绪数值）——
只回答一个问题：「这批推文到底在说什么」。

兼容任意 OpenAI 风格的 /chat/completions 接口（--api-base + --model）：

    DeepSeek  --api-base https://api.deepseek.com               --model deepseek-chat
    Kimi      --api-base https://api.moonshot.cn/v1             --model moonshot-v1-8k
    智谱 GLM   --api-base https://open.bigmodel.cn/api/paas/v4   --model glm-4-flash
    OpenAI    --api-base https://api.openai.com/v1              --model gpt-4o-mini
    本地 Ollama --api-base http://127.0.0.1:11434/v1            --model 任意（--key ollama 占位）

LLM Key 寻找顺序：--key > $X_LLM_API_KEY / $OPENAI_API_KEY > .secrets/llm.key。
费用：按 token 计，与 twitterapi.io 的 credits 无关。
隐私：推文正文会发送给你所选的 LLM 服务商。
代理：默认继承环境变量（HTTPS_PROXY 等）——OpenAI 这类被墙服务需先设好代理；
     DeepSeek / GLM / Ollama 在无代理环境不受影响。
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import xapi  # noqa: E402

LLM_KEY_ENV = ("X_LLM_API_KEY", "LLM_API_KEY", "OPENAI_API_KEY")
LLM_KEY_FILES = (
    os.path.join(".secrets", "llm.key"),
    os.path.join(".secrets", "openai.key"),
    os.path.join(os.path.expanduser("~"), ".llm.key"),
)
DEFAULT_BASE = "https://api.openai.com/v1"
DEFAULT_MODEL = "gpt-4o-mini"

MAX_TWEET_CHARS = 240      # 单条推文进提示词前截断
PROMPT_CHAR_BUDGET = 20000 # 用户消息总预算（约 8k token）
TEMPERATURE = 0.3

KEY_GUIDANCE = (
    "没找到 LLM 的 API Key。本功能需要一个任意 OpenAI 兼容服务的 Key（与 twitterapi.io 无关）：\n"
    '  export X_LLM_API_KEY="你的key"        # 环境变量（也接受 OPENAI_API_KEY）\n'
    '  mkdir -p .secrets && echo "你的key" > .secrets/llm.key   # 密钥文件\n'
    "  python scripts/interpret.py ... --key 你的key            # 临时传参\n"
    "  本地 Ollama 不需要真 Key：--api-base http://127.0.0.1:11434/v1 --key ollama"
)

# --------------------------------------------------------------------------
# 提示词
# --------------------------------------------------------------------------
SYSTEM_ZH = """你是严谨的社媒内容分析师。用户给你一批围绕同一主题的 X（推特）推文，你的任务是**解读内容**，帮读者快速明白「这批人在说什么、谁在说、分歧在哪」。

硬性规则：
- 只做内容解读：**不要打分、不要评级、不要输出任何情绪数值或百分比**。
- 区分「事实陈述」与「观点/预测」；涉及事实性声明时注明「未经核实」。
- 不编造推文里没有的内容；引用原话必须来自给定推文，并注明 @作者。
- 输出 Markdown，直接开始正文，不要开场白。

结构（内容不足的小节可合并或写明「未见」）：
## 总览
4~6 句：这批推文整体在聊什么、氛围如何。
## 主要观点与叙事
3~6 条，每条一句话概括 + 代表性推文（@作者）。
## 分歧与对立
谁与谁意见冲突、争点是什么；没有就写「未见明显分歧」。
## 值得注意的原话
2~4 条最有信息量或互动最高的原话摘录（@作者 + 原句 + 互动数据）。
## 存疑与背景
可疑的事实声明、可能的利益相关（喊单 / 带货 / 拉票等），以及读者应额外查证什么。"""

SYSTEM_EN = """You are a rigorous social-media content analyst. The user gives you a batch of X (Twitter) tweets around one topic. Your job is **content interpretation**: help readers quickly understand what these people are saying, who is saying it, and where they disagree.

Hard rules:
- Interpretation only: **Do not assign scores, ratings, or any sentiment numbers or percentages.**
- Separate factual claims from opinions/predictions; mark factual claims as "unverified".
- Never invent content; quotes must come from the given tweets and be attributed with @author.
- Output Markdown, start with the body directly, no preamble.

Structure (merge or mark sections as "not observed" when content is thin):
## Overview
4-6 sentences: what this batch is about and its tone.
## Main viewpoints and narratives
3-6 items, each a one-line summary + a representative tweet (@author).
## Disagreements
Who conflicts with whom and over what; write "no clear disagreement" if none.
## Notable quotes
2-4 most informative or highest-engagement quotes (@author + text + stats).
## Caveats and context
Suspicious factual claims, possible conflicts of interest (shilling / promotion / astroturfing), and what readers should verify further."""


def engagement(t: dict) -> int:
    return int(t.get("like") or 0) + int(t.get("rt") or 0) * 3 + \
        int(t.get("reply") or 0) + int(t.get("quote") or 0)


def load_tweets(path: str) -> dict:
    """读输入：既支持 fetch.py 的 tweets.json（整体 JSON），也支持逐行 JSONL。

    判定方式：先按整体 JSON 试解析（JSONL 会报 Extra data），失败再逐行解析——
    不能靠首字符 `{` 判断，JSONL 每行也是 `{` 开头。
    """
    try:
        with open(path, encoding="utf-8") as f:
            d = json.load(f)
        if isinstance(d, dict):
            d.setdefault("tweets", [])
            return d
        raise ValueError("输入 JSON 顶层应是对象")
    except json.JSONDecodeError:
        pass
    tweets = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(rec, dict) and isinstance(rec.get("tweet"), dict):
                rec = rec["tweet"]
            tweets.append(rec)
    return {"label": os.path.basename(os.path.dirname(path)),
            "query": "", "fetched_at": "", "tweets": tweets}


def select_tweets(tweets: list[dict], max_n: int = 40) -> list[dict]:
    """挑选进提示词的推文：去重、剔纯转推与空文本，60% 按互动、其余按新鲜度。"""
    seen: dict[str, dict] = {}
    for t in tweets or []:
        tid = str(t.get("id") or "")
        txt = (t.get("text") or "").strip()
        if not tid or not txt or t.get("is_rt"):
            continue
        seen.setdefault(tid, t)
    pool = list(seen.values())
    if not pool:
        return []
    pool.sort(key=engagement, reverse=True)
    n_hot = max(1, int(round(max_n * 0.6)))
    hot = pool[:n_hot]
    hot_ids = {str(t.get("id")) for t in hot}
    rest = sorted((t for t in pool if str(t.get("id")) not in hot_ids),
                  key=lambda t: int(t.get("ts") or 0), reverse=True)
    sel = hot + rest
    return sel[:max_n]


def _clip(s: str, n: int = MAX_TWEET_CHARS) -> str:
    s = " ".join((s or "").split())
    return s if len(s) <= n else s[: n - 1] + "…"


def build_messages(sel: list[dict], meta: dict, lang: str = "zh"):
    system = SYSTEM_ZH if (lang or "zh").lower().startswith("zh") else SYSTEM_EN
    zh = system is SYSTEM_ZH
    head = (
        f"主题：{meta.get('label') or '未命名'}\n"
        f"查询：{meta.get('query') or '（未记录）'}\n"
        f"抓取时间：{meta.get('fetched_at') or '（未记录）'}\n"
    )
    if zh:
        head += f"以下是从 {meta.get('total', len(sel))} 条去重后挑出的 {len(sel)} 条（60% 按互动、其余按新鲜度，纯转推已剔除）：\n\n"
    else:
        head += (f"Selected {len(sel)} of {meta.get('total', len(sel))} deduped tweets "
                 "(60% by engagement, rest by recency; pure retweets excluded):\n\n")
    lines, budget = [], PROMPT_CHAR_BUDGET
    for i, t in enumerate(sel, 1):
        line = (f"[{i}] @{t.get('author') or 'unknown'} · {t.get('time') or '?'} · "
                f"{t.get('like', 0)}like/{t.get('rt', 0)}rt/{t.get('reply', 0)}reply — "
                f"{_clip(t.get('text'))}")
        if budget - len(line) < 0:
            if zh:
                lines.append(f"（受长度限制，其余 {len(sel) - i + 1} 条未纳入）")
            break
        lines.append(line)
        budget -= len(line) + 1
    return system, head + "\n".join(lines)


def extract_content(resp: dict) -> str:
    """从 /chat/completions 响应里取正文，剥掉可能的 ``` 围栏。"""
    content = ""
    try:
        content = (resp["choices"][0]["message"]["content"] or "").strip()
    except (KeyError, IndexError, TypeError):
        raise ValueError("响应缺少 choices[0].message.content —— 检查 --model 与 --api-base 是否匹配")
    content = content.strip()
    if content.startswith("```"):
        parts = content.split("\n", 1)
        if len(parts) == 2:
            content = parts[1]
        if content.rstrip().endswith("```"):
            content = content.rstrip()[:-3]
    return content.strip()


# --------------------------------------------------------------------------
# HTTP
# --------------------------------------------------------------------------
def call_llm(base: str, key: str, model: str, system: str, user: str,
             proxy: str | None = None, timeout: int = 120,
             transport=None) -> tuple[str, str]:
    """调 /chat/completions，返回 (正文, 实际使用的 base)。

    transport 仅供离线自检注入；生产走 urllib。
    代理策略：显式 --proxy > 环境变量（urllib 默认行为，OpenAI 等被墙服务依赖它）。
    """
    url = base.rstrip("/") + "/chat/completions"
    payload = {
        "model": model,
        "temperature": TEMPERATURE,
        "messages": [{"role": "system", "content": system},
                     {"role": "user", "content": user}],
    }
    headers = {"Authorization": "Bearer " + key,
               "Content-Type": "application/json"}
    if transport is not None:
        status, body = transport(payload, headers)
        if status != 200:
            raise RuntimeError(f"LLM 服务返回 HTTP {status}: {body[:200]}")
        return extract_content(json.loads(body)), base

    if proxy:
        opener = urllib.request.build_opener(
            urllib.request.ProxyHandler({"http": proxy, "https": proxy}))
    else:
        opener = urllib.request.build_opener()  # 继承环境代理变量
    data = json.dumps(payload).encode("utf-8")
    last_err = None
    for attempt in range(2):
        req = urllib.request.Request(url, data=data, headers=headers, method="POST")
        try:
            with opener.open(req, timeout=timeout) as r:
                d = json.loads(r.read().decode("utf-8", "replace") or "{}")
            return extract_content(d), base
        except urllib.error.HTTPError as e:
            detail = ""
            try:
                detail = e.read().decode("utf-8", "replace")[:300]
            except Exception:
                pass
            if e.code in (401, 403):
                raise RuntimeError(
                    f"LLM Key 被拒绝（HTTP {e.code}）。检查 Key 与 --api-base 是否同一服务商。{detail}")
            last_err = f"HTTP {e.code} {detail}"
        except urllib.error.URLError as e:
            last_err = f"{type(e).__name__}: {e.reason}"
        except RuntimeError:
            raise
        except Exception as e:  # noqa: BLE001
            last_err = f"{type(e).__name__}: {e}"
        if attempt < 1:
            time.sleep(2)
    raise RuntimeError(f"LLM 请求失败 —— {last_err}")


# --------------------------------------------------------------------------
# 产物
# --------------------------------------------------------------------------
def render(input_path: str, out_label: str, model: str, base: str,
           sel: list[dict], total: int, content: str) -> str:
    host = urllib.parse.urlparse(base).hostname or base
    L = [
        f"# LLM 解读 · {out_label}",
        "",
        f"- **生成时间**：{time.strftime('%Y-%m-%d %H:%M:%S')}",
        f"- **模型**：{model} @ {host}",
        f"- **输入**：`{input_path}`（送入 {len(sel)} / 共 {total} 条；纯转推已剔除）",
        "- **性质**：内容解读，非投资建议；模型可能出错，引用请回看原推。",
        "",
        "---",
        "",
        content,
        "",
    ]
    return "\n".join(L)


def find_llm_key(cli_key: str | None = None) -> str:
    if cli_key and cli_key.strip():
        return cli_key.strip()
    for name in LLM_KEY_ENV:
        v = (os.environ.get(name) or "").strip()
        if v:
            return v
    for path in LLM_KEY_FILES:
        try:
            with open(path, encoding="utf-8") as f:
                for line in f:
                    s = line.strip()
                    if s and not s.startswith("#"):
                        return s
        except OSError:
            continue
    return ""


def run(input_path: str, *, key: str | None = None, model: str | None = None,
        api_base: str | None = None, lang: str = "zh", max_tweets: int = 40,
        proxy: str | None = None, timeout: int = 120, quiet: bool = False,
        out: str | None = None, transport=None) -> tuple[bool, str]:
    """给 fetch.py 等调用方用的入口：不抛异常，返回 (是否成功, 说明/原因)。"""
    k = find_llm_key(key)
    if not k:
        return False, "没找到 LLM API Key —— " + KEY_GUIDANCE.splitlines()[0] + "（详见 scripts/interpret.py --help）"
    if not os.path.isfile(input_path):
        return False, f"输入文件不存在：{input_path}"
    try:
        meta = load_tweets(input_path)
    except Exception as e:  # noqa: BLE001
        return False, f"输入解析失败：{type(e).__name__}: {e}"

    sel = select_tweets(meta.get("tweets") or [], max_tweets)
    if not sel:
        return False, "输入里没有可解读的推文（全是转推/空文本）"
    meta["total"] = len(meta.get("tweets") or [])
    meta.setdefault("label", os.path.basename(os.path.dirname(input_path)))

    system, user = build_messages(sel, meta, lang)
    base = api_base or os.environ.get("X_LLM_BASE") or os.environ.get("OPENAI_BASE_URL") or DEFAULT_BASE
    mdl = model or os.environ.get("X_LLM_MODEL") or DEFAULT_MODEL
    try:
        content, used_base = call_llm(base, k, mdl, system, user,
                                      proxy=proxy, timeout=timeout,
                                      transport=transport)
    except RuntimeError as e:
        return False, str(e)

    out_label = meta.get("label") or "解读"
    out_path = out or os.path.join(os.path.dirname(input_path), "interpretation.md")
    md = render(input_path, out_label, mdl, used_base, sel, meta["total"], content)
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(md)
    if not quiet:
        print(md)
    return True, f"解读完成 → {out_path}（模型 {mdl}，送入 {len(sel)}/{meta['total']} 条）"


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------
def main(argv=None):
    xapi.setup_stdout()
    p = argparse.ArgumentParser(
        description="把抓到的推文交给 LLM 做内容解读（不打分、只解读）",
        epilog="Key 配置与可用服务商见文件头注释；抓完顺手解读：python scripts/fetch.py ... --interpret",
    )
    p.add_argument("--input", help="tweets.json / 逐行 JSONL 路径（默认用 --label 拼 out/<label>/tweets.json）")
    p.add_argument("--label", help="查询标签（对应 out/<label>/ 目录名）")
    p.add_argument("--out-root", default="out", help="输出根目录，默认 out/")
    p.add_argument("--out", help="解读 md 的输出路径（默认与输入同目录 interpretation.md）")
    p.add_argument("--lang", default="zh", help="解读输出语言：zh（默认）/ en")
    p.add_argument("--max-tweets", type=int, default=40, help="最多送入多少条，默认 40")
    p.add_argument("--api-base", help=f"OpenAI 兼容接口地址，默认 {DEFAULT_BASE}（也可 $X_LLM_BASE）")
    p.add_argument("--model", help=f"模型名，默认 {DEFAULT_MODEL}（也可 $X_LLM_MODEL）")
    p.add_argument("--key", help="LLM Key（也可 $X_LLM_API_KEY / $OPENAI_API_KEY / .secrets/llm.key）")
    p.add_argument("--proxy", help="显式代理（默认继承环境变量；DeepSeek/GLM 无需代理）")
    p.add_argument("--timeout", type=int, default=120, help="请求超时秒数，默认 120")
    p.add_argument("--quiet", action="store_true", help="只写文件，不打印全文")
    a = p.parse_args(argv)

    input_path = a.input
    if not input_path and a.label:
        input_path = os.path.join(a.out_root, xapi.slug(a.label), "tweets.json")
    if not input_path:
        p.error("至少给 --input 或 --label")

    k = find_llm_key(a.key)
    if not k:
        print("✗ " + KEY_GUIDANCE, file=sys.stderr)
        raise SystemExit(2)

    ok, msg = run(input_path, key=k, model=a.model, api_base=a.api_base,
                  lang=a.lang, max_tweets=a.max_tweets, proxy=a.proxy,
                  timeout=a.timeout, quiet=a.quiet, out=a.out)
    print(("✓ " if ok else "✗ ") + msg, flush=True)
    if ok and not a.quiet:
        print("\n想边抓边看？python scripts/dashboard.py → http://127.0.0.1:8765")
    if not ok:
        raise SystemExit(2)


if __name__ == "__main__":
    main()
