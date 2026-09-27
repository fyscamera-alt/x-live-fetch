# -*- coding: utf-8 -*-
"""twitterapi.io 通用客户端 —— 纯 Python 标准库实现，零第三方依赖。

把每个脚本都要做的三件事集中在这里，避免重复实现：

  1) 找 API Key —— 命令行 > 环境变量 > 密钥文件（多个候选位置）
  2) 发请求   —— 默认**直连**（不继承 HTTP_PROXY / HTTPS_PROXY 等环境代理）
  3) 小工具   —— 余额查询、推文时间解析、输出路径、终端输出

被 fetch.py / rules.py / stream.py import。也可以直接当命令行用：

    python scripts/xapi.py balance        # 查余额
    python scripts/xapi.py rules          # 列出现有过滤规则
"""

from __future__ import annotations

import json
import os
import re
import socket
import ssl
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

BASE = "https://api.twitterapi.io"
WS_HOST = "ws.twitterapi.io"
WS_PORT = 443
WS_PATH = "/twitter/tweet/websocket"

# ——— 计费口径（官方定价页）———
CREDITS_PER_TWEET = 15          # $0.15 / 1000 条推文
CREDITS_PER_USD = 100_000       # 100,000 credits = $1.00
MIN_CREDITS_PER_CALL = 15       # 每次调用最低扣费（返回 0 条也扣）

KEY_ENV_VARS = (
    "TWITTERAPI_IO_KEY",
    "TWITTERAPI_KEY",
    "TWITTERAPI_IO_API_KEY",
    "X_API_KEY",
)

KEY_FILE_CANDIDATES = (
    os.path.join(".secrets", "x-api.key"),
    os.path.join(".secrets", "twitterapi.key"),
    os.path.join(".secrets", "twitterapi-io.key"),
    os.path.join(os.path.expanduser("~"), ".twitterapi-io.key"),
    os.path.join(os.path.expanduser("~"), ".secrets", "x-api.key"),
)

SIGNUP_URL = "https://twitterapi.io?ref=fysc666"
# 官方 FAQ 口径：新账号注册立送 $0.1 免费额度、无需信用卡。
# 1 USD = 100,000 credits → 10,000 credits；每条推文 15 credits ≈ 660 条推文。
FREE_TRIAL_NOTE = (
    f"新用户通过 {SIGNUP_URL} 注册即送 $0.1 免费额度"
    "（= 10,000 credits，约 660 条推文），无需信用卡，可先免费测试。"
)


# --------------------------------------------------------------------------
# 输出
# --------------------------------------------------------------------------
def setup_stdout() -> None:
    """Windows 控制台默认不是 UTF-8，中文会乱码 —— 尽力修一下，失败就算了。"""
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass


def die(msg: str, code: int = 2):
    print("✗ " + msg, file=sys.stderr)
    sys.exit(code)


# --------------------------------------------------------------------------
# API Key
# --------------------------------------------------------------------------
def _clean_key(raw: str) -> str:
    """容忍用户把整个 header 或引号一起粘进来。"""
    s = (raw or "").strip().strip('"').strip("'").strip()
    if not s:
        return ""
    # 形如 `x-api-key: my_test_xxx` 或 `X-API-Key=xxx`
    m = re.match(r"^x-api-key\s*[:=]\s*(.+)$", s, re.I)
    if m:
        s = m.group(1).strip()
    return s.split()[0] if s.split() else s


def load_key(cli_key: str | None = None) -> str:
    """按优先级找 Key；找不到就带着「去哪注册」的提示退出。"""
    if cli_key and _clean_key(cli_key):
        return _clean_key(cli_key)

    for name in KEY_ENV_VARS:
        v = _clean_key(os.environ.get(name, ""))
        if v:
            return v

    for path in KEY_FILE_CANDIDATES:
        try:
            with open(path, encoding="utf-8") as f:
                for line in f:
                    if line.strip() and not line.lstrip().startswith("#"):
                        v = _clean_key(line)
                        if v:
                            return v
        except OSError:
            continue

    die(
        "没找到 twitterapi.io 的 API Key。\n"
        f"  {FREE_TRIAL_NOTE}\n"
        f"  1) 打开 {SIGNUP_URL} 注册并复制 dashboard 上的 API Key\n"
        "  2) 任选一种方式交给脚本：\n"
        '       export TWITTERAPI_IO_KEY="你的key"          # 环境变量\n'
        '       mkdir -p .secrets && echo "你的key" > .secrets/x-api.key   # 密钥文件\n'
        "       python <脚本> --key 你的key                  # 临时传参"
    )


def key_source_hint(cli_key: str | None = None) -> str:
    """只用于打印，说明 Key 是从哪儿来的。"""
    if cli_key:
        return "--key"
    for name in KEY_ENV_VARS:
        if _clean_key(os.environ.get(name, "")):
            return "$" + name
    for path in KEY_FILE_CANDIDATES:
        if os.path.exists(path):
            return path
    return "?"


# --------------------------------------------------------------------------
# HTTP
# --------------------------------------------------------------------------
def build_opener(proxy: str | None = None):
    """默认**直连**。

    为什么默认直连：twitterapi.io 对国内网络可直连，走代理反而容易 403，
    WebSocket 握手还可能被 Cloudflare 拦下。所以这里显式清空代理链，
    只有调用方明确传 --proxy 时才走代理。
    """
    if proxy:
        return urllib.request.build_opener(
            urllib.request.ProxyHandler({"http": proxy, "https": proxy})
        )
    return urllib.request.build_opener(urllib.request.ProxyHandler({}))


def request(
    path: str,
    params: dict | None = None,
    body: dict | None = None,
    method: str | None = None,
    key: str | None = None,
    proxy: str | None = None,
    timeout: int = 30,
    retries: int = 2,
) -> dict:
    """调一个 REST 端点，返回解析后的 JSON。

    429（限速）自动退避重试；401/403 直接给出可执行的提示。
    """
    key = key or load_key()
    url = BASE + path
    if params:
        clean = {k: v for k, v in params.items() if v is not None and v != ""}
        if clean:
            url += "?" + urllib.parse.urlencode(clean)

    data = json.dumps(body).encode("utf-8") if body is not None else None
    headers = {"x-api-key": key, "Accept": "application/json"}
    if data is not None:
        headers["Content-Type"] = "application/json"

    opener = build_opener(proxy)
    last_err = None
    for attempt in range(retries + 1):
        req = urllib.request.Request(url, data=data, headers=headers, method=method)
        try:
            with opener.open(req, timeout=timeout) as r:
                return json.loads(r.read().decode("utf-8", "replace") or "{}")
        except urllib.error.HTTPError as e:
            detail = ""
            try:
                detail = e.read().decode("utf-8", "replace")[:300]
            except Exception:
                pass
            if e.code == 429 and attempt < retries:
                time.sleep(3 * (attempt + 1))
                continue
            if e.code == 402:
                die(
                    "额度不足（HTTP 402）—— 账户 credits 已耗尽，需要充值。\n"
                    f"  去 {SIGNUP_URL} 登录 dashboard 充值后重试。\n"
                    "  充值前可以用 python scripts/xapi.py balance 确认余额。\n"
                    f"  （如果你还没注册过自己的账号，{FREE_TRIAL_NOTE}）"
                )
            if e.code in (401, 403):
                die(
                    f"API Key 被拒绝（HTTP {e.code}）。{detail}\n"
                    f"  确认 Key 可用：打开 {SIGNUP_URL} 登录 dashboard 查看。\n"
                    "  如果你正挂着代理，试试去掉 --proxy（本接口默认直连最稳）。"
                )
            last_err = f"HTTP {e.code} {detail}"
        except urllib.error.URLError as e:
            last_err = f"{type(e).__name__}: {e.reason}"
        except Exception as e:  # noqa: BLE001
            last_err = f"{type(e).__name__}: {e}"
        if attempt < retries:
            time.sleep(1.5)

    die(f"请求失败 {method or 'GET'} {path} —— {last_err}")
    return {}  # 到不了


def get_balance(key: str | None = None, proxy: str | None = None) -> int | None:
    """剩余 credits；**查询失败返回 None**。

    ⚠️ 注意：余额可以是**负数**（欠费）—— 那是合法值，不能和「查不到」混为一谈。
    """
    try:
        d = request("/oapi/my/info", key=key, proxy=proxy, retries=1)
    except SystemExit:
        return None
    v = d.get("recharge_credits")
    try:
        return int(v)
    except (TypeError, ValueError):
        return None


def usd(credits: float) -> str:
    return "$%.4f" % (credits / CREDITS_PER_USD)


def fmt_balance(b: int | None) -> str:
    """把余额渲染成一行，含欠费提示。"""
    if b is None:
        return "查询失败"
    s = f"{b:,} credits ≈ {usd(b)}"
    if b <= 0:
        s += "  ⚠️ 余额已耗尽（负数=欠费），请到 dashboard 充值后才能继续调用"
    return s


# --------------------------------------------------------------------------
# WebSocket
# --------------------------------------------------------------------------
def ws_frame(opcode: int, payload: bytes = b"") -> bytes:
    """构造客户端 → 服务端的帧（RFC6455 要求必须带掩码）。

    opcode: 0x1 文本 / 0x8 close / 0x9 ping / 0xA pong
    """
    mask = os.urandom(4)
    n = len(payload)
    if n < 126:
        head = bytes([0x80 | opcode, 0x80 | n])
    elif n < 65536:
        head = bytes([0x80 | opcode, 0x80 | 126]) + n.to_bytes(2, "big")
    else:
        head = bytes([0x80 | opcode, 0x80 | 127]) + n.to_bytes(8, "big")
    masked = bytes(b ^ mask[i % 4] for i, b in enumerate(payload))
    return head + mask + masked


def ws_connect(key: str, proxy: str | None = None, timeout: int = 25):
    """建立到 twitterapi.io 的 WebSocket 连接（握手用标准库手写）。

    要点（都踩过坑）：
      · `x-api-key` 放在握手 header，不走 query string
      · 一个 Key 只能有一条活跃连接，第二条会被 1008 拒绝
      · 失败时返回 None 并把原因打在 stdout，交给调用方决定何时重连
    """
    import base64

    raw = None
    try:
        if proxy:
            u = urllib.parse.urlparse(proxy)
            raw = socket.create_connection(
                (u.hostname, u.port or 8080), timeout=timeout
            )
            raw.sendall(
                (
                    f"CONNECT {WS_HOST}:{WS_PORT} HTTP/1.1\r\nHost: {WS_HOST}:{WS_PORT}\r\n\r\n"
                ).encode()
            )
            buf = b""
            while b"\r\n\r\n" not in buf:
                chunk = raw.recv(4096)
                if not chunk:
                    break
                buf += chunk
            line = buf.split(b"\r\n")[0].decode("utf-8", "replace")
            if "200" not in line:
                print("WS 代理 CONNECT 失败: " + line, flush=True)
                return None
            sock = raw
        else:
            sock = socket.create_connection((WS_HOST, WS_PORT), timeout=timeout)

        ss = ssl.create_default_context().wrap_socket(sock, server_hostname=WS_HOST)
        handshake = (
            f"GET {WS_PATH} HTTP/1.1\r\n"
            f"Host: {WS_HOST}\r\n"
            "Upgrade: websocket\r\n"
            "Connection: Upgrade\r\n"
            f"Sec-WebSocket-Key: {base64.b64encode(os.urandom(16)).decode()}\r\n"
            "Sec-WebSocket-Version: 13\r\n"
            f"x-api-key: {key}\r\n"
            "User-Agent: x-live-fetch/1.0\r\n\r\n"
        )
        ss.sendall(handshake.encode())
        head = ss.recv(8192).decode("utf-8", "replace").split("\r\n")[0]
        if "101" not in head:
            print("WS 握手失败: " + head, flush=True)
            try:
                ss.close()
            except Exception:
                pass
            return None
        return ss
    except Exception as e:  # noqa: BLE001
        print(f"WS 连接异常: {type(e).__name__}: {str(e)[:160]}", flush=True)
        try:
            if raw is not None:
                raw.close()
        except Exception:
            pass
        return None


def ws_frames_from(buf: bytes):
    """从缓冲区里切出完整的帧，返回 (frames, 剩余缓冲)。

    frames 里每项是 (opcode, payload_bytes)；不完整时把剩余字节还回去，
    下次收到新数据再拼起来继续切（推文正文常被 TCP 分片）。

    服务端下行帧按 RFC6455 **不带掩码**，但这里也兼容带掩码的帧并自动解掩码 ——
    否则遇到掩码帧会**静默解析出乱码**而不是报错，是个很难查的坑。
    """
    frames = []
    i = 0
    n = len(buf)
    while True:
        if n - i < 2:
            break
        op = buf[i] & 0x0F
        masked = bool(buf[i + 1] & 0x80)
        ln = buf[i + 1] & 0x7F
        off = i + 2
        if ln == 126:
            if n - i < 4:
                break
            ln = int.from_bytes(buf[i + 2 : i + 4], "big")
            off = i + 4
        elif ln == 127:
            if n - i < 10:
                break
            ln = int.from_bytes(buf[i + 2 : i + 10], "big")
            off = i + 10
        mask = b""
        if masked:
            if n - off < 4:
                break
            mask = buf[off : off + 4]
            off += 4
        if n - off < ln:
            break
        payload = buf[off : off + ln]
        if masked:
            payload = bytes(b ^ mask[k % 4] for k, b in enumerate(payload))
        frames.append((op, payload))
        i = off + ln
    return frames, buf[i:]


# --------------------------------------------------------------------------
# 时间与文本
# --------------------------------------------------------------------------
_TW_FORMAT = "%a %b %d %H:%M:%S %z %Y"


def parse_created(tw: dict):
    """把推文对象里的时间戳解析成带时区的 datetime；失败返回 None。

    兼容两种来源：
      · REST / 规则推送：`createdAt` = "Sat Mar 15 05:31:28 +0000 2025"
      · Stream 优先通道：`created_ms` / `snowflake_created_ms` = epoch 毫秒
    """
    import datetime as _dt

    s = tw.get("createdAt")
    if s:
        try:
            return _dt.datetime.strptime(s, _TW_FORMAT)
        except Exception:
            return None
    ms = tw.get("created_ms") or tw.get("snowflake_created_ms")
    if ms:
        try:
            return _dt.datetime.fromtimestamp(int(ms) / 1000.0, _dt.timezone.utc)
        except Exception:
            return None
    return None


def human_time(tw: dict, local: bool = True) -> str:
    dt = parse_created(tw)
    if not dt:
        return ""
    if local:
        dt = dt.astimezone()
    return dt.strftime("%Y-%m-%d %H:%M")


def epoch_ms(tw: dict) -> int:
    dt = parse_created(tw)
    try:
        return int(dt.timestamp() * 1000) if dt else 0
    except Exception:
        return 0


def slug(text: str, maxlen: int = 40) -> str:
    """把任意查询串变成安全的目录名。"""
    s = re.sub(r"[^\w\u4e00-\u9fff]+", "-", (text or "").strip().lower())
    s = re.sub(r"-{2,}", "-", s).strip("-")
    return (s or "out")[:maxlen]


def shorten(text: str, n: int = 400) -> str:
    t = re.sub(r"\s+", " ", (text or "").strip())
    return t if len(t) <= n else t[: n - 1] + "…"


def num(n) -> str:
    """1234 → 1.2k"""
    try:
        n = int(n)
    except (TypeError, ValueError):
        return "0"
    if n >= 1_000_000:
        return "%.1fM" % (n / 1_000_000)
    if n >= 1_000:
        return "%.1fk" % (n / 1_000)
    return str(n)


def ensure_dir(path: str) -> str:
    os.makedirs(path, exist_ok=True)
    return path


# --------------------------------------------------------------------------
# 直接当命令行用
# --------------------------------------------------------------------------
def _cli(argv):
    cmd = argv[1] if len(argv) > 1 else "help"
    key = None
    proxy = None
    if "--key" in argv:
        key = argv[argv.index("--key") + 1]
    if "--proxy" in argv:
        proxy = argv[argv.index("--proxy") + 1]

    if cmd == "balance":
        k = load_key(key)
        print(f"Key 来源: {key_source_hint(key)}")
        b = get_balance(k, proxy)
        if b is None:
            die("余额查询失败（Key 无效或网络不通）")
        print(f"剩余额度: {fmt_balance(b)}")
    elif cmd == "rules":
        import rules as _r

        _r.cmd_list(key=key, proxy=proxy)
    else:
        print(
            "用法:\n"
            "  python scripts/xapi.py balance [--key K] [--proxy P]\n"
            "  python scripts/xapi.py rules\n\n"
            f"还没有 Key？去 {SIGNUP_URL} 注册。\n"
            f"{FREE_TRIAL_NOTE}"
        )


if __name__ == "__main__":
    setup_stdout()
    _cli(sys.argv)
