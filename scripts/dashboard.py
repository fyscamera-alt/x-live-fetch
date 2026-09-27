# -*- coding: utf-8 -*-
"""内容监控看板：起一个只读的本地网页，实时看「抓到了什么」。

    python scripts/dashboard.py                  # http://127.0.0.1:8765
    python scripts/dashboard.py --open           # 同时自动打开浏览器
    python scripts/dashboard.py --port 9000 --interval 2

它做什么：
  · 自动发现 out/ 下所有抓取产物（各查询目录的 tweets.json）
  · 若有 out/x_live.jsonl（模式 B 的实时流）则把它作为「⚡ 实时流」一并展示
  · 页面每 N 秒自动拉增量，新进来的推文高亮

安全边界（重要）：
  · **只读** —— 不写、不删任何文件
  · 默认只监听 **127.0.0.1**，局域网 / 外网访问不到
  · 查询名经过白名单校验，挡掉 `../` 之类的路径穿越
  · 零第三方依赖，只用 Python 标准库

⚠️ 这是常驻服务：请在你的本机终端里跑（关掉窗口即停止）。
   从 agent 的沙箱里拉起常驻进程，命令一结束就可能被回收。
"""
from __future__ import annotations

import argparse
import http.server
import json
import os
import sys
import time
import urllib.parse

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import xapi  # noqa: E402

DEFAULT_PORT = 8765
LIVE_FILE = "x_live.jsonl"
MAX_LIVE = 300          # 实时流最多回传多少条给前端渲染
PREVIEW = 600           # 前端折叠前展示的正文字符数


# --------------------------------------------------------------------------
# 数据读取（带 mtime 缓存，避免每次轮询都全量解析）
# --------------------------------------------------------------------------
_CACHE: dict[str, tuple] = {}


def load_json(path: str):
    """读 JSON，按 (mtime, size) 缓存；文件没变就直接复用。"""
    try:
        st = os.stat(path)
    except OSError:
        return None
    sig = (st.st_mtime_ns, st.st_size)
    hit = _CACHE.get(path)
    if hit and hit[0] == sig:
        return hit[1]
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, json.JSONDecodeError, UnicodeDecodeError):
        return None
    _CACHE[path] = (sig, data)
    return data


def read_jsonl(path: str, limit: int = MAX_LIVE):
    """读 jsonl 的尾部若干条，返回 (items, total)。坏行直接跳过。"""
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            lines = f.read().splitlines()
    except OSError:
        return [], 0
    total = len(lines)
    items = []
    for idx, line in enumerate(lines):
        if idx < max(0, total - limit):
            continue
        line = line.strip()
        if not line:
            continue
        try:
            rec = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(rec, dict):
            rec["_idx"] = idx
            items.append(rec)
    return items, total


def safe_name(outdir: str, name: str) -> str | None:
    """只接受 outdir 下真实存在的子目录名，挡掉路径穿越。"""
    if not name or name != os.path.basename(name) or name in (".", ".."):
        return None
    base = os.path.abspath(outdir)
    full = os.path.abspath(os.path.join(base, name))
    if not full.startswith(base + os.sep):
        return None
    return name if os.path.isdir(full) else None


# --------------------------------------------------------------------------
# 组装接口数据
# --------------------------------------------------------------------------
def scan_queries(outdir: str) -> list[dict]:
    """列出 out/ 下所有有 tweets.json 的查询目录，最新的排前面。"""
    try:
        names = sorted(os.listdir(outdir))
    except OSError:
        return []

    rows = []
    for name in names:
        d = load_json(os.path.join(outdir, name, "tweets.json"))
        if not isinstance(d, dict) or "tweets" not in d:
            continue
        tweets = d.get("tweets") or []
        acts = 0
        for t in tweets:
            try:
                acts += int(t.get("like") or 0) + int(t.get("rt") or 0)
            except (TypeError, ValueError):
                pass
        rows.append({
            "name": name,
            "label": d.get("label") or name,
            "query": d.get("query") or "",
            "fetched_at": d.get("fetched_at") or "",
            "count": len(tweets),
            "engagement": acts,
        })
    rows.sort(key=lambda r: (r["fetched_at"], r["count"]), reverse=True)
    return rows


def state(outdir: str, interval: int = 3) -> dict:
    live_path = os.path.join(outdir, LIVE_FILE)
    live_total = 0
    if os.path.isfile(live_path):
        try:
            with open(live_path, encoding="utf-8", errors="replace") as f:
                live_total = sum(1 for _ in f)
        except OSError:
            live_total = 0
    return {
        "outdir": os.path.abspath(outdir),
        "now": time.strftime("%Y-%m-%d %H:%M:%S"),
        "interval": max(1, int(interval)),
        "queries": scan_queries(outdir),
        "live": {
            "file": live_path if os.path.isfile(live_path) else "",
            "total": live_total,
        },
        "preview": PREVIEW,
    }


def query_payload(outdir: str, name: str) -> dict:
    safe = safe_name(outdir, name)
    if not safe:
        return {"error": "无效的查询名"}
    d = load_json(os.path.join(outdir, safe, "tweets.json"))
    if not isinstance(d, dict):
        return {"error": "读不到 tweets.json"}
    return {
        "name": safe,
        "label": d.get("label") or safe,
        "query": d.get("query") or "",
        "fetched_at": d.get("fetched_at") or "",
        "count": len(d.get("tweets") or []),
        "tweets": d.get("tweets") or [],
    }


def live_payload(outdir: str) -> dict:
    path = os.path.join(outdir, LIVE_FILE)
    if not os.path.isfile(path):
        return {"error": "还没有实时流（模式 B 跑起来后才有）", "total": 0, "items": []}
    items, total = read_jsonl(path)
    return {"file": path, "total": total, "items": items}


# --------------------------------------------------------------------------
# HTTP
# --------------------------------------------------------------------------
class Handler(http.server.BaseHTTPRequestHandler):
    server_version = "x-live-fetch-dashboard/1.0"
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt, *args):  # 默认太吵，只在 --verbose 时输出
        if getattr(self.server, "verbose", False):
            sys.stderr.write("  [dash] %s\n" % (fmt % args))

    # -- helpers --
    def _send(self, code: int, ctype: str, body: bytes):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        try:
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def _json(self, obj, code: int = 200):
        self._send(code, "application/json; charset=utf-8",
                   json.dumps(obj, ensure_ascii=False).encode("utf-8"))

    def do_GET(self):
        u = urllib.parse.urlparse(self.path)
        q = urllib.parse.parse_qs(u.query)
        try:
            if u.path in ("/", "/index.html"):
                return self._send(200, "text/html; charset=utf-8", PAGE.encode("utf-8"))
            if u.path == "/api/health":
                return self._json({"ok": True, "now": time.strftime("%Y-%m-%d %H:%M:%S")})
            if u.path == "/api/state":
                return self._json(state(self.server.outdir,
                                        getattr(self.server, "interval", 3)))
            if u.path == "/api/query":
                name = (q.get("name") or [""])[0]
                if not safe_name(self.server.outdir, name):
                    return self._json({"error": "无效的查询名"}, 400)
                return self._json(query_payload(self.server.outdir, name))
            if u.path == "/api/live":
                return self._json(live_payload(self.server.outdir))
            return self._send(404, "text/plain; charset=utf-8", b"not found")
        except Exception as e:  # noqa: BLE001
            return self._json({"error": "%s: %s" % (type(e).__name__, e)}, 500)


# --------------------------------------------------------------------------
# 页面（内嵌，避免多文件；深色，长时间盯屏不累）
# --------------------------------------------------------------------------
PAGE = r"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>X 内容监控看板</title>
<style>
:root{
  --bg:#0d1117; --panel:#161b22; --panel2:#1c2128; --line:#30363d;
  --fg:#e6edf3; --dim:#8b949e; --accent:#58a6ff;
  --like:#f778ba; --rt:#3fb950; --rp:#58a6ff; --qt:#d29922; --vw:#8b949e;
}
*{box-sizing:border-box}
body{margin:0;height:100vh;display:flex;flex-direction:column;background:var(--bg);
  color:var(--fg);font:14px/1.6 -apple-system,"Segoe UI","Microsoft YaHei",sans-serif}
header{padding:12px 18px;border-bottom:1px solid var(--line);background:var(--panel);
  display:flex;align-items:baseline;gap:14px;flex-wrap:wrap}
h1{font-size:16px;margin:0;font-weight:600}
header .meta{color:var(--dim);font-size:12px}
header code{background:var(--panel2);padding:1px 6px;border-radius:4px;color:var(--accent)}
main{flex:1;display:flex;min-height:0}
aside{width:268px;flex:none;border-right:1px solid var(--line);overflow:auto;background:var(--panel)}
aside .t{padding:10px 14px;font-size:11px;letter-spacing:.08em;color:var(--dim);
  text-transform:uppercase;border-bottom:1px solid var(--line)}
.q{padding:10px 14px;border-bottom:1px solid var(--line);cursor:pointer;transition:background .15s}
.q:hover{background:var(--panel2)}
.q.on{background:var(--panel2);box-shadow:inset 3px 0 0 var(--accent)}
.q .n{font-weight:600;display:flex;justify-content:space-between;gap:8px}
.q .c{color:var(--accent);font-variant-numeric:tabular-nums}
.q .s{color:var(--dim);font-size:12px;margin-top:2px}
section{flex:1;overflow:auto;padding:16px 18px}
.empty{color:var(--dim);padding:40px 0;text-align:center}
.tw{background:var(--panel);border:1px solid var(--line);border-radius:10px;
  padding:12px 14px;margin-bottom:10px}
.tw.new{border-color:var(--accent);animation:pop .7s ease}
@keyframes pop{from{background:#1f6feb33}to{background:var(--panel)}}
.tw .hd{display:flex;align-items:baseline;gap:8px;flex-wrap:wrap;font-size:13px}
.tw .au{font-weight:600;color:var(--accent)}
.tw .nm{color:var(--dim)}
.tw .tm{margin-left:auto;color:var(--dim);font-variant-numeric:tabular-nums}
.tw .tx{margin:8px 0;white-space:pre-wrap;word-break:break-word}
.tw .tx .more{display:none}
.tw .tx a.expand{margin-left:4px;font-size:12px}
.tw .st{display:flex;gap:14px;align-items:center;font-size:12.5px;
  color:var(--dim);font-variant-numeric:tabular-nums;flex-wrap:wrap}
.tw .st b{font-weight:600}
.like{color:var(--like)}.rt{color:var(--rt)}.rp{color:var(--rp)}
.qt{color:var(--qt)}.vw{color:var(--vw)}
.tw a{color:var(--accent);text-decoration:none;margin-left:auto}
.tw a:hover{text-decoration:underline}
.tags{margin-top:6px;display:flex;gap:6px;flex-wrap:wrap}
.tag{font-size:11px;padding:1px 7px;border-radius:999px;border:1px solid var(--line);color:var(--dim)}
.tag.k{border-color:var(--accent);color:var(--accent)}
.banner{background:var(--panel);border:1px solid var(--line);border-radius:10px;
  padding:14px 16px;margin-bottom:14px;color:var(--dim);font-size:13px}
.banner b{color:var(--fg)}
</style>
</head>
<body>
<header>
  <h1>X 内容监控看板</h1>
  <span class="meta">数据目录 <code id="outdir">…</code></span>
  <span class="meta">每 <span id="iv">3</span>s 自动刷新 · 上次更新 <span id="upd">—</span></span>
</header>
<main>
  <aside>
    <div class="t">抓取记录</div>
    <div id="list"></div>
  </aside>
  <section id="feed"><div class="empty">正在加载…</div></section>
</main>
<script>
var CUR = null, TIMER = null, IV = 3, LAST = {}, PREVIEW = 600, FIRST = true, KEY = '';

function fmt(n){
  n = Number(n)||0;
  if (n >= 1e6) return (n/1e6).toFixed(1)+'M';
  if (n >= 1e3) return (n/1e3).toFixed(1)+'k';
  return String(n);
}
function esc(s){
  return String(s==null?'':s).replace(/[&<>"']/g, function(c){
    return {'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c];
  });
}
function ago(t){
  if (!t) return '';
  var d = (Date.now() - new Date(t.replace(/-/g,'/')).getTime())/1000;
  if (isNaN(d)) return t;
  if (d < 60) return Math.max(0,Math.round(d))+' 秒前';
  if (d < 3600) return Math.round(d/60)+' 分钟前';
  if (d < 86400) return Math.round(d/3600)+' 小时前';
  return Math.round(d/86400)+' 天前';
}
function textHtml(t){
  var s = String(t.text == null ? '' : t.text);
  if (s.length <= PREVIEW) return '<div class="tx">' + esc(s) + '</div>';
  var uid = 'x' + Math.random().toString(36).slice(2, 8);
  return '<div class="tx">' + esc(s.slice(0, PREVIEW))
    + '<span class="more" id="' + uid + '">' + esc(s.slice(PREVIEW)) + '</span>'
    + '<a href="#" class="expand" data-t="' + uid + '">…展开全文</a></div>';
}
function card(t, isNew){
  var flags = [];
  if (t.rule_tag) flags.push('<span class="tag k">'+esc(t.rule_tag)+'</span>');
  if (t.kind) flags.push('<span class="tag">'+esc(t.kind)+'</span>');
  if (t.is_rt) flags.push('<span class="tag">转推</span>');
  if (t.is_quote) flags.push('<span class="tag">引用</span>');
  if (t.is_reply) flags.push('<span class="tag">回复</span>');
  if (t.lang) flags.push('<span class="tag">'+esc(t.lang)+'</span>');
  if (t.received_at) flags.push('<span class="tag">收到 '+esc(t.received_at.slice(11))+'</span>');
  var url = t.url || (t.author ? 'https://x.com/'+encodeURIComponent(t.author) : '');
  return '<article class="tw'+(isNew?' new':'')+'">'
   + '<div class="hd"><span class="au">@'+esc(t.author||'?')+'</span>'
   + (t.author_name && t.author_name !== t.author ? '<span class="nm">'+esc(t.author_name)+'</span>' : '')
   + (t.followers ? '<span class="nm">'+fmt(t.followers)+' 粉丝</span>' : '')
   + '<span class="tm">'+esc(t.time||'')+'</span></div>'
   + textHtml(t)
   + '<div class="st">'
   +   '<span class="like">👍 <b>'+fmt(t.like)+'</b></span>'
   +   '<span class="rt">🔁 <b>'+fmt(t.rt)+'</b></span>'
   +   '<span class="rp">💬 <b>'+fmt(t.reply)+'</b></span>'
   +   '<span class="qt">🔗 <b>'+fmt(t.quote)+'</b></span>'
   +   '<span class="vw">👁 <b>'+fmt(t.view)+'</b></span>'
   +   (url ? '<a href="'+esc(url)+'" target="_blank" rel="noreferrer">原推 ↗</a>' : '')
   + '</div>'
   + (flags.length ? '<div class="tags">'+flags.join('')+'</div>' : '')
   + '</article>';
}
function cmp(a, b){
  var A = (a.like||0) + (a.rt||0)*3, B = (b.like||0) + (b.rt||0)*3;
  return B - A;
}

function feedParts(){
  var feed = document.getElementById('feed');
  if (feed.dataset.ready !== '1'){
    feed.innerHTML = '<div id="banner"></div><div id="cards"></div>';
    feed.dataset.ready = '1';
  }
  return [document.getElementById('banner'), document.getElementById('cards')];
}

async function tick(){
  var st;
  try { st = await (await fetch('/api/state')).json(); } catch(e){ return; }
  if (!TIMER){
    IV = st.interval || 3;
    TIMER = setInterval(tick, IV * 1000);
  }
  document.getElementById('iv').textContent = IV;
  document.getElementById('outdir').textContent = st.outdir;
  document.getElementById('upd').textContent = st.now;

  var html = '';
  st.queries.forEach(function(q){
    html += '<div class="q'+(CUR===q.name?' on':'')+'" data-n="'+esc(q.name)+'">'
      + '<div class="n"><span>'+esc(q.label)+'</span><span class="c">'+q.count+'</span></div>'
      + '<div class="s">'+esc(q.fetched_at)+' · 互动 '+fmt(q.engagement)+'</div></div>';
  });
  if (st.live.total){
    html += '<div class="q'+(CUR==='@@live'?' on':'')+'" data-n="@@live">'
      + '<div class="n"><span>⚡ 实时流</span><span class="c">'+st.live.total+'</span></div>'
      + '<div class="s">'+esc((st.live.file||'').split(/[\\/]/).pop())+'</div></div>';
  }
  document.getElementById('list').innerHTML = html ||
    '<div class="s" style="padding:14px">还没有抓取产物。<br>先跑一次 fetch.py。</div>';
  PREVIEW = st.preview || 600;

  Array.prototype.forEach.call(document.querySelectorAll('.q'), function(el){
    el.onclick = function(){ CUR = el.dataset.n; LAST = {}; FIRST = true; tick(); };
  });

  if (!CUR){
    var first = st.queries[0];
    if (first){ CUR = first.name; return tick(); }
    if (st.live.total){ CUR = '@@live'; return tick(); }
    document.getElementById('feed').innerHTML =
      '<div class="banner"><b>还没有可看的内容。</b><br>'
      + '先用 <code>python scripts/fetch.py --ticker NVDA --hours 6</code> 抓一次，'
      + '或开模式 B 的实时流，这里就会自动出现。</div>';
    return;
  }

  var d;
  if (CUR === '@@live'){
    try { d = await (await fetch('/api/live')).json(); } catch(e){ return; }
    var pL = feedParts(), bnL = pL[0], cdL = pL[1];
    if (d.error){ cdL.dataset.key=''; bnL.innerHTML='';
      cdL.innerHTML = '<div class="empty">'+esc(d.error)+'</div>'; return; }
    var items = (d.items||[]).slice().sort(function(a,b){ return (b._idx||0)-(a._idx||0); });
    bnL.innerHTML = '<div class="banner">⚡ 实时流 · 累计 <b>'+d.total+'</b> 条（显示最新 '
      + items.length + ' 条）· 最后收到 <b>'
      + esc(items.length ? ago(items[0].received_at || items[0].time) : '—')+'</b></div>';
    var kL = '@@live|' + d.total + '|' + items.length;
    if (cdL.dataset.key === kL) return;      // 没有新数据 → 不动 DOM，保留展开状态
    cdL.dataset.key = kL;
    var seen = LAST['@@live'] || (LAST['@@live'] = {});
    cdL.innerHTML = items.length ? items.map(function(t){
      var isNew = !FIRST && !seen[t.id];
      if (t.id) seen[t.id] = 1;
      return card(t, isNew);
    }).join('') : '<div class="empty">流文件是空的</div>';
  } else {
    try { d = await (await fetch('/api/query?name='+encodeURIComponent(CUR))).json(); }
    catch(e){ return; }
    var pA = feedParts(), bnA = pA[0], cdA = pA[1];
    if (d.error){ cdA.dataset.key=''; bnA.innerHTML='';
      cdA.innerHTML = '<div class="empty">'+esc(d.error)+'</div>'; return; }
    var tws = (d.tweets||[]).slice().sort(cmp);
    bnA.innerHTML = '<div class="banner"><b>'+esc(d.label)+'</b> · 命中 <b>'+d.count
      + '</b> 条 · 抓取于 '+esc(d.fetched_at)
      + '<br><span style="font-size:12px">按 赞 + 转推×3 排序</span></div>';
    var kA = CUR + '|' + tws.length + '|' + ((tws[0]||{}).id||'') + '|'
             + ((tws[tws.length-1]||{}).id||'');
    if (cdA.dataset.key === kA) return;
    cdA.dataset.key = kA;
    var seen2 = LAST[CUR] || (LAST[CUR] = {});
    cdA.innerHTML = tws.length ? tws.map(function(t){
      var isNew = !FIRST && !seen2[t.id];
      if (t.id) seen2[t.id] = 1;
      return card(t, isNew);
    }).join('') : '<div class="empty">这个查询没有结果</div>';
  }
  FIRST = false;
}

// 长正文的「展开全文」用事件委托，卡片重建后依然有效
document.addEventListener('click', function(e){
  var el = e.target;
  while (el && el !== document.body){
    if (el.classList && el.classList.contains('expand')){
      e.preventDefault();
      var more = document.getElementById(el.getAttribute('data-t'));
      if (more) more.style.display = 'inline';
      if (el.remove) el.remove();
      return;
    }
    el = el.parentNode;
  }
});

tick();
</script>
</body>
</html>
"""


# --------------------------------------------------------------------------
def main():
    xapi.setup_stdout()
    p = argparse.ArgumentParser(
        description="内容监控看板：本地起一个只读网页，实时看抓到的推文",
        epilog="只读 out/ 目录，默认只监听 127.0.0.1。Ctrl+C 停止。",
    )
    p.add_argument("--port", type=int, default=DEFAULT_PORT, help=f"端口，默认 {DEFAULT_PORT}")
    p.add_argument("--host", default="127.0.0.1",
                   help="监听地址，默认 127.0.0.1（仅本机）；改成 0.0.0.0 会暴露到局域网，慎用")
    p.add_argument("--out", default="out", help="产物根目录，默认 out/")
    p.add_argument("--interval", type=int, default=3, help="页面自动刷新间隔（秒），默认 3")
    p.add_argument("--open", action="store_true", help="启动后自动打开浏览器")
    p.add_argument("--verbose", action="store_true", help="打印每个 HTTP 请求")
    a = p.parse_args()

    outdir = os.path.abspath(a.out)
    os.makedirs(outdir, exist_ok=True)

    try:
        srv = http.server.ThreadingHTTPServer((a.host, a.port), Handler)
    except OSError as e:
        xapi.die(f"端口 {a.port} 起不来（{e}）。换一个：--port 8899")
        return
    srv.outdir = outdir                     # type: ignore[attr-defined]
    srv.verbose = a.verbose                 # type: ignore[attr-defined]
    srv.interval = max(1, a.interval)       # type: ignore[attr-defined]

    url = f"http://{a.host}:{a.port}/"
    qs = scan_queries(outdir)
    live = os.path.isfile(os.path.join(outdir, LIVE_FILE))
    print(f"内容监控看板已启动：{url}")
    print(f"  数据目录：{outdir}")
    print(f"  已发现 {len(qs)} 份抓取记录" + (f"（{', '.join(q['label'] for q in qs[:5])}…）" if len(qs) > 5
                                         else (f"（{', '.join(q['label'] for q in qs)}）" if qs else "")))
    print(f"  实时流  ：{'已找到 out/' + LIVE_FILE if live else '暂无（模式 B 跑起来后出现）'}")
    print(f"  刷新间隔：{a.interval}s · 只读 · Ctrl+C 停止")
    print("  提示：页面每几秒自动拉增量，新到的推文会高亮。", flush=True)

    if a.open:
        try:
            import webbrowser
            webbrowser.open(url)
        except Exception:  # noqa: BLE001
            pass

    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print("\n看板已停止。")
    finally:
        srv.server_close()


if __name__ == "__main__":
    main()
