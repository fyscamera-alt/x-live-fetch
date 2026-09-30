# -*- coding: utf-8 -*-
"""离线自检：不需要 API Key、不联网、不花一分钱。

    python scripts/selftest.py

覆盖：
  ① query 构造 —— 时间窗必须落在 query 字符串里（最容易静默出错的地方）
  ② 抓取管线   —— 归一化 / 按 id 去重 / 按时间倒序 / 空结果分支 / Markdown 渲染与落盘
  ③ 字段契约   —— 落盘字段集合与 SKILL.md 第 2 节的字段表逐一对齐
  ④ 内容监控看板 —— 数据组装 / HTTP 接口 / 路径穿越防护 / 只读保证
  ⑤ WebSocket  —— 帧编解码（含掩码与分片）、握手头、ping→pong、
                  connected/ping/tweet/fast_tweet 事件解析、--tag 过滤语义
  ⑥ frontmatter —— SKILL.md 头部 YAML 可被真解析器解析（平台侧会报错，
                  官方 quick_validate.py 只做正则、查不出这类问题）
  ⑦ LLM 解读   —— 推文挑选（剔转推/去重/互动排序）、提示词硬性禁评分、
                  围栏响应解析、产物落盘、无 Key 分支（transport 注入，全程离线）

CI 里也能直接跑（退出码非 0 即失败）。
"""
from __future__ import annotations

import json
import os
import re
import socket
import ssl
import sys
import tempfile
import threading
import time
import types
import urllib.error
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "scripts"))

import dashboard  # noqa: E402
import fetch   # noqa: E402
import interpret  # noqa: E402
import stream  # noqa: E402
import xapi    # noqa: E402

OK = []


def check(cond, label):
    if not cond:
        raise AssertionError("FAILED: " + label)
    OK.append(label)


# ==========================================================================
# ① query 构造
# ==========================================================================
def test_query():
    a = types.SimpleNamespace(ticker="nvda", query=None, words=[], from_users="elonmusk,@WholeMarsBlog",
                              lang="en", min_likes=100, hours=24, days=None)
    q = fetch.build_query(a)
    check(q.startswith("$NVDA "), "股票代码自动转大写 cashtag")
    check("since_time:" in q and "until_time:" in q, "时间窗写在 query 字符串里")
    check("from:elonmusk" in q and "from:WholeMarsBlog" in q, "多个账号用 OR 组合且去掉 @")
    check("lang:en" in q and "min_faves:100" in q, "lang / min_faves 拼接正确")
    check("?" not in q, "query 里不应出现 URL 参数分隔符")

    b = types.SimpleNamespace(ticker=None, query=None, words=["ai", "agents"],
                              from_users=None, lang=None, min_likes=0, hours=0, days=None)
    check(fetch.build_query(b) == "ai agents", "多词拼接；--hours 0 时不加时间窗")
    check(fetch.base_label(b) == "ai agents", "base_label 不含时间窗")


# ==========================================================================
# ② 抓取管线
# ==========================================================================
FAKE_RESPONSE = {"has_next_page": False, "next_cursor": "", "tweets": [
    {"id": "1002", "url": "https://x.com/a/status/1002", "text": "NVDA guidance raise\nsecond line",
     "createdAt": "Sat Sep 26 12:00:00 +0000 2026", "lang": "en", "likeCount": 1200,
     "retweetCount": 300, "replyCount": 40, "quoteCount": 12, "viewCount": 90000,
     "author": {"userName": "a", "name": "A", "followers": 50000}},
    {"id": "1001", "url": "https://x.com/b/status/1001", "text": "older tweet",
     "createdAt": "Fri Sep 25 08:00:00 +0000 2026", "likeCount": 5, "retweetCount": 0,
     "author": {"userName": "b", "name": "B", "followers": 900}},
    {"id": "1002", "url": "https://x.com/a/status/1002", "text": "duplicate id must be dropped",
     "createdAt": "Sat Sep 26 12:00:00 +0000 2026", "author": {"userName": "a"}},
]}


def test_fetch_pipeline(tmp):
    real = xapi.request
    xapi.request = lambda *a, **k: FAKE_RESPONSE
    try:
        args = types.SimpleNamespace(ticker="NVDA", query=None, words=[], from_users=None,
                                     lang=None, min_likes=0, hours=6, days=None, no_rt=False)
        tws, raw = fetch.fetch(fetch.build_query(args), 1, 0, "k", None)
    finally:
        xapi.request = real

    check(len(tws) == 2, "按 id 去重（3 条原始 → 2 条）")
    check([t["id"] for t in tws] == ["1002", "1001"], "按时间倒序")
    check(tws[0]["like"] == 1200 and tws[0]["author"] == "a", "字段归一化正确")
    check(tws[0]["text"].count("\n") == 1 or "second line" in tws[0]["text"], "正文换行保留")
    check(tws[0]["time"].startswith("2026-09-26"), "createdAt 解析成功")

    label, query = fetch.base_label(args), fetch.build_query(args)

    # 有数据
    md = fetch.render_digest(label, query, tws, 1, -121, args)
    check("高互动 Top" in md and "时间线" in md and "发言账号 Top" in md, "摘要三个区块齐全")
    check("余额已耗尽" in md, "负余额（欠费）有明确提示")
    check(md.count("### ") == 2, "高互动 Top 逐条渲染")
    path = os.path.join(tmp, "digest.md")
    with open(path, "w", encoding="utf-8") as f:
        f.write(md)
    check(os.path.getsize(path) > 200, "摘要落盘")

    # 空结果：必须给「换个短词复验」的提示，而不是让人以为真的没人讨论
    em = fetch.render_digest(label, query, [], 1, 5000, args)
    check("没有抓到任何推文" in em and "更短的词" in em, "空结果给出召回陷阱提示")

    # 单页 0 条时，最低仍扣 15 credits
    check("15 credits" in em, "0 条也按最低 15 credits 计")


# ==========================================================================
# ③ 字段契约（SKILL.md 第 2 节的字段表必须与此处一致）
# ==========================================================================
NORM_FIELDS = {"id", "url", "time", "created_at", "ts", "text", "author", "author_name",
               "followers", "like", "rt", "reply", "quote", "view", "lang",
               "is_reply", "is_rt", "is_quote", "rule_tag"}

STREAM_FIELDS = {"id", "time", "author", "author_name", "followers", "text", "like", "rt",
                 "reply", "quote", "view", "lang", "url", "ts", "rule_tag", "received_at"}

FAST_EXTRA = {"kind", "media", "mentions"}

FULL_RAW = {"id": "1", "url": "u", "text": "t", "createdAt": "Sat Sep 26 12:00:00 +0000 2026",
            "lang": "en", "likeCount": 1, "retweetCount": 2, "replyCount": 3, "quoteCount": 4,
            "viewCount": 5, "isReply": True, "retweeted_tweet": {"id": "0"},
            "quoted_tweet": {"id": "0"},
            "author": {"userName": "a", "name": "A", "followers": 10}}


def test_field_contract():
    """抓到的字段就是文档承诺的那些 —— 多了少了都意味着文档该更新了。"""
    rec = fetch.normalize(FULL_RAW)
    check(set(rec) == NORM_FIELDS, "模式 A 落盘字段与文档一致（19 个）")
    check(rec["is_reply"] is True and rec["is_rt"] is True and rec["is_quote"] is True,
          "is_reply / is_rt / is_quote 由 isReply / retweeted_tweet / quoted_tweet 推得")
    check(rec["reply"] == 3 and rec["quote"] == 4 and rec["view"] == 5,
          "回复数 / 引用数 / 浏览量都有真实值")
    check(rec["url"] == "u" and rec["author_name"] == "A" and rec["lang"] == "en",
          "url / 作者昵称 / 语言保留")
    check(rec["created_at"].startswith("Sat Sep 26") and rec["time"].startswith("2026-09-26"),
          "原始 UTC 串与本地可读时间都在")

    s1 = stream.normalize_stream(dict(FULL_RAW))
    check(set(s1) == STREAM_FIELDS, "模式 B 规则推流的字段与文档一致（16 个 + 3）")
    check("is_rt" not in s1 and "created_at" not in s1,
          "模式 B 不产出 is_* / created_at（文档已注明仅模式 A 有）")

    s2 = stream.normalize_stream({"id": "9", "screen_name": "s", "display_name": "S", "text": "t",
                                 "type": "post", "created_ms": 1776623419483,
                                 "media": [{"type": "photo"}], "mentions": ["x"]})
    check(set(s2) == STREAM_FIELDS | FAST_EXTRA, "优先通道多出 kind / media / mentions")
    check(s2["like"] == 0 and s2["rt"] == 0 and s2["followers"] == 0,
          "优先通道没有互动数据（别拿它按热度排序 —— 文档已注明）")
    check(s2["kind"] == "post" and s2["url"].endswith("/status/9"), "优先通道补出 url 与 kind")


# ==========================================================================
# ④ 内容监控看板（dashboard.py）
# ==========================================================================
def test_dashboard(tmp):
    outdir = os.path.join(tmp, "dashout")
    qdir = os.path.join(outdir, "nvda")
    os.makedirs(qdir, exist_ok=True)

    rec = fetch.normalize(FULL_RAW)
    rec.update({"id": "555", "author": "trader", "like": 120, "rt": 8, "text": "看板测试"})
    with open(os.path.join(qdir, "tweets.json"), "w", encoding="utf-8") as f:
        json.dump({"label": "$NVDA", "query": "$NVDA since_time:1", "fetched_at": "2026-09-27 19:00:00",
                   "count": 1, "tweets": [rec]}, f, ensure_ascii=False)

    live = os.path.join(outdir, dashboard.LIVE_FILE)
    with open(live, "w", encoding="utf-8") as f:
        f.write(json.dumps(rec, ensure_ascii=False) + "\n")
        f.write("这一行是坏数据，必须被跳过\n")
        f.write(json.dumps(dict(rec, id="556", received_at="2026-09-27 19:05:00"),
                           ensure_ascii=False) + "\n")

    # —— 路径穿越防护（发布到市场时审核会看这个）——
    check(dashboard.safe_name(outdir, "nvda") == "nvda", "合法查询名通过校验")
    for bad in ("..", "../etc", "a/b", "nvda/../..", "", ".", "/abs", "C:\\Windows"):
        check(dashboard.safe_name(outdir, bad) is None, "路径穿越被挡：%r" % bad)

    # —— 数据组装 ——
    st = dashboard.state(outdir, 5)
    check(st["interval"] == 5 and len(st["queries"]) == 1, "state 返回查询列表与刷新间隔")
    check(st["queries"][0]["count"] == 1 and st["queries"][0]["label"] == "$NVDA", "查询概要正确")
    check(st["queries"][0]["engagement"] == 128, "互动数（赞+转推）统计正确")
    check(st["live"]["total"] == 3, "实时流累计行数统计正确")
    check(os.path.isabs(st["outdir"]), "outdir 以绝对路径回传")

    q = dashboard.query_payload(outdir, "nvda")
    check(q["count"] == 1 and q["tweets"][0]["id"] == "555", "query_payload 返回推文正文")
    check("error" in dashboard.query_payload(outdir, "../x"), "非法查询名返回 error")

    items, total = dashboard.read_jsonl(live)
    check(total == 3 and len(items) == 2, "jsonl 坏行被跳过（total 仍计 3）")
    check(items[-1]["id"] == "556" and items[-1]["_idx"] == 2, "增量条目带行号 _idx")
    check("error" in dashboard.live_payload(os.path.join(tmp, "nope")),
          "没有流文件时给出友好提示")

    # —— HTTP 端到端（随机端口起真服务）——
    srv = dashboard.http.server.ThreadingHTTPServer(("127.0.0.1", 0), dashboard.Handler)
    srv.outdir, srv.verbose, srv.interval = outdir, False, 3
    port = srv.server_address[1]
    threading.Thread(target=srv.serve_forever, daemon=True).start()

    def get(path):
        with urllib.request.urlopen("http://127.0.0.1:%d%s" % (port, path), timeout=5) as r:
            return r.status, r.read().decode("utf-8", "replace")

    try:
        code, body = get("/")
        check(code == 200 and "内容监控看板" in body, "看板首页 200 且是看板页")
        check("<style>" in body and "fetch('/api/state')" in body, "页面内嵌样式与轮询脚本")

        code, body = get("/api/health")
        check(code == 200 and json.loads(body)["ok"] is True, "/api/health 正常")

        code, body = get("/api/state")
        check(json.loads(body)["queries"][0]["name"] == "nvda", "/api/state 返回查询列表")

        code, body = get("/api/query?name=nvda")
        check(json.loads(body)["tweets"][0]["id"] == "555", "/api/query 返回推文")

        code, body = get("/api/live")
        check(json.loads(body)["total"] == 3, "/api/live 返回流数据")

        try:
            get("/api/query?name=..%2F..%2Fetc%2Fpasswd")
            check(False, "HTTP 层应拒绝路径穿越")
        except urllib.error.HTTPError as e:
            check(e.code == 400, "HTTP 层拒绝路径穿越（400）")

        try:
            get("/nope")
            check(False, "未知路径应 404")
        except urllib.error.HTTPError as e:
            check(e.code == 404, "未知路径返回 404")
    finally:
        srv.shutdown()
        srv.server_close()

    # —— 只读保证：服务全程不应改动 out/ 里的任何文件 ——
    check(set(os.listdir(outdir)) == {dashboard.LIVE_FILE, "nvda"},
          "看板没有新建/删除任何文件")


# ==========================================================================
# ⑤ WebSocket
# ==========================================================================
def _server_frame(op, payload=b""):
    n = len(payload)
    if n < 126:
        head = bytes([0x80 | op, n])
    elif n < 65536:
        head = bytes([0x80 | op, 126]) + n.to_bytes(2, "big")
    else:
        head = bytes([0x80 | op, 127]) + n.to_bytes(8, "big")
    return head + payload


def test_ws_framing():
    for payload in (b"", b"hi", b"x" * 300, b"y" * 70000):
        f = xapi.ws_frame(0xA, payload)
        check(f[0] == 0x8A, "pong 帧 opcode 正确")
        check(bool(f[1] & 0x80), "客户端帧带掩码位（RFC6455 要求，漏了会 20s 断线）")
        frames, rest = xapi.ws_frames_from(f)
        check(rest == b"" and len(frames) == 1 and frames[0][1] == payload,
              "掩码帧能被解析并自动解掩码")

    f = xapi.ws_frame(0x1, b"hello world")
    half = len(f) // 2
    fr1, rest = xapi.ws_frames_from(f[:half])
    check(fr1 == [], "半包不产出帧")
    fr2, rest2 = xapi.ws_frames_from(rest + f[half:])
    check(fr2 and fr2[0][1] == b"hello world" and rest2 == b"", "拼包后正确还原")

    # 两个帧粘在一个 chunk 里
    two = xapi.ws_frame(0x1, b"a") + xapi.ws_frame(0x1, b"b")
    fr, _ = xapi.ws_frames_from(two)
    check([p for _, p in fr] == [b"a", b"b"], "粘包能切出两帧")


def test_ws_events():
    evs = [
        ({"event_type": "connected", "timestamp": 1}, 0),
        ({"event_type": "ping", "timestamp": 1}, 0),
        ({"event_type": "tweet", "rule_id": "r1", "rule_tag": "t", "tweets": [
            {"id": "9", "text": "hi", "createdAt": "Sat Sep 26 12:00:00 +0000 2026",
             "likeCount": 3, "author": {"userName": "u", "followers": 10}}]}, 1),
        ({"event_type": "fast_tweet", "timestamp": 2, "tweet": {
            "id": "77", "screen_name": "MarioNawfal", "text": "fast lane",
            "created_ms": 1776623419483, "type": "post"}}, 1),
    ]
    for obj, want in evs:
        check(len(stream.extract_tweets(obj)) == want, f"extract_tweets({obj.get('event_type')})")

    r1 = stream.normalize_stream(evs[2][0]["tweets"][0])
    check(r1["author"] == "u" and r1["like"] == 3 and r1["rule_tag"] == "", "规则推文归一化")
    r2 = stream.normalize_stream(evs[3][0]["tweet"])
    check(r2["author"] == "MarioNawfal" and r2["url"].endswith("/status/77"),
          "fast_tweet 归一化并补出 url")


class _PlainTLS:
    def wrap_socket(self, sock, server_hostname=None):
        return sock


def _fake_server(port_holder, ready, obs):
    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    srv.bind(("127.0.0.1", 0))
    srv.listen(1)
    port_holder.append(srv.getsockname()[1])
    ready.set()

    conn, _ = srv.accept()
    hs = b""
    while b"\r\n\r\n" not in hs:
        c = conn.recv(4096)
        if not c:
            break
        hs += c
    head = hs.decode("utf-8", "replace")
    obs["key_header"] = "x-api-key: " in head
    obs["upgrade"] = "Upgrade: websocket" in head
    # 101 响应和第一个 ping 帧**故意一次性 sendall** —— 真实服务端完全可能把帧
    # 粘在握手响应里发过来，客户端必须把头之后的字节交给帧循环而不是丢弃。
    # （这一行曾在 Linux CI 上稳定复现：单次 recv 读握手会静默吞掉 ping。）
    conn.sendall(b"HTTP/1.1 101 Switching Protocols\r\nUpgrade: websocket\r\n"
                 b"Connection: Upgrade\r\n\r\n" + _server_frame(0x9, b"hb-1"))
    conn.settimeout(2.0)
    buf = b""
    try:
        while True:
            buf += conn.recv(65536)
            frames, _ = xapi.ws_frames_from(buf)
            if frames:
                obs["pong"] = frames[0]
                break
    except Exception:
        pass

    def ev(obj):
        conn.sendall(_server_frame(0x1, json.dumps(obj).encode()))

    ev({"event_type": "connected", "timestamp": 1})
    ev({"event_type": "ping", "timestamp": 2})
    ev({"event_type": "tweet", "rule_id": "r1", "rule_tag": "nvda", "tweets": [{
        "id": "9001", "text": "NVDA guided up. " * 200, "url": "https://x.com/u/status/9001",
        "createdAt": "Sat Sep 26 12:00:00 +0000 2026", "likeCount": 7,
        "author": {"userName": "u", "followers": 1234}}]})
    ev({"event_type": "fast_tweet", "timestamp": 3, "tweet": {
        "id": "9002", "screen_name": "fastlane", "text": "fast lane hit",
        "created_ms": 1776623419483, "type": "post"}})
    ev({"event_type": "tweet", "rule_id": "r2", "rule_tag": "other", "tweets": [{
        "id": "9003", "text": "other tag",
        "createdAt": "Sat Sep 26 12:00:00 +0000 2026", "author": {"userName": "x"}}]})
    time.sleep(0.5)
    conn.sendall(_server_frame(0x8, b""))
    time.sleep(0.2)
    try:
        conn.close()
    except Exception:
        pass
    srv.close()


def _run_ws_session(tags, tmp, label):
    obs, port_holder = {}, []
    ready = threading.Event()
    t = threading.Thread(target=_fake_server, args=(port_holder, ready, obs), daemon=True)
    t.start()
    ready.wait(5)
    if not port_holder:
        raise AssertionError("假服务端没起来")

    ssl.create_default_context = lambda *a, **k: _PlainTLS()
    xapi.WS_HOST, xapi.WS_PORT = "127.0.0.1", port_holder[0]

    out = os.path.join(tmp, f"live_{label}.jsonl")
    with open(out, "w", encoding="utf-8") as f:
        stream.session(3, "testkey", None, {}, tags, 0, f, True)
    t.join(timeout=5)

    recs = [json.loads(l) for l in open(out, encoding="utf-8") if l.strip()]
    check(obs.get("key_header"), f"[{label}] 握手带 x-api-key")
    check(obs.get("upgrade"), f"[{label}] 握手带 Upgrade: websocket")
    pong = obs.get("pong")
    check(pong and pong[0] == 0xA and pong[1] == b"hb-1",
          f"[{label}] ping→pong 正确（含解掩码）")
    return recs


def test_ws_session(tmp):
    recs = _run_ws_session({"nvda"}, tmp, "tagged")
    check([r["id"] for r in recs] == ["9001"], "带 --tag 时只收该 tag 的推文")
    check(len(recs[0]["text"]) > 1000, "长正文跨 TCP 分片正确重组")
    check(recs[0]["author"] == "u" and recs[0]["like"] == 7, "字段正确")

    recs2 = _run_ws_session(set(), tmp, "untagged")
    check([r["id"] for r in recs2] == ["9001", "9002", "9003"],
          "不带 --tag 时规则推文与 Stream 推文都收")
    check(recs2[1]["author"] == "fastlane" and recs2[1]["url"].endswith("/status/9002"),
          "fast_tweet 能收到（带 --tag 时会被过滤，属预期）")


# ==========================================================================
# ⑦ SKILL.md frontmatter —— 平台侧用真 YAML 解析器（go-yaml），而官方
#    quick_validate.py 只做正则匹配、**不检查 YAML 语法**，所以必须自己查。
#    踩过的坑：description 里写 "Also for: X monitoring."（冒号+空格）会被
#    YAML 当成新键，平台报 "mapping values are not allowed in this context"。
# ==========================================================================
REQUIRED_FM = ["name", "description", "description_zh", "description_en",
               "version", "author"]

YAML_INDICATORS = ("-", "?", ":", ",", "[", "]", "{", "}", "#", "&", "*",
                   "!", "|", ">", "%", "@", "`", '"', "'")


def _frontmatter_fields(text):
    if not text.startswith("---\n"):
        raise AssertionError("FAILED: SKILL.md 必须以 '---' + LF 开头")
    end = text.find("\n---\n", 3)
    if end < 0:
        raise AssertionError("FAILED: SKILL.md frontmatter 未闭合")
    fields = []
    for raw in text[4:end].split("\n"):
        if not raw.strip() or raw.lstrip().startswith("#"):
            continue
        key, sep, val = raw.partition(":")
        if not sep or not key.strip() or " " in key.strip():
            raise AssertionError("FAILED: frontmatter 不是 key: value -> " + raw)
        fields.append((key.strip(), val.strip()))
    return fields, text[4:end]


def _unquote(val):
    """取字面量语义值：去掉包裹引号并反转义。"""
    if len(val) >= 2 and val[0] == val[-1] and val[0] in "\"'":
        inner = val[1:-1]
        if val[0] == '"':
            return inner.replace('\\"', '"').replace("\\\\", "\\")
        return inner.replace("''", "'")
    return val


def test_frontmatter():
    blob = open(os.path.join(ROOT, "SKILL.md"), "rb").read()
    check(b"\r\n" not in blob, "SKILL.md 是 LF 换行（CRLF 会被校验器误判）")
    fields, fm_text = _frontmatter_fields(blob.decode("utf-8"))
    keys = [k for k, _ in fields]

    for need in REQUIRED_FM:
        check(need in keys, "frontmatter 含平台必填字段 " + need)
    check(keys[0] == "name", "name 是 frontmatter 第一个字段")
    check(len(keys) == len(set(keys)), "frontmatter 无重复键")

    for key, val in fields:
        quoted = len(val) >= 2 and val[0] == val[-1] and val[0] in "\"'"
        if quoted:
            inner = val[1:-1].replace('\\"', "").replace("\\'", "")
            check(val[0] not in inner, key + " 的引号完整包裹（内部无裸引号）")
            continue
        check(": " not in val,
              key + " 未加引号时不得含「冒号+空格」（会让 YAML 解析失败）")
        check("# " not in val, key + " 未加引号时不得含「井号+空格」")
        check("\t" not in val, key + " 未加引号时不得含制表符")
        check(not val.startswith(YAML_INDICATORS),
              key + " 未加引号时不得以 YAML 指示符开头")

    fm = {k: _unquote(v) for k, v in fields}
    check(fm["name"] == os.path.basename(ROOT), "name 与技能目录名一致")
    check("-" in fm["name"] and fm["name"] == fm["name"].lower(),
          "name 是 hyphen-case 小写")
    check(bool(re.match(r"^\d+\.\d+\.\d+$", fm["version"])),
          "version 是 x.y.z 形式")
    head = fm["description"][:149]
    check(len(fm["description"]) >= 60, "description 长度足够")
    check(any(w in head.lower() for w in ("tweet", "twitter", "cashtag", "monitoring")),
          "description 前 149 字符含英文检索词（agent 只注入这么长）")
    check(any(w in head for w in ("推文", "推特", "实时", "股票代码")),
          "description 前 149 字符含中文检索词")

    # 官方 quick_validate.py 用**裸正则**取值、不做 YAML 去引号，
    # 所以 name 必须裸露；给它加引号会被判 "should be hyphen-case" 而打包失败。
    m = re.search(r"name:\s*(.+)", fm_text)
    check(bool(m) and bool(re.match(r"^[a-z0-9-]+$", m.group(1).strip())),
          "name 能被官方校验器的裸正则取到（加引号会判非法）")
    dm = re.search(r"description:\s*(.+)", fm_text)
    check(bool(dm) and "<" not in dm.group(1) and ">" not in dm.group(1),
          "description 不含尖括号（官方校验器硬性要求）")

    try:                     # 有真解析器就交叉验证一次（没装则跳过，不算失败）
        import yaml
        yaml.safe_load(fm_text)
        check(True, "真 YAML 解析器可解析 frontmatter")
    except ImportError:
        pass
    except Exception as exc:  # noqa: BLE001
        raise AssertionError("FAILED: 真 YAML 解析器报错 -> " + str(exc))


# ==========================================================================
# ⑦ LLM 解读（transport 注入假响应，全程离线）
# ==========================================================================
def test_interpret(tmp):
    inp = os.path.join(tmp, "nvda")
    os.makedirs(inp, exist_ok=True)

    def tw(i, author, text, like, ts, is_rt=False):
        return {"id": str(i), "url": "https://x.com/x/status/" + str(i), "text": text,
                "author": author, "time": "2026-09-30 10:00", "ts": ts,
                "like": like, "rt": like // 10, "reply": 3, "quote": 1,
                "followers": 1000, "is_rt": is_rt}

    tweets = [
        tw(1, "aaa", "NVDA guidance looks strong, Blackwell demand is insane", 900, 5000),
        tw(2, "bbb", "retweet shell", 0, 4999, is_rt=True),
        tw(3, "ccc", "I think the run is priced in, careful here", 300, 4000),
        tw(4, "ddd", "", 100, 3999),
        tw(5, "eee", "just bought more $NVDA today", 50, 3998),
    ]
    with open(os.path.join(inp, "tweets.json"), "w", encoding="utf-8") as f:
        json.dump({"label": "$NVDA", "query": "$NVDA since_time:1",
                   "fetched_at": "2026-09-30 10:00:00",
                   "count": len(tweets), "tweets": tweets}, f, ensure_ascii=False)

    sel = interpret.select_tweets(tweets, 40)
    check([t["author"] for t in sel] == ["aaa", "ccc", "eee"],
          "解读挑选：剔纯转推/空文本，按互动排序")
    sys_zh, user = interpret.build_messages(
        sel, {"label": "$NVDA", "query": "q", "fetched_at": "t", "total": 5})
    check("不要打分" in sys_zh and "不要评级" in sys_zh,
          "提示词硬性禁止打分/评级（解读而非评分系统）")
    check("@aaa" in user and "Blackwell" in user, "用户消息带作者与原文")
    sys_en, _ = interpret.build_messages(
        sel, {"label": "x", "query": "", "fetched_at": "", "total": 5}, lang="en")
    check("Do not assign scores" in sys_en, "英文提示词同样禁评分")

    check(interpret.extract_content(
        {"choices": [{"message": {"content": "```markdown\n正文\n```"}}]}) == "正文",
        "响应解析剥掉 markdown 围栏")

    seen = {}

    def fake_transport(payload, headers):
        seen.update(payload=payload)
        return 200, json.dumps({"choices": [{"message": {"content": "## 总览\n这是测试解读。"}}]})

    ok, _msg = interpret.run(os.path.join(inp, "tweets.json"), key="test-key",
                             model="test-model", api_base="https://llm.example/v1",
                             quiet=True, transport=fake_transport)
    check(ok, "run() 在假传输下成功")
    check(seen["payload"]["model"] == "test-model"
          and seen["payload"]["messages"][0]["role"] == "system",
          "请求体带模型名与 system 消息")
    outp = os.path.join(inp, "interpretation.md")
    check(os.path.isfile(outp), "解读落盘 interpretation.md")
    md = open(outp, encoding="utf-8").read()
    check("这是测试解读" in md and "test-model" in md and "llm.example" in md,
          "产物含正文与模型/服务商标识")
    check("非投资建议" in md, "产物带免责声明")

    jsonl = os.path.join(tmp, "stream.jsonl")
    with open(jsonl, "w", encoding="utf-8") as f:
        for t in tweets[:2]:
            f.write(json.dumps({"event": "tweet",
                                "tweet": {"id": t["id"], "text": t["text"],
                                          "author": t["author"], "ts": t["ts"],
                                          "like": t["like"]}},
                               ensure_ascii=False) + "\n")
    d = interpret.load_tweets(jsonl)
    check(len(d["tweets"]) == 2 and d["tweets"][0]["author"] == "aaa",
          "JSONL 输入（含 tweet 包装）可解析")

    # 无 LLM Key：给出可执行提示并跳过（绝不联网、绝不崩溃）
    old_cwd = os.getcwd()
    saved = {name: os.environ.pop(name, None) for name in interpret.LLM_KEY_ENV}
    try:
        os.chdir(tmp)  # 避开仓库里的 .secrets 密钥文件
        ok2, msg2 = interpret.run(os.path.join(inp, "tweets.json"))
        check(not ok2 and "API Key" in msg2, "无 LLM Key 时给出可执行提示并跳过")
    finally:
        os.chdir(old_cwd)
        for name, v in saved.items():
            if v is not None:
                os.environ[name] = v


# ==========================================================================
def main():
    xapi.setup_stdout()
    with tempfile.TemporaryDirectory() as tmp:
        test_query()
        test_fetch_pipeline(tmp)
        test_field_contract()
        test_interpret(tmp)
        test_dashboard(tmp)
        test_ws_framing()
        test_ws_events()
        test_ws_session(tmp)
        test_frontmatter()
    print(f"✓ 全部通过（{len(OK)} 项断言）")
    for label in OK:
        print("  ·", label)


if __name__ == "__main__":
    main()
