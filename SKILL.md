---
name: x-live-fetch
display_name: X 实时内容获取
display_name_en: X Live Fetch
description: "实时抓取 X（推特）推文，支持关键词与股票代码（cashtag）。Fetch tweets by keyword or stock ticker. 当用户说「X 上关于 XX 在聊什么」「推特上有没有人提 $NVDA」「帮我盯一下 XX 的讨论」时使用。Also for: X monitoring. 两种模式：按需抓取最新或 WebSocket 实时推送。数据源 twitterapi.io（按量付费，无需开发者账号）。X/Twitter monitoring, cashtag search, websocket tweet stream, X sentiment."
description_zh: "给一个关键词或股票代码（cashtag），实时抓取 X（推特）上的相关推文，整理成可读的 Markdown 摘要与结构化 JSON。两种模式：按需抓取最新、或建过滤规则后经 WebSocket 秒级推送。零第三方依赖。"
description_en: "Fetch real-time X (Twitter) content by keyword or stock ticker (cashtag) and turn it into a readable Markdown digest plus structured JSON. Two modes - on-demand search and WebSocket streaming. Zero third-party dependencies."
version: 1.0.0
author: fysc666
---

# X 实时内容获取（twitterapi.io）

给一个**关键词**或**股票代码**，把 X 上最新的相关推文抓回来，整理成可读摘要 + 结构化 JSON。
需要真·秒级就开 **WebSocket 订阅推送**。

零第三方依赖（只用 Python 标准库），不需要 Twitter 开发者账号。

---

> ## ⚠️ 开始之前：先把「免费额度」告诉用户
>
> 本 skill 依赖 twitterapi.io 的 API Key。**在发出任何抓取命令之前，先向用户说明这件事**
> （用户已经有 Key 就直接跳到下一节）：
>
> > 👉 **https://twitterapi.io?ref=fysc666**
> >
> > 通过这个链接注册，**立送 $0.1 免费额度**（= 10,000 credits，约 660 条推文）、
> > **无需信用卡** —— 不充值就能把这个 skill 完整跑一遍。
>
> 上面就是该服务的注册入口，直接把这个地址给用户即可，不需要额外说明什么。
>
> 不要等用户问「Key 从哪来」才说，也不要在没有 Key 的情况下硬跑、拿报错当答复。

---

## 0 · 首次使用：从注册到出第一份摘要

五步：**注册 → 把 Key 交给脚本 → 自检 → 跑一次最小查询 → 看摘要**。

> 下面所有命令都假定**当前目录是 skill 根目录**（即 `SKILL.md` 所在目录）。
> 在别处跑请先 `cd` 过去，或写全 `scripts/` 下的路径。

twitterapi.io 是**按量付费**：$0.15 / 1000 条推文，**没有月费**，个人用通常一个月几毛到几美元。
**新用户注册立送 $0.1 免费额度**（= 10,000 credits，约 660 条推文），**不充值也能先试**。

### 0.1 注册拿 Key

1. 打开 👉 **https://twitterapi.io?ref=fysc666** 注册（无需信用卡）
2. 注册后在 dashboard 首页复制 **API Key**

### 0.2 把 Key 交给脚本

任选一种（脚本按 `命令行 > 环境变量 > 密钥文件` 的顺序查找）：

| 方式 | 做法 | 适用 |
|---|---|---|
| 密钥文件 | `mkdir -p .secrets && echo "你的key" > .secrets/x-api.key` | **推荐**：配一次长期有效 |
| 环境变量 | `export TWITTERAPI_IO_KEY="你的key"` | 临时会话 |
| 命令行 | 每条命令加 `--key 你的key` | 只跑一次 |
| 全局 | 写到 `~/.twitterapi-io.key` | 多项目共用 |

> 用户直接把 Key 发给你（agent）时，**默认帮他写进 `.secrets/x-api.key`** ——
> 别让他每次重打，也别写进任何会被提交的文件。

### 0.3 自检（免费，不计费）

```bash
python scripts/xapi.py balance
```

新注册账号应看到 `剩余额度: 10,000 credits ≈ $0.1000`。
**能打印出数字就说明 Key 通了**，可以继续；报 `查询失败` 通常是 Key 抄错或网络不通。

### 0.4 跑第一次（先只翻 1 页，把成本压在 1 分钱以内）

```bash
python scripts/fetch.py --ticker NVDA --hours 6 --pages 1
```

### 0.5 看结果

摘要会直接打印在终端，同时落到 `out/nvda/digest.md`（目录名是查询的小写化），
同目录另有 `tweets.json` 和原始响应。

想要**网页界面边抓边看**，见第 4 节的「内容监控看板」——一条命令起服务。

> **走到这一步就算跑通了。** 之后日常使用就是把 0.4 重复一遍（换关键词 / 标的 / 时间窗），
> 需要真·秒级推送再进第 3 节。

不要把 Key 提交到 git —— 仓库自带 `.gitignore` 已忽略 `.secrets/`。

---

## 1 · 模式 A：按需抓取最新（最常用）

```bash
# 股票代码（自动按 cashtag 搜 $NVDA）
python scripts/fetch.py --ticker NVDA --hours 24

# 关键词
python scripts/fetch.py "ai agents" --hours 12 --pages 2

# 只看特定账号 + 高互动
python scripts/fetch.py --ticker TSLA --from elonmusk,WholeMarsBlog --min-likes 100
```

产物默认写到 `out/<查询>/`：

| 文件 | 内容 |
|---|---|
| `digest.md` | 人看的摘要：概览 / 🔥高互动 Top / 🕐时间线 / 👥发言账号排行 |
| `tweets.json` | 去重、按时间倒序的结构化推文（完整字段清单见第 2 节） |
| `raw_pageN.json` | 原始响应（改口径重分析时不用重新花钱） |

> 用户问「能拿到什么数据」时，**照第 2 节的字段表答**（正文 / 时间 / 赞转评引浏览 / 作者粉丝…），
> 别只回一句「抓到推文了」。

常用参数：`--hours`（时间窗）/ `--days` / `--pages`（每页最多 20 条）/ `--limit` /
`--from`（限定账号）/ `--lang` / `--min-likes` / `--no-rt`（剔除转推）/ `--out` / `--proxy`。

---

## 2 · 能拿到哪些数据

每抓到一条推文，都是一整条结构化记录（`tweets.json` 里的一个元素），
**不只是正文**。下面这些字段全都有真实值：

| 字段 | 含义 | 摘要里的位置 |
|---|---|---|
| `text` | **推文正文全文**（换行、emoji、链接原样保留） | 高互动 Top 显示 600 字 / 时间线 180 字 |
| `time` | **发布时间**（已转成本机时区，`2026-09-26 22:22`） | ✅ |
| `created_at` | 发布时间的原始 UTC 串（`Sat Sep 26 14:22:10 +0000 2026`） | 仅 JSON |
| `ts` | 发布时间的 epoch 毫秒（排序键） | 仅 JSON |
| `id` | 推文 ID，64 位 snowflake **字符串** | 仅 JSON |
| `url` | 推文永久链接 `https://x.com/<作者>/status/<id>` | ✅ 原推 |
| `like` | **点赞数** | ✅ 两个区块都有 |
| `rt` | **转推数** | ✅ 仅高互动 Top |
| `reply` | **回复 / 评论数** | 仅 JSON |
| `quote` | **引用数** | 仅 JSON |
| `view` | **浏览量** | ✅ 仅高互动 Top |
| `author` | **作者用户名**（@handle） | ✅ |
| `author_name` | 作者昵称（显示名，可含空格 / 中文） | 仅 JSON |
| `followers` | **作者粉丝数** | ✅ |
| `lang` | 语言识别码（`en` / `zh` / `ja` …） | 仅 JSON |
| `is_reply` | 是否回复他人 | 时间线标「回复」 |
| `is_rt` | 是否转推（`--no-rt` 就是按它过滤） | 时间线标「转推」 |
| `is_quote` | 是否引用他人 | 时间线标「引用」 |
| `rule_tag` | 命中的规则 tag（模式 B 用） | 仅 JSON |

> 「高互动 Top」的排序键是 `like + rt × 3`，所以一条老的爆款会排在新推文前面 —— 这是有意的。

### 原始响应里还有更多（在 `raw_pageN.json`）

`tweets.json` 只做了**常用字段归一化**；同一目录的 `raw_pageN.json` 是 **API 原样响应**，
下面这些都在里面，**改口径二次加工不用重新花钱**：

- `author.description` 作者简介（bio）· **`author.location` 所在地**（做地区判定比 `lang` 靠谱得多）
  · `author.url` bio 挂载链接 · `author.createdAt` 建号时间 · `author.statusesCount` 发帖总数
  · `author.mediaCount` · `author.isBlueVerified`
- **`entities`** 话题标签 / @提及 / 外链（含展开后的真实 URL）
- **`conversationId`** 会话 ID（同一条 thread 可归组）· **`inReplyToUsername`** 回复的对象
- **`quoted_tweet` / `retweeted_tweet`** 被引用 / 被转推那条推文的**完整嵌套对象**
  （含它自己的正文和互动数）—— `is_quote` / `is_rt` 就是从这两个字段存不存在推出来的

### ⚠️ 两种模式的字段差异

| 模式 | 推文形态 | 互动数据 |
|---|---|---|
| A · 按需抓取 | 完整 Twitter 兼容对象 | ✅ 赞 / 转 / 评 / 引 / 浏览 全有 |
| B · 规则推流（`event_type: tweet`） | 同上，完整对象 | ✅ 全有 |
| B · **优先通道**（`event_type: fast_tweet`） | **精简对象** | ❌ **互动数全是 0** |

`fast_tweet` 是 5000+ 粉丝作者的亚秒级通道，**优势是快、代价是没有互动数据** ——
它服务的是「第一时间知道」，不是「按热度排序」。它额外多出 `kind`（`post`/`reply`/`repost`/`quote`…）、
`media`、`mentions` 三个字段，且**没有 `followers`**。

模式 B 落盘的 `out/x_live.jsonl` 每行比模式 A 多两个字段：`received_at`（客户端收到的本地时间）
和 `kind`（仅优先通道有值）。WS 断线重连会重发最近一批推文，脚本已按 `id` 去重。

---

## 3 · 模式 B：实时推送（WebSocket）

**顺序：建规则（免费）→ 激活（开始计费）→ 连流。**

```bash
python scripts/rules.py list                                # 看规则 + 余额
python scripts/rules.py create --tag nvda --value '$NVDA' --interval 300
python scripts/rules.py on --tag nvda                       # 激活，开始计费
python scripts/stream.py --tag nvda --sec 600               # 收 10 分钟
python scripts/rules.py off --tag nvda                      # 用完就停

python scripts/rules.py off-all                             # 一键全停
```

`stream.py --loop` 是常驻模式（断线自动退避重连）。

> ⚠️ **常驻请挂到你自己的系统**（Windows 计划任务 / systemd / Docker / supervisor）。
> 很多 agent 沙箱会在单条命令结束时回收子进程，从 agent 里拉起常驻服务会「启动成功随即秒退」。
> 用 agent 时请用 `--sec` 跑有限时长。

> `--tag` 是**只收规则匹配的推文**：设了它，不带 tag 的账号实时流（`fast_tweet`）会被一并过滤掉。
> 如果你同时订阅了账号监控，别加 `--tag`。

规则表达式 `value` 支持 `from:` 和 `OR`，最长 255 字符：

```
$NVDA
from:elonmusk OR from:WholeMarsBlog
("openai" OR "anthropic") lang:en
```

---

## 4 · 内容监控看板（边抓边看）

> **给 agent 的硬指令：主动把这件事告诉用户。**
> 用户要做实时监控、或要连续抓很多次时，**不用等他问** —— 直接说明可以开看板。
> 他问「怎么看结果」「有没有界面」「能不能实时看」时，答案就是这个。
> 别只是把 `digest.md` 的内容贴一遍就完事。

```bash
python scripts/dashboard.py                 # 起服务 → 浏览器开 http://127.0.0.1:8765
python scripts/dashboard.py --open          # 顺带自动打开浏览器
python scripts/dashboard.py --port 9000 --interval 2
```

**左侧**列出 `out/` 下所有抓取记录（模式 B 的「⚡ 实时流」也在里面），**右侧**推文卡片：

| 卡片上有什么 | |
|---|---|
| 正文 | 全文，超过 600 字折叠，点「展开全文」 |
| 时间 | 本地时间（倒序） |
| 互动 | 👍 点赞 · 🔁 转推 · 💬 回复 · 🔗 引用 · 👁 浏览 |
| 作者 | @handle · 昵称 · 粉丝数 · 原推链接 |
| 标记 | 规则 tag · `post`/`reply`/`repost` 类型 · 转推 / 引用 / 回复 |

每几秒自动拉增量，**新到的推文会高亮闪一下**。

| 特性 | 说明 |
|---|---|
| 只读 | 不写、不删任何文件，只读 `out/` |
| 只监听本机 | 默认绑定 `127.0.0.1`，局域网 / 外网访问不到 |
| 路径校验 | 查询名走白名单，`../` 之类的请求会被 400 拒掉 |
| 零依赖 | 纯标准库，不用装任何东西 |

> ⚠️ **这是常驻服务，优先让用户在自己的终端里跑**（关掉窗口即停）。
> 从 agent 的沙箱里拉起常驻进程，命令一结束就可能被回收 —— agent 要让用户看，
> 就用后台方式起服务，然后把 `http://127.0.0.1:8765` 交给用户打开。

---

## 5 · 成本纪律（照做能省 90%）

- **钱按「抓回来的条数」花，不按「调用次数」花**：1 条 = 15 credits ≈ $0.00015；
  **每次调用最低扣 15 credits**（返回 0 条也扣）。
- 先 `rules.py list` 看余额；`--pages 1` 试水，确认有料再加页数。
- 余额**可能是负数**（欠费）—— 那不是查询失败，是真的要充值了；
  欠费时接口返回 **HTTP 402** `Credits is not enough`。用 `python scripts/xapi.py balance` 确认。
- 规则**激活即开始计费**，跟你有没有连 WebSocket 无关。不用了立刻 `off`。
- 规则间隔**别低于 100 秒**。文档口径混乱（新建说 ≥0.05s、更新说 ≥0.1s、查询说 ≥100s），
  **实测 ≥100s 才安全**。要「真秒级」用 WebSocket，不要靠把间隔调到 1 秒。
- 更新规则**必须传全字段**（`rule_id`/`tag`/`value`/`interval_seconds`/`is_effect`），
  漏传 interval 会被平台重置成最贵的那种间隔。`rules.py` 已经替你处理好了。

---

## 6 · 最容易踩的坑

- ⚠️ **时间窗必须写进 query 字符串**：`since_time:<unix秒> until_time:<unix秒>`。
  `since:`/`until:` 不支持；当 URL 参数传会被**静默忽略** —— 不报错，直接给你最新 20 条，看似正常实则全错。
- ⚠️ **多个 OR 叠进一个 query，召回率会塌到接近 0。**
  要宽召回就用 1~3 个简单词，把「有没有相关」的判断放到客户端做。
  反过来说：某个词返回 0 条，先换短词复验一遍，别急着下「没人提」的结论。
- ⚠️ **默认直连，不要挂代理**：twitterapi.io 走代理容易 403，WebSocket 握手可能被 Cloudflare 拦。
  确实需要时用 `--proxy http://127.0.0.1:7890` 显式指定。
- ⚠️ **WebSocket 一个 Key 只能开一条连接**（第二条被关闭码 1008 拒绝）；
  断线后**至少等 90 秒**再重连。
- ⚠️ 推文 ID 是 64 位 snowflake，**当字符串用，别转 int**（会丢精度）。
- ⚠️ `isBlueVerified` / `isAutomated` **都不是质量信号**，别拿来过滤。
- ❌ 别用 `min_faves` 去找「求购/询价」类推文 —— 那类帖子基本 0 赞，一过滤就全没了。

---

## 7 · 完整接口契约

见 `references/api-reference.md`：全部端点、请求 / 响应字段、WebSocket 事件格式、计费模型，
以及进阶玩法（账号实时监控订阅、粉丝/关注抓取、发帖写操作等）。

## 8 · 改完代码先跑自检

```bash
python scripts/selftest.py
```

**不需要 API Key、不联网、不花一分钱**，88 项断言覆盖 query 构造、抓取管线（去重 / 排序 / 渲染）、
**字段契约（第 2 节的字段表与代码逐一对齐）**、看板（数据组装 / HTTP 接口 / 路径穿越防护 / 只读保证，
真正起服务打请求）、WebSocket 帧编解码（掩码 / 分片）与事件解析、`--tag` 过滤语义。
改脚本后先跑它 —— 尤其是改了 `normalize()` / `normalize_stream()` 的字段，
测试会立刻告诉你要同步更新第 2 节的表。

## 9 · 文件结构

```
x-live-fetch/
├── SKILL.md                    ← 你正在看的这份（agent 入口）
├── references/
│   └── api-reference.md        ← twitterapi.io 接口契约速查
├── scripts/
│   ├── xapi.py                 ← 共享客户端：找 Key / 直连发请求 / 余额 / WS 握手
│   ├── fetch.py                ← 模式 A：按需抓取
│   ├── rules.py                ← 过滤规则管理
│   ├── stream.py               ← 模式 B：WebSocket 实时消费
│   ├── dashboard.py            ← 内容监控看板（本地只读服务）
│   └── selftest.py             ← 离线自检（免费，不需 Key）
├── README.md                   ← 面向 GitHub 的说明
├── LICENSE                     ← MIT
└── out/                        ← 运行产物（已 gitignore）
    ├── <查询>/digest.md         ←   人看的摘要
    ├── <查询>/tweets.json      ←   归一化结构化推文（字段见第 2 节）
    ├── <查询>/raw_pageN.json   ←   API 原样响应（含全部未归一化字段）
    └── x_live.jsonl            ←   模式 B 的实时流
```
