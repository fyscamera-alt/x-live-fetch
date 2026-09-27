# -*- coding: utf-8 -*-
"""实时消费 twitterapi.io 的推文流（WebSocket），一条进来就立刻打印 + 落盘。

前提：先用 rules.py 建好规则并 **激活**（未激活的规则不推数据）。

    python scripts/stream.py --sec 600                 # 收 10 分钟
    python scripts/stream.py --tag nvda --sec 600      # 只看某条规则
    python scripts/stream.py --loop                    # 常驻（断线自动退避重连）

产物：out/x_live.jsonl（一行一条，可直接喂给下游分析）

平台行为（实测，别当成 bug）：
  · 一个 API Key 只能开**一条**活跃连接，第二条被关闭码 1008 拒绝
  · 断线后**至少等 90 秒**再重连，否则服务端连接槽位还没释放
  · 会话常在 20~30 秒内被服务端主动关闭 → 正常形态是"连上 → 收一会儿 → 断 → 退避重连"
  · 每次重连服务端可能把最近一批推文重发一遍（客户端必须按 id 去重，否则统计虚高）
  · 服务端会发协议级 ping 帧（要回 pong）和应用级 {"event_type":"ping"}（忽略即可）
"""

from __future__ import annotations

import argparse
import collections
import json
import os
import socket
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import xapi  # noqa: E402

RECONNECT_WAIT = 95     # 实测：服务端槽位释放在 90s 左右
MAX_WAIT = 600
SHORT_SESSION = 60      # 会话短于此秒数 = 这轮没料，退避更久一点
SEEN_CAP = 20000


def normalize_stream(tw: dict) -> dict:
    """把两种推文形态统一成一条记录。

    规则推送：完整 Twitter 兼容对象（author / createdAt / likeCount …）
    优先通道：精简对象（screen_name / created_ms / media / mentions）
    """
    if "author" in tw or "createdAt" in tw:
        rec = {
            "id": str(tw.get("id") or ""),
            "time": xapi.human_time(tw),
            "author": (tw.get("author") or {}).get("userName") or "",
            "author_name": (tw.get("author") or {}).get("name") or "",
            "followers": (tw.get("author") or {}).get("followers") or 0,
            "text": (tw.get("text") or "").strip(),
            "like": tw.get("likeCount") or 0,
            "rt": tw.get("retweetCount") or 0,
            "reply": tw.get("replyCount") or 0,
            "quote": tw.get("quoteCount") or 0,
            "view": tw.get("viewCount") or 0,
            "lang": tw.get("lang") or "",
            "url": tw.get("url") or "",
        }
    else:
        rec = {
            "id": str(tw.get("id") or ""),
            "time": xapi.human_time(tw),
            "author": tw.get("screen_name") or "",
            "author_name": tw.get("display_name") or "",
            "followers": 0,
            "text": (tw.get("text") or "").strip(),
            "like": 0,
            "rt": 0,
            "reply": 0,
            "quote": 0,
            "view": 0,
            "lang": "",
            "url": f"https://x.com/{tw.get('screen_name')}/status/{tw.get('id')}"
            if tw.get("screen_name") and tw.get("id")
            else "",
            "kind": tw.get("type") or "",
            "media": tw.get("media") or [],
            "mentions": tw.get("mentions") or [],
        }
    rec["ts"] = xapi.epoch_ms(tw)
    rec["rule_tag"] = tw.get("rule_tag") or ""
    rec["received_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
    return rec


def extract_tweets(obj: dict) -> list[dict]:
    """从一个 WS 事件里取出推文数组（可能 0 条）。"""
    et = obj.get("event_type") or obj.get("type")

    if et in ("ping", "connected"):
        return []
    if et == "fast_tweet" and isinstance(obj.get("tweet"), dict):
        return [obj["tweet"]]

    tws = obj.get("tweets")
    if isinstance(tws, list):
        return [t for t in tws if isinstance(t, dict)]
    if isinstance(tws, dict):
        return [tws]
    if obj.get("id") and (obj.get("text") is not None):
        return [obj]
    return []


def rules_active(key, proxy) -> int:
    """激活中的规则条数；查不到返回 -1（不确定）。"""
    try:
        d = xapi.request("/oapi/tweet_filter/get_rules", key=key, proxy=proxy, retries=1)
    except SystemExit:
        return -1
    rules = d if isinstance(d, list) else (d.get("rules") or [])
    return sum(1 for r in rules if str((r or {}).get("is_effect")) in ("1", "True", "true"))


def session(sec: int, key: str, proxy, seen, tags: set[str], min_followers: int,
            out_f, quiet: bool) -> tuple[int, int]:
    """一次连接：收到 sec 秒或断线为止。返回 (新增推文数, 重复跳过数)。"""
    ss = xapi.ws_connect(key, proxy)
    if ss is None:
        return 0, 0

    if not quiet:
        print(f"已连接 {xapi.WS_HOST}{xapi.WS_PATH}，等待推文…（计划 {sec}s）", flush=True)

    n_new = n_dup = 0
    buf = b""
    t_end = time.time() + sec
    try:
        while time.time() < t_end:
            try:
                ss.settimeout(max(1, min(45, t_end - time.time())))
                chunk = ss.recv(65536)
            except socket.timeout:
                continue
            if not chunk:
                if not quiet:
                    print("服务端关闭连接", flush=True)
                break
            buf += chunk
            frames, buf = xapi.ws_frames_from(buf)
            for op, payload in frames:
                if op == 0x9:                      # 协议级 ping → 必须回 pong（且要带掩码）
                    try:
                        ss.sendall(xapi.ws_frame(0xA, payload))
                    except Exception:
                        pass
                    continue
                if op == 0x8:                      # close
                    if not quiet:
                        print("收到 close 帧", flush=True)
                    return n_new, n_dup
                if op not in (0x1, 0x0):
                    continue
                txt = payload.decode("utf-8", "replace").strip()
                if not txt or not txt.startswith("{"):
                    continue
                try:
                    obj = json.loads(txt)
                except json.JSONDecodeError:
                    continue

                for tw in extract_tweets(obj):
                    tag = str(obj.get("rule_tag") or tw.get("rule_tag") or "")
                    if tags and tag not in tags:
                        continue
                    rec = normalize_stream(tw)
                    if not rec["id"]:
                        continue
                    if rec["id"] in seen:
                        n_dup += 1
                        continue
                    seen[rec["id"]] = 1
                    while len(seen) > SEEN_CAP:
                        seen.popitem(last=False)
                    if min_followers and rec["followers"] < min_followers:
                        continue
                    n_new += 1
                    out_f.write(json.dumps(rec, ensure_ascii=False) + "\n")
                    out_f.flush()
                    if not quiet:
                        tagtxt = f"[{tag}] " if tag else ""
                        print(
                            f"{rec['received_at'][11:]} {tagtxt}@{rec['author']:<18} "
                            f"{xapi.shorten(rec['text'], 100)}",
                            flush=True,
                        )
    except Exception as e:  # noqa: BLE001
        if not quiet:
            print(f"会话异常 {type(e).__name__}: {str(e)[:160]}", flush=True)
    finally:
        try:
            ss.close()
        except Exception:
            pass
    return n_new, n_dup


def main():
    xapi.setup_stdout()
    p = argparse.ArgumentParser(
        description="实时消费 X 推文流（WebSocket）",
        epilog=f"还没有 API Key？去 {xapi.SIGNUP_URL} 注册（按量付费）。",
    )
    p.add_argument("--tag", help="只收这些规则 tag 的推文（逗号分隔）。"
                                 "注意：设了它就只收规则匹配的推文，"
                                 "不带 tag 的 Stream/fast_tweet 事件会被一并过滤掉；"
                                 "要收账号实时流就别加 --tag")
    p.add_argument("--sec", type=int, default=600, help="单次会话时长秒，默认 600")
    p.add_argument("--loop", action="store_true", help="常驻：断线自动退避重连")
    p.add_argument("--min-followers", type=int, default=0, help="过滤掉粉丝数低于此值的账号")
    p.add_argument("--out", default=os.path.join("out", "x_live.jsonl"), help="落盘文件")
    p.add_argument("--quiet", action="store_true")
    p.add_argument("--key")
    p.add_argument("--proxy", help="默认直连")
    a = p.parse_args()

    key = xapi.load_key(a.key)
    tags = {t.strip() for t in (a.tag or "").split(",") if t.strip()}

    xapi.ensure_dir(os.path.dirname(a.out) or ".")
    out_f = open(a.out, "a", encoding="utf-8")

    seen: collections.OrderedDict = collections.OrderedDict()
    total = dups = 0
    wait = RECONNECT_WAIT

    print(f"落盘: {a.out}", flush=True)
    print("看板: 另开一个终端跑 python scripts/dashboard.py，"
          "浏览器开 http://127.0.0.1:8765 —— 这里收到的每条会实时出现在页面上。", flush=True)
    try:
        while True:
            act = rules_active(key, a.proxy)
            if act == 0:
                msg = (
                    "当前没有任何**已激活**的规则 → 连上也不会收到数据（且空连可能触发付费补推）。\n"
                    "  先建规则并激活：\n"
                    "    python scripts/rules.py create --tag demo --value '$NVDA' --interval 300\n"
                    "    python scripts/rules.py on --tag demo"
                )
                print("⚠️ " + msg, flush=True)
                if not a.loop:
                    return
                time.sleep(60)
                continue
            if act == -1 and not a.quiet:
                print("（规则状态查不到，按「可能有」继续）", flush=True)

            t0 = time.time()
            n, d = session(a.sec, key, a.proxy, seen, tags, a.min_followers, out_f, a.quiet)
            total += n
            dups += d
            dur = time.time() - t0

            if not a.loop:
                print(f"\n结束：运行 {dur:.0f}s，新收 {total} 条（重复跳过 {dups} 条）", flush=True)
                return

            wait = min(wait * 2, MAX_WAIT) if dur < SHORT_SESSION else RECONNECT_WAIT
            print(
                f"[{time.strftime('%H:%M:%S')}] 本轮 {dur:.0f}s 收 {n} 条 | "
                f"累计 {total} 条 | 等 {wait}s 重连",
                flush=True,
            )
            time.sleep(wait)
    except KeyboardInterrupt:
        print(f"\n已手动停止。累计收到 {total} 条（重复跳过 {dups} 条）", flush=True)
    finally:
        out_f.close()


if __name__ == "__main__":
    main()
