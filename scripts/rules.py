# -*- coding: utf-8 -*-
"""管理 tweet_filter 过滤规则（WebSocket / Webhook 推送的「订阅」）。

    python scripts/rules.py list                                  # 规则 + 余额（免费）
    python scripts/rules.py create --tag nvda --value '$NVDA' --interval 300
    python scripts/rules.py on --tag nvda                         # 激活（开始计费）
    python scripts/rules.py off --tag nvda                        # 停用（停止计费）
    python scripts/rules.py off-all
    python scripts/rules.py delete --tag nvda

平台契约（照抄官方文档 + 实测）：
  · 新建：POST   /oapi/tweet_filter/add_rule    body {tag, value, interval_seconds}
          **默认不激活** —— 建好是免费的，必须再调 update 才会生效。
  · 更新：POST   /oapi/tweet_filter/update_rule body {rule_id, tag, value, interval_seconds, is_effect}
          **必须传全字段**，漏传 interval_seconds 会被平台重置成最便宜那档里最贵的间隔（20s）。
  · 删除：DELETE /oapi/tweet_filter/delete_rule body {rule_id}    （用 POST 会 405）
  · 列表：GET    /oapi/tweet_filter/get_rules
  · 规则激活即开始计费，跟你有没有连 WebSocket 无关。
"""

from __future__ import annotations

import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import xapi  # noqa: E402

SAFE_MIN_INTERVAL = 100  # 文档口径混乱（add 说 ≥0.05 / update 说 ≥0.1 / get_rules 说 ≥100），实测 ≥100 才安全


def _rules(key=None, proxy=None) -> list[dict]:
    d = xapi.request("/oapi/tweet_filter/get_rules", key=key, proxy=proxy)
    if isinstance(d, list):
        rs = d
    else:
        rs = d.get("rules") or []
    return [r for r in rs if isinstance(r, dict)]


def _find(rules: list[dict], tag: str) -> dict:
    for r in rules:
        if str(r.get("tag")) == tag:
            return r
    xapi.die(
        f"没有 tag = {tag!r} 的规则。现有："
        + (", ".join(str(r.get("tag")) for r in rules) or "（无）")
    )
    return {}


def _is_on(r) -> bool:
    return str(r.get("is_effect")) in ("1", "True", "true")


# --------------------------------------------------------------------------
def cmd_list(key=None, proxy=None):
    rules = _rules(key, proxy)
    bal = xapi.get_balance(key, proxy)

    if not rules:
        print("当前没有过滤规则。用 create 建一条（建好不激活，免费）。")
    else:
        print(f"{'tag':<22} {'生效':<6} {'间隔':<10} value")
        print("-" * 72)
        for r in rules:
            eff = "✅ 开" if _is_on(r) else "⭕ 关"
            iv = r.get("interval_seconds")
            try:
                ivs = f"{float(iv):g}s"
            except (TypeError, ValueError):
                ivs = str(iv)
            print(f"{str(r.get('tag')):<22} {eff:<6} {ivs:<10} {r.get('value')}")
        n_on = sum(1 for r in rules if _is_on(r))
        print("-" * 72)
        print(f"共 {len(rules)} 条，其中已激活 {n_on} 条")
        if n_on:
            print("⚠️ 已激活的规则**正在持续计费**，不用了记得 off。")
        else:
            print("规则全关 → 连上 WebSocket 也收不到数据（且空连可能触发付费补推）。")

    if bal is None:
        print("\n余额: 查询失败")
    else:
        print(f"\n余额: {xapi.fmt_balance(bal)}")
    return rules


# --------------------------------------------------------------------------
def cmd_create(a):
    body = {"tag": a.tag, "value": a.value, "interval_seconds": a.interval}
    d = xapi.request("/oapi/tweet_filter/add_rule", body=body, key=a.key, proxy=a.proxy)
    rid = d.get("rule_id")
    print(f"✓ 已创建规则 tag={a.tag} id={rid} value={a.value!r} interval={a.interval}s")
    print("  ⚠️ 新规则默认**未激活**（不推送、不扣费）。要开始收流：")
    print(f"     python scripts/rules.py on --tag {a.tag}")


def cmd_update(a, activate=None):
    rules = _rules(a.key, a.proxy)
    r = _find(rules, a.tag)

    value = a.value if a.value is not None else r.get("value")
    interval = a.interval if a.interval is not None else r.get("interval_seconds")
    try:
        interval = float(interval)
    except (TypeError, ValueError):
        interval = 300.0
    if interval < SAFE_MIN_INTERVAL:
        print(
            f"⚠️ 间隔 {interval:g}s 低于 {SAFE_MIN_INTERVAL}s —— 文档允许但**会显著烧钱**。"
            "要「真秒级」请用 WebSocket（stream.py），不要靠调小间隔。",
            file=sys.stderr,
        )

    if activate is None:
        is_effect = 1 if _is_on(r) else 0
    else:
        is_effect = 1 if activate else 0

    body = {
        "rule_id": r.get("rule_id"),
        "tag": a.tag,
        "value": value,
        "interval_seconds": interval,
        "is_effect": is_effect,
    }
    xapi.request("/oapi/tweet_filter/update_rule", body=body, key=a.key, proxy=a.proxy)

    # 改完必须逐条比对 —— 这是唯一的验收手段（平台不返回生效后的明细）
    after = _find(_rules(a.key, a.proxy), a.tag)
    ok = str(after.get("interval_seconds")) in (str(interval), str(int(interval)))
    print(
        f"✓ {a.tag}: is_effect={after.get('is_effect')} "
        f"interval={after.get('interval_seconds')} value={after.get('value')} "
        f"{'' if ok else '⚠️ 间隔与期望不符，请复查'}"
    )
    if is_effect == 1:
        print("  ℹ️ 已激活 = 开始计费。激活后首次连接会补推一小批历史推文，都算钱。")


def cmd_delete(a):
    rules = _rules(a.key, a.proxy)
    r = _find(rules, a.tag)
    xapi.request(
        "/oapi/tweet_filter/delete_rule",
        body={"rule_id": r.get("rule_id")},
        method="DELETE",
        key=a.key,
        proxy=a.proxy,
    )
    left = [x for x in _rules(a.key, a.proxy) if str(x.get("tag")) != a.tag]
    print(f"✓ 已删除 tag={a.tag}（剩余 {len(left)} 条规则）")


def cmd_all(a, activate: bool):
    rules = _rules(a.key, a.proxy)
    if not rules:
        print("没有规则可操作。")
        return
    for r in rules:
        body = {
            "rule_id": r.get("rule_id"),
            "tag": r.get("tag"),
            "value": r.get("value"),
            "interval_seconds": r.get("interval_seconds") or 300,
            "is_effect": 1 if activate else 0,
        }
        xapi.request("/oapi/tweet_filter/update_rule", body=body, key=a.key, proxy=a.proxy)
    state = "激活" if activate else "停用"
    print(f"✓ 已{state}全部 {len(rules)} 条规则")
    if activate:
        print("  ⚠️ 现在正在持续计费。用完请 off-all。")


# --------------------------------------------------------------------------
def main():
    xapi.setup_stdout()
    p = argparse.ArgumentParser(description="twitterapi.io 推文过滤规则管理")
    p.add_argument("--key")
    p.add_argument("--proxy", help="默认直连")
    sub = p.add_subparsers(dest="cmd", required=True)

    sub.add_parser("list", help="列出规则与余额")

    c = sub.add_parser("create", help="新建规则（默认不激活）")
    c.add_argument("--tag", required=True, help="规则标识，如 nvda")
    c.add_argument("--value", required=True, help="过滤表达式，如 '$NVDA' 或 'from:a OR from:b'")
    c.add_argument("--interval", type=float, default=300, help="轮询间隔秒，默认 300")

    o = sub.add_parser("on", help="激活规则（开始计费）")
    o.add_argument("--tag", required=True)
    f = sub.add_parser("off", help="停用规则")
    f.add_argument("--tag", required=True)

    u = sub.add_parser("update", help="改 value / interval（不改开关状态）")
    u.add_argument("--tag", required=True)
    u.add_argument("--value")
    u.add_argument("--interval", type=float)

    d = sub.add_parser("delete", help="删除规则")
    d.add_argument("--tag", required=True)

    sub.add_parser("on-all", help="激活全部")
    sub.add_parser("off-all", help="停用全部")

    a = p.parse_args()

    if a.cmd == "list":
        cmd_list(a.key, a.proxy)
    elif a.cmd == "create":
        cmd_create(a)
    elif a.cmd == "on":
        cmd_update(a, activate=True)
    elif a.cmd == "off":
        cmd_update(a, activate=False)
    elif a.cmd == "update":
        cmd_update(a, activate=None)
    elif a.cmd == "delete":
        cmd_delete(a)
    elif a.cmd == "on-all":
        cmd_all(a, True)
    elif a.cmd == "off-all":
        cmd_all(a, False)


if __name__ == "__main__":
    main()
