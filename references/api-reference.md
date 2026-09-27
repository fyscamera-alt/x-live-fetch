# twitterapi.io 接口契约速查

面向改脚本 / 排障用。所有内容以官方文档为准，标注「实测」的是文档没写、踩过坑才知道的。

- Base URL：`https://api.twitterapi.io`
- 认证：请求头 **`x-api-key: <你的key>`**（不是 `Authorization`，也不是 query 参数）
- WebSocket：`wss://ws.twitterapi.io/twitter/tweet/websocket`，同样用 `x-api-key` 握手头
- 官方文档：https://docs.twitterapi.io/introduction
- 官方也有自己的 agent skill：`npx skills add kaitoInfra/twitterapi-io`（端点覆盖更全，本仓库偏实战与成本纪律）

---

## 1 · 计费模型

- 100,000 credits = **$1.00**
- 推文 **$0.15 / 1000 条** → 1 条 = **15 credits**
- 用户资料 **$0.18 / 1000**；关注者 ID（批量端点）**$0.0045 / 1000**
- **每次请求最低扣 15 credits**，返回 0 条也扣
- 规则浏览（`get_rules`）、余额（`my/info`）不计费
- **余额可以是负数**（欠费）。欠费后调用返回 **HTTP 402**
  `{"error":"Unauthorized","message":"Credits is not enough.Please recharge"}`
- 限速触发时返回 **HTTP 429**，退避重试即可

**关键认知：钱按「抓回来的条数」花，不按「查询次数」花。** 所以成本由
「每组词翻几页 × 多少组词」决定，与时间窗跨多长无关（窗口只影响每组词有多少可取）。

没有账单明细接口（`credit_log` / `usage` / `billing` 全 404）。想测消耗只能**差分余额**：
记 `t0 / balance0` → 等 N 分钟 → 记 `balance1`。

> ⚠️ 差分余额前先排除**并发任务**：如果同一个 Key 还有别的程序在跑，
> 静默 75 秒后余额也会掉。先做一次「什么都不调用」的静默观测确认。

---

## 2 · REST 端点

### 2.1 高级搜索（模式 A 的核心）

```
GET /twitter/tweet/advanced_search?query=<q>&queryType=Latest&cursor=<c>
```

| 参数 | 说明 |
|---|---|
| `query` | 必填。搜索表达式，**时间窗也写在这里面** |
| `queryType` | 必填，`Latest` 或 `Top` |
| `cursor` | 翻页游标，首页留空 |

响应：

```json
{ "tweets": [ /* Tweet */ ], "has_next_page": true, "next_cursor": "..." }
```

**query 语法要点**

| 写法 | 效果 |
|---|---|
| `$NVDA` | cashtag，搜股票代码 |
| `"exact phrase"` | 精确词组 |
| `from:elonmusk` | 限定作者 |
| `lang:en` | 限定语言 |
| `min_faves:100` | 最少点赞 |
| `since_time:1776045662 until_time:1776081762` | **时间窗（unix 秒），必须写在 query 字符串里** |
| `(a OR b) c` | 布尔组合 |

- ❌ `since:2021-12-31_23:59:59_UTC` 这种写法**已不支持**
- ❌ 把 `since_time` 当 **URL 参数**传 → **静默忽略**，直接返回最新 20 条（不报错，最坑）
- 更多运算符语法：https://github.com/igorbrigadir/twitter-advanced-search
- ⚠️ **实测：多个 OR 词组叠进一个 query，召回率会塌到接近 0。** 宽召回请用 1~3 个简单词

**Tweet 对象主要字段**

`id`（snowflake，**用字符串**）· `url` · `text` · `createdAt`（`Sat Mar 15 05:31:28 +0000 2025`，
解析用 `strptime('%a %b %d %H:%M:%S %z %Y')`）· `lang` · `retweetCount` · `replyCount` ·
`likeCount` · `quoteCount` · `viewCount` · `isReply` · `inReplyToUsername` · `conversationId` ·
`author`（UserInfo）· `entities` · `quoted_tweet` · `retweeted_tweet` · `isLimitedReply`

**UserInfo 主要字段**

`userName` · `name` · `id` · `description`(bio) · `location` · `url`（bio 挂载链接）·
`followers` · `following` · `isBlueVerified`（⚠️ 付费蓝标，非质量信号）· `isAutomated`
（⚠️ 实测恒为 false，无用）· `canDm` · `createdAt` · `statusesCount` · `mediaCount`

**脚本落盘字段映射**（`tweets.json` 的元素 / `out/x_live.jsonl` 的每行）

| 落盘字段 | 来源 | 类型 | 说明 |
|---|---|---|---|
| `id` | `id` | str | 强制转字符串，避免 snowflake 精度丢失 |
| `url` | `url` | str | 模式 B 优先通道没有此字段，脚本按 `screen_name` + `id` 拼 |
| `text` | `text` | str | 保留换行与 emoji |
| `created_at` | `createdAt` | str | 原始 UTC 串（**仅模式 A**） |
| `time` | 解析 `createdAt` / `created_ms` | str | **已转本机时区**，`%Y-%m-%d %H:%M` |
| `ts` | 同上 | int | epoch 毫秒，**排序键** |
| `author` | `author.userName` / `screen_name` | str | |
| `author_name` | `author.name` / `display_name` | str | |
| `followers` | `author.followers` | int | 优先通道恒为 0（上游没给） |
| `like` | `likeCount` | int | 缺省 0 |
| `rt` | `retweetCount` | int | 缺省 0 |
| `reply` | `replyCount` | int | 缺省 0 |
| `quote` | `quoteCount` | int | 缺省 0 |
| `view` | `viewCount` | int | 缺省 0 |
| `lang` | `lang` | str | 优先通道为 `""` |
| `is_reply` | `isReply` | bool | **仅模式 A** |
| `is_rt` | `retweeted_tweet` 是否存在 | bool | **仅模式 A**；`--no-rt` 按此过滤 |
| `is_quote` | `quoted_tweet` 是否存在 | bool | **仅模式 A** |
| `rule_tag` | 事件里的 `rule_tag` | str | 模式 A 恒为空 |
| `received_at` | — | str | **仅模式 B**：客户端收到时刻（本地时间） |
| `kind` | `type` | str | **仅模式 B 优先通道**：`post`/`reply`/`repost`/`quote`/`thread`/`like`/`mention`/`follow` |
| `media` · `mentions` | `media` · `mentions` | list | **仅模式 B 优先通道** |

**未归一化、但 `raw_pageN.json` 里原样保留**的：`entities`（话题标签 / @提及 / 外链展开）·
`conversationId` · `inReplyToUsername` · `isLimitedReply` · `quoted_tweet` /
`retweeted_tweet` 的**完整嵌套对象**（含其自身正文与互动数）· 以及 `author` 的
`description`(bio) / `location` / `url` / `createdAt` / `statusesCount` / `mediaCount` /
`following` / `canDm`。
→ **要做地区判定、话题归组、按热度筛引用/转推，这些字段基本都要用上；改口径重分析直接读原始响应，不必重新抓。**

### 2.2 用户相关

| 端点 | 说明 |
|---|---|
| `GET /twitter/user/info?userName=<h>` | 单个用户资料（18 credits） |
| `GET /twitter/user/last_tweets?userName=<h>&includeReplies=false` | 用户最近推文（15 credits/条） |
| `GET /twitter/user/user_ids` 系列 | 批量按 userId 取资料 |
| `GET /twitter/user/followers` · `/followings` | 粉丝 / 关注（带完整资料） |
| `GET /twitter/user/followers_ids` 批量 | 只要 ID，超便宜 |
| `GET /twitter/user/mentions?userName=<h>` | 提到某人的推文，每页 20 条 |
| `GET /twitter/user/search?query=<kw>` | 按关键词搜用户 |

### 2.3 推文相关

| 端点 | 说明 |
|---|---|
| `GET /twitter/tweet/replies?tweetId=<id>` | 回复，每页 20 |
| `GET /twitter/tweet/quotes?tweetId=<id>` | 引用，每页 20 |
| `GET /twitter/tweet/retweeters?tweetId=<id>` | 转推者，约 100/页 |
| `GET /twitter/tweet/thread_context?tweetId=<id>` | 整条 thread 上下文 |
| `GET /twitter/tweets?tweet_ids=<a,b>` | 按 ID 批量取 |

### 2.4 过滤规则（模式 B 的订阅）

```
GET    /oapi/tweet_filter/get_rules      # 列出（免费）
POST   /oapi/tweet_filter/add_rule       # 新建，body {tag, value, interval_seconds}
POST   /oapi/tweet_filter/update_rule    # 更新，body {rule_id, tag, value, interval_seconds, is_effect}
DELETE /oapi/tweet_filter/delete_rule    # 删除，body {rule_id}
```

坑点：

1. **新建的规则默认 `is_effect=0`（未激活）** —— 建好是免费的，必须再调 update 激活。
2. **update 必须传全字段**。只传要改的那个字段会 400；漏传 `interval_seconds` 会被平台
   重置成 20 秒（最贵档）。
3. **删除必须用 HTTP `DELETE`**，用 POST 会 405。
4. `interval_seconds` 文档口径不一致：add 说最小 0.05、update 说最小 0.1、get_rules 说最小 100。
   **实测按 ≥100 来**。
5. 规则激活后**首次连接会补推一小批历史推文**（实测约 19 条），都计费 → 别在高热时段上线新规则。
6. 规则返回里的 `cost_credit` 字段**恒为 0**，不能用来对账。
7. 规则浏览 / 修改也可以直接在网页 dashboard 上做。

### 2.5 账号与余额

```
GET /oapi/my/info        →  { "recharge_credits": 123456 }
```

### 2.6 账号实时监控订阅（Stream 产品，按账号数包月）

与过滤规则**并行可用**，不是替代关系。适合「盯住 N 个固定账号」而非「盯关键词」。

| 端点 | 说明 |
|---|---|
| `POST /oapi/user/monitor/add` | 加入监控名单 |
| `POST /oapi/user/monitor/remove` | 移出 |
| `GET  /oapi/user/monitor/list` | 查看名单 |

定价（按监控账号数包月）：Starter $29/6 账号 · Growth $79/20 · Professional $149/50 ·
Enterprise $299/150 · Plus $499/500 · Scale $999/2000。配置后约 20 分钟内生效。
用规则做关键词监控、用 Stream 做账号监控，同一个 WebSocket 连接可同时收两种。

### 2.7 写操作（需要 login_cookie，谨慎）

`/twitter/user_login_v2` 拿 cookie 后可用：发推 `create_tweet_v2`、删除 `delete_tweet_v2`、
点赞 `like_tweet_v2`、转推 `retweet_tweet_v2`、关注 `follow_user_v2`、私信 `send_dm_v2`、
改资料 `update_profile_v2`、上传媒体 `upload_media_v2`、书签 `bookmark_v2` 等。
按次计费 $0.002~$0.005。**本仓库不封装这部分** —— 写操作风险高，且需要保管账号 cookie。

### 2.8 其他

`GET /twitter/trends`（按 woeid 取趋势）· 社区相关端点 · `GET /twitter/space/<id>` ·
`GET /twitter/article/<id>`（长文，100 credits/篇）· List 相关端点

---

## 3 · WebSocket 实时流

**端点**：`wss://ws.twitterapi.io/twitter/tweet/websocket`
**认证**：握手 header 带 `x-api-key`（浏览器无法直接设 header，需要后端代理）

### 连接规则（容易踩）

- **1 个 Key = 1 条活跃连接**，开第二条直接被关闭码 `1008` 拒绝。要并行就多申请 Key。
- 断开后**至少等 90 秒**再重连，否则服务端连接槽位没释放。
- **实测**：会话常在 20~30 秒内被服务端主动关闭 —— 这是平台行为，不是客户端 bug。
  正常形态就是「连上 → 收一会儿 → 断 → 退避重连」。
- **实测**：每次重连服务端可能把最近一批推文重发一遍，**客户端必须按 `id` 去重**。
- 服务端会发 **协议级 ping 帧**（opcode `0x9`）→ 客户端必须回 **pong（`0xA`）**，
  且 RFC6455 要求客户端帧**必须带掩码**，否则会话 20 秒就断。
- 同时还有 **应用级心跳** `{"event_type":"ping"}`（约每 40 秒），**无需响应**，忽略即可。
- 服务端只发文本帧（UTF-8 JSON），不会有二进制帧。

### 事件类型

| event_type | 含义 | 关键字段 |
|---|---|---|
| `connected` | 握手确认，仅一次 | `timestamp` |
| `ping` | 应用级心跳 | `timestamp`（可用来算延迟） |
| `tweet` | **规则匹配**批次 | `rule_id` · `rule_tag` · `tweets[]`（完整 Tweet 对象） |
| `tweet` | Stream 非优先推文 | 同上但**不含** `rule_id` |
| `fast_tweet` | Stream 优先通道（5000+ 粉丝作者，亚秒） | `tweet{ id, screen_name, text, type, created_ms, media[], mentions[] }` |

规则匹配 payload：

```json
{
  "event_type": "tweet",
  "rule_id": "rule_12345",
  "rule_tag": "nvda",
  "tweets": [
    {
      "id": "1234567890",
      "text": "...",
      "author": {"id": "1", "username": "someone", "name": "Someone"},
      "createdAt": "Sat Mar 15 05:31:28 +0000 2025",
      "likeCount": 420, "retweetCount": 42, "replyCount": 10
    }
  ],
  "timestamp": 1642789123456
}
```

优先通道 payload：

```json
{
  "event_type": "fast_tweet",
  "timestamp": 1776623420082,
  "tweet": {
    "id": "2045879341243043889",
    "screen_name": "MarioNawfal",
    "display_name": "Mario Nawfal",
    "text": "...",
    "type": "post",
    "created_ms": 1776623419483,
    "snow_delay_ms": 571,
    "media": [], "mentions": []
  }
}
```

`type` 取值：`post` / `reply` / `repost` / `quote` / `thread` / `like` / `mention` / `follow`。
`snow_delay_ms` = 上游发布 → 服务端收到的延迟，是**流健康度最好的信号**（持续 >2s 说明上游有问题）。

### 关闭码

| Code | 含义 | 处理 |
|---|---|---|
| 1000 | 正常关闭 | 正常重连 |
| 1001 | 服务端重启 | 正常重连 |
| 1002 | 协议错误 | 检查帧实现（掩码！） |
| 1006 | 网络异常 | 等 90s 重连 |
| 1008 | 策略违规 | 通常是同 Key 重复连接或 Key 失效 |
| 1011 | 服务端内部错误 | 可安全重连 |
| 1013 | 服务端过载 | 等 60s+ 再重连 |

---

## 4 · QPS

按余额分档（免费号 1 请求 / 5 秒；余额 ≥50,000 credits 可到 20 QPS；文档称最高支持 1000+ req/s）。
查表：https://twitterapi.io/qps-limits

---

## 5 · 抓取实践要点

- **每页响应立即落盘**再离线分析 —— 否则每次改口径都要重新烧钱。
- 时间窗取**并集**：中国用户的「昨天」∪ 美东的「昨天」≈ 36 小时，之后按美东日期分组。
- 关键词表建议 50~160 组，成本很低（一天全量扫 ≈ $0.5~1）。
- **宽召回、严判断**：query 里别写意图长句，把分类放到客户端做。
- 地区判定不能用 `lang` 猜（`en` 分不出英/美/澳/加）：用
  `author.location` 里的州名/城市名/`USA` + 正文里的 `US only` / `ship to US` 组合判断。
  ⚠️ 州缩写要带分隔符匹配（`CA` 既是加州也是加拿大，`ME` 既是缅因也是「我」），且**只在 location 字段里匹配，别扫正文**。
