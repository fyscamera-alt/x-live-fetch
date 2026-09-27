# x-live-fetch

[![CI](https://github.com/fyscamera-alt/x-live-fetch/actions/workflows/ci.yml/badge.svg)](https://github.com/fyscamera-alt/x-live-fetch/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Python](https://img.shields.io/badge/python-3.8%2B-blue.svg)](https://www.python.org/)

[English](README.md) | 简体中文

**给一个关键词或股票代码，实时抓取 X（推特）上的内容。**

一个即插即用的 AI Agent Skill。零第三方依赖（纯 Python 标准库），零配置（一个 API Key 搞定），
不需要 Twitter 开发者账号、不用等审批。

```
用户：X 上现在关于 $NVDA 在聊什么？
Agent：→ python scripts/fetch.py --ticker NVDA --hours 24
       → 40 条命中 · 高互动 Top 15 · 时间线 · 发言账号排行（digest.md + tweets.json）
```

---

## 为什么用它

- **两种模式**：按需抓最新（REST）／真·秒级持续推送（WebSocket），按场景选。
- **自带内容监控看板**：一条命令起个本地网页，正文 / 时间 / 赞转评引浏览 / 作者粉丝实时上屏，
  新到的推文高亮 —— 不用只盯着终端刷 Markdown。只读、只监听本机、零依赖。
- **不烧钱**：$0.15 / 1000 条推文，注册送免费额度，没有月费。内置成本纪律，避开所有常见的"余额莫名消失"陷阱。
- **结果能直接用**：不只吐原始 JSON —— 直接给你一份带高互动 Top、时间线、发言账号排行的 Markdown 摘要。
- **把踩过的坑写进了代码**：时间窗静默失效、WS 必须回带掩码的 pong、规则更新漏字段会被重置成最贵档……
  这些都是实测踩出来的，已经替你绕开。

---

## 能拿到哪些数据

每条推文都是一整条结构化记录（`tweets.json` 里的一个元素）—— **不只是正文**：

| 类别 | 字段 |
|---|---|
| **推文正文** | `text` 全文（换行 / emoji / 链接原样保留） |
| **时间戳** | `time` 本地时区可读（`2026-09-26 22:22`）· `created_at` 原始 UTC 串 · `ts` epoch 毫秒 |
| **互动数据** | **`like` 点赞 · `rt` 转推 · `reply` 回复/评论 · `quote` 引用 · `view` 浏览量** |
| **作者信息** | `author` 用户名 · `author_name` 昵称 · `followers` 粉丝数 |
| **其他** | `id` 推文 ID · `url` 原推链接 · `lang` 语言 · `is_reply` / `is_rt` / `is_quote` 类型标记 |

摘要 `digest.md` 实际长这样：

```markdown
## 🔥 高互动 Top

### 1. @TrendSpider · 2.4k👍 412🔁 486.0k👁

> $NVDA Nvidia earnings preview: data center revenue seen at $41.2B, up 52% YoY.
> Whisper number above guidance. Watching 175 level closely.

`2026-09-26 22:22` · 粉丝 312,400 · [原推](https://x.com/TrendSpider/status/…)

## 🕐 时间线（新 → 旧）

- `09-26 22:22` **@TrendSpider** (312.4k) 2.4k👍 — $NVDA Nvidia earnings preview: data center
  revenue seen at $41.2B, up 52% YoY. [↗](https://x.com/TrendSpider/status/…)
- `09-26 19:40` **@retail_dad** (2.2k) 57👍 — 我的 $NVDA 仓位已经拿了两年，今天又加了一点。
  [↗](https://x.com/retail_dad/status/…) · 转推

## 👥 发言账号 Top

| 账号 | 条数 | 粉丝 | 累计赞 |
|---|---:|---:|---:|
| @TrendSpider | 1 | 312.4k | 2.4k |
| @quant_flow | 1 | 142.0k | 1.1k |
```

**原始响应里还有更多**：`raw_pageN.json` 保留 API 原样数据，作者 bio / `location` 所在地 /
`entities` 话题标签 / `conversationId` / `inReplyToUsername`，以及
**被引用、被转推那条推文的完整嵌套对象**（含它自己的正文和互动数）——
**改口径二次加工不用重新花钱抓**。

> ⚠️ 一个真实差异：WebSocket 的 `fast_tweet` 优先通道（5000+ 粉丝作者，亚秒级）
> **只有正文和时间，没有互动数据**。要按热度排序，请走按需抓取或规则推流。

---

## 30 秒上手

> 命令都假定**当前目录是 skill 根目录**（`SKILL.md` 所在处）。在别处跑请先 `cd` 过去。

### 1. 拿 API Key

👉 **https://twitterapi.io?ref=fysc666**

注册（无需信用卡）→ 在 dashboard 首页复制 API Key。
**注册即送 $0.1 免费额度**（= 10,000 credits，约 660 条推文）——
查一次几百条推文的量只花几十 credits，这点额度足够你把这个 skill 从头试到尾。

### 2. 把 Key 交给脚本

```bash
mkdir -p .secrets && echo "你的key" > .secrets/x-api.key
```

或用环境变量 `export TWITTERAPI_IO_KEY="你的key"`，或每条命令加 `--key 你的key`。
脚本按 `命令行 > 环境变量 > 密钥文件` 的顺序查找。

### 3. 自检 + 跑第一次

```bash
python scripts/xapi.py balance                               # 新账号应显示 10,000 credits ≈ $0.1000
python scripts/fetch.py --ticker NVDA --hours 6 --pages 1     # 第一次先跑这个，成本 <1 分钱
```

第二次打印出摘要、`out/nvda/digest.md` 生成了，就算跑通。之后照常按需换参数：

```bash
python scripts/fetch.py --ticker NVDA --hours 24
python scripts/fetch.py "ai agents" --hours 12 --pages 2
```

---

## 两种模式

### 模式 A · 按需抓取最新

```bash
python scripts/fetch.py --ticker NVDA --hours 24            # 股票代码（cashtag）
python scripts/fetch.py "openai" --hours 12 --pages 3        # 关键词
python scripts/fetch.py --ticker TSLA --from elonmusk --lang en --min-likes 100
```

产出到 `out/<查询>/`：`digest.md`（人看）· `tweets.json`（去重结构化）· `raw_pageN.json`（原始响应）。

### 模式 B · 实时推送（WebSocket）

```bash
python scripts/rules.py create --tag nvda --value '$NVDA' --interval 300   # 建规则（免费）
python scripts/rules.py on --tag nvda                                      # 激活（开始计费）
python scripts/stream.py --tag nvda --sec 600                              # 收 10 分钟
python scripts/rules.py off --tag nvda                                     # 用完停掉
```

`stream.py --loop` 是常驻模式，断线自动退避重连。
**常驻请挂到你自己的系统**（计划任务 / systemd / Docker / supervisor）—— 别挂在 agent 的一次性命令里。

产物：`out/x_live.jsonl`（一行一条推文，字段与 `tweets.json` 一致，另加 `received_at` 收到时刻，
可直接喂给下游分析）。

---

## 内容监控看板

不想只盯着终端刷 Markdown？一条命令起个本地网页看板：

```bash
python scripts/dashboard.py            # → 浏览器打开 http://127.0.0.1:8765
python scripts/dashboard.py --open     # 顺带自动打开浏览器
```

左侧列出 `out/` 下的所有抓取记录（模式 B 的「⚡ 实时流」也在里面），右侧是推文卡片：
正文全文（超 600 字折叠）、本地时间、**👍 赞 / 🔁 转推 / 💬 回复 / 🔗 引用 / 👁 浏览**、
作者 @handle 与粉丝数、原推链接、规则 tag 与类型标记。
每 3 秒自动拉增量，**新到的推文会高亮闪一下** —— 跑模式 B 时相当于一面实时监视墙。

| | |
|---|---|
| 只读 | 不写、不删任何文件，只读 `out/` |
| 只监听本机 | 默认 `127.0.0.1`，局域网访问不到（`--host 0.0.0.0` 才会暴露，慎用） |
| 路径校验 | 查询名走白名单，`../` 之类的请求直接 400 |
| 零依赖 | 纯标准库，不用装东西 |

> ⚠️ 这是常驻服务 —— 在**你自己的终端**里跑（关掉窗口即停）。
> 从 agent 的沙箱里拉起常驻进程，命令一结束就可能被回收。

---

## 装给别的 Agent

本仓库就是一个标准 Skill：`SKILL.md` 带 `name` + `description` frontmatter，
放在仓库根目录，可被 Claude Code / Cursor / Codex / Copilot / Gemini CLI 等支持 Skills 的 agent 直接读取。

```bash
# 方式一：用 skills CLI（发布到 GitHub 之后）
npx skills add <your-name>/x-live-fetch

# 方式二：手动复制到 agent 的 skills 目录
cp -r x-live-fetch ~/.claude/skills/      # Claude Code
```

装好后直接对 agent 说「X 上关于 XX 在聊什么」即可，它会自己读 `SKILL.md`。

### 装进 WorkBuddy

放到 **`~/.workbuddy/skills/x-live-fetch/`**（用户级，所有项目可用）即可，与从技能市场安装的技能同目录。
放到项目的 `.workbuddy/skills/` 则只在该项目内生效。

---

## 费用

| 项目 | 单价 |
|---|---|
| 推文 | $0.15 / 1000 条（1 条 = 15 credits） |
| 用户资料 | $0.18 / 1000 |
| 关注者 ID（批量） | $0.0045 / 1000 |
| 每次请求最低扣费 | 15 credits（返回 0 条也扣） |

**钱按「抓回来的条数」花，不按「调用次数」花。** 日常查一只票一天的量，几分钱。
只有规则持续激活 + 一直开着 WebSocket 才会累积，所以**用完请 `rules.py off`**。

余额**可能是负数**（欠费）—— 那不是查询错误，是真的该充值了；欠费时接口会返回
`HTTP 402 Credits is not enough`。先用 `python scripts/xapi.py balance` 确认。

---

## 目录结构

```
x-live-fetch/
├── SKILL.md                    # agent 入口（frontmatter: name + description + 平台字段）
├── references/
│   └── api-reference.md        # twitterapi.io 完整接口契约速查
├── scripts/
│   ├── xapi.py                 # 共享客户端：找 Key / 直连请求 / 余额 / WS 握手
│   ├── fetch.py                # 模式 A：按需抓取 + 生成摘要
│   ├── rules.py                # 过滤规则管理（增删改查 / 批量开关）
│   ├── stream.py               # 模式 B：WebSocket 实时消费
│   ├── dashboard.py            # 内容监控看板（本地只读服务）
│   └── selftest.py             # 离线自检
├── README.md / README.zh-CN.md
├── LICENSE
└── out/                        # 运行产物（已 gitignore）
    ├── <查询>/digest.md         #   人看的摘要
    ├── <查询>/tweets.json      #   归一化后的结构化推文
    ├── <查询>/raw_pageN.json   #   API 原样响应（含全部字段）
    └── x_live.jsonl            #   模式 B 的实时流
```

要求：**Python 3.8+**，无第三方依赖。

---

## 开发 / 自检

```bash
python scripts/selftest.py
```

**不需要 API Key、不联网、不花一分钱**，129 项断言覆盖 query 构造（时间窗必须落在 query 字符串里）、
抓取管线（去重 / 排序 / 渲染 / 空结果分支）、**字段契约（上面那张表与代码逐一对齐）**、
看板（数据组装 / HTTP 接口 / 路径穿越防护 / 只读保证，起真服务打真请求）、
WebSocket 帧编解码（掩码 / 粘包 / 分片重组）与四种事件类型解析、`--tag` 过滤语义。
改脚本后先跑它，CI 里也能直接跑。

---

## 常见坑（都已经在代码里绕开了）

- 时间窗 `since_time/until_time` **必须写进 query 字符串**；当 URL 参数传会被静默忽略。
- 多个 `OR` 词组叠进一个 query，**召回率会塌到 0** —— 宽召回要用简单词，判断放客户端。
- 更新规则**必须传全字段**，漏传 `interval_seconds` 会被重置成最贵档。
- 删除规则必须用 HTTP **DELETE**（POST 会 405）。
- WebSocket 一个 Key **只能开一条连接**；断线后**至少等 90 秒**再重连。
- 客户端 pong 帧**必须带掩码**（RFC6455），否则会话 20 秒就断。
- 推文 ID 是 64 位 snowflake，**当字符串用**，别转 int。
- 默认**直连**最快；挂代理容易 403，WS 还可能被 Cloudflare 拦。

---

## 说明

- 本项目只是一个 **twitterapi.io 的客户端封装**，与该服务无隶属关系，也不对其可用性与计费政策负责。
- 请遵守 twitterapi.io 的服务条款与 X 的平台规则，仅将本项目用于合法用途。

## License

MIT
