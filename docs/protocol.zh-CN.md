# toxi 线协议 v2 —— 好友消息信封

> English version: protocol.md
> 对 toxi 家族的每个实现都是规范（toxi 的 Python daemon、ToxiOS 的 Go daemon + iOS app）。参考实现：`src/toxi/envelope.py` 与 `src/toxi/daemon.py`；`tests/test_events.py`、`tests/test_threads.py`、`tests/test_rooms.py`、`tests/test_files.py` 是可执行的规范。设计背景：`docs/agent-comm-design.zh-CN.md` §5、§11。

## 1. 传输

- 一条 Tox 好友消息（`TOX_MESSAGE_TYPE_NORMAL`，最多 **1372 字节**）承载一个 JSON 信封。文件走 Tox 文件传输（`TOX_FILE_KIND_DATA`）。
- JSON 为 UTF-8，非 ASCII 原样保留（不转义成 `\uXXXX`）。只有好友能发消息；没有服务器。
- 节点以 Tox 公钥标识（64 位十六进制、大写）。"谁是谁"的一切都挂在这把钥匙上。

## 2. 信封

```json
{"v": 2, "uuid": "550e8400-…", "type": "text", "ts": 1760600000, "data": { … }}
```

| 字段 | 类型 | 规则 |
|---|---|---|
| `v` | int | 协议版本。v1 对端发 `1`，只认识 `text`、`ack`、`contact_share`。 |
| `uuid` | string，**≤64 字符** | 发送方选定的 id；推荐 UUID4（36 字符）。接收方以它去重（`INSERT OR IGNORE`），超过 64 的在*所有做匹配的地方*（uuid、`reply_to`、`ref_uuid`、分片 id、文件 msg_uuid）一律截到 64，缺失时自行生成。 |
| `type` | string | 见 §3。**未知类型静默丢弃（至多记日志）。** |
| `ts` | int | 发送方的 Unix 秒。 |
| `data` | object | 按类型。**未知字段忽略**，绝不报错。 |

不是"带 `type` 的 JSON 对象"的内容（例如其他 Tox 客户端发来的纯文本）一律当作 `text`，原始字节即 `body`。

## 3. 类型

### 3.1 `text`

| data 字段 | 类型 / 上限 | 含义 |
|---|---|---|
| `body` | string | 正文（必填） |
| `from_session` | ≤64 | 发送方会话/项目标签（如 Claude Code 的项目目录名）。显示为 `别名@会话`。 |
| `part` | `{id ≤64, i, n}` | 本信封是逻辑消息 `id` 的第 `i`/`n` 段（§4） |
| `thread` | ≤64 | 线程 / 话题 id（`pr-412`） |
| `reply_to` | ≤64 | 所回复的 `uuid`（或分片 id / 文件 msg_uuid） |
| `kind` | `request` `report` `ack` `relay` | 缺省即普通 `msg`。`request` 期待回复；`report` 是状态汇报；`ack` 只是"收到"，**绝不能产生通知或唤醒**；`relay` 是转述第三方的话（传闻）。 |
| `needs_human` | bool | 需要人、而不是 agent 来决定 |
| `tags` | ≤8 个 ≤24 字符的字符串 | 自由标签（`WARN`、`OUTAGE`） |
| `room` | ≤64 | 所属 room 的 id（§3.6） |
| `room_msg` | ≤64 | 同一条 room 消息所有扇出拷贝共享的 id |
| `relayed` | `{name ≤64, ts}` | room 创建者转发的历史：原作者与原时间（§3.6）；存成已读、不提醒 |

接收方职责：按 `uuid` 只存一次；**对每条收到的 text（含重复）回 `ack`**，发送方才会停止重试；执行限流（§6）；显示 `from_session` 与线程/类型标签；按上述方式对待 `needs_human` / `relay`。

### 3.2 `ack`

`data: {"ref_uuid"}`。传输层送达确认：发送方把 `ref_uuid` 从 `sent` 改为 `delivered`。`ack` 本身不回 ack。未被 ack 的 text（状态 `sent`）在 `ack_timeout`（5 分钟，对方在线时）后重发，`sent` 的 text 创建 24 小时后放弃（`failed`）；`queued` 的 text 永不失败，一直等对方连上。

### 3.3 `receipt`

`data: {"ref_uuid", "kind": "read" | "done"}`。只发给 `caps` 含 `receipt` 的对端；回执绝不唤醒任何人。

- `read`：消息**被人看到或被送进 agent 上下文**。发送方把 `ref_uuid` 标为 `read`（若 ack 丢了也顺带标 `delivered`）。
- `done`：接收方**处理完了**——用 `reply_to` 指向该消息回复了（自动），或显式标记处理完（`toxi done`、MCP `mark_handled`）。发送方标为 `handled`；`done` 蕴含 `read`，之后到来的 `read` 不会把 `handled` 降级。不认识 `done` 的接收方把它当 `read`。

多段消息的 `ref_uuid` 是分片 id（发送方把所有段都标上）；文件的是发送方的文件 `msg_uuid`（§3.7）。

### 3.4 `profile`

```json
{"kind":"agent","name":"castle-op","runtime":"grok","model":"grok-4","host":"grok-vm",
 "operator":"jeff","role":"Power-Up-Chess ops bot","proto":2,"caps":["profile","session","receipt","part"]}
```

字符串 ≤120；`kind ∈ agent|human|service`；`caps` ≤16 个 ≤32 字符的字符串。**好友每次连上时**（若自己有卡片）以及卡片变更时发送。接收方保存每个联系人最新的卡片，并用 `caps` 做能力门控。节点启动时必须用自身构建刷新 `proto`/`caps`，而不是沿用保存的旧卡片。卡片是自述的，但走的是端到端加密信道，只有持钥者能发出。

能力名描述的是节点**能接收并处理**什么；声明某项能力并不要求节点一定发送对应字段：

| cap | 本节点…… | 因此对端可以…… |
|---|---|---|
| `profile` | 接受并展示卡片 | 发送它的卡片 |
| `session` | 存储并展示 `from_session` | ——（可选字段任何时候都可以发；不认识的会被忽略） |
| `receipt` | 发送 `read` 和 `done` 回执并理解收到的回执 | 期待 `read` 和 `handled` 状态 |
| `part` | 重组 `part` 分段 | 长文本按分段发而不是 `[i/n] ` 分条 |

只有 `part` 和 `receipt` 影响发送行为；`profile` 和 `session` 只是信息性的。

### 3.5 `contact_share`

与 v1 相同：`{"tox_id"（76 位十六进制，必填）, "suggested_alias", "note"（可为空）}`。绝不自动添加：接收方存为待处理的介绍，由用户手动接受（`toxi accept-intro`）。

### 3.6 `room`

```json
{"op":"create","id":"457707e9-…","name":"p3-room",
 "members":[{"pk":"8FBE…","name":"ada"},{"pk":"A6CD…","name":"belinda-bot"}]}
```

**room 卡片**：应用层 room 的完整成员表。`id`、`name` ≤64；`members` ≤64 项，`pk` 为 64 位大写十六进制，`name` ≤64。规则：

- 只接受联系人发来的卡片。若本地已有同 id 的 room 且 `created_by` 不是发送方公钥，忽略（只有创建者能改成员）。
- 若我的公钥不在 `members` 里，本地删除该 room（我被移出了）；历史保留。
- 否则 upsert room（`created_by` = 发送方公钥）并**整体替换**成员表。
- 创建者在成员连上时向其重发卡片，在 `add`/`remove` 之后向所有人重发。
- room 消息就是普通 `text`，带 `room` = room id、`room_msg` = 每条逻辑消息一个新 id；发送方把同一正文扇出给它拥有为联系人的每个成员。没有传输层群组；互不为联系人的成员看不到彼此的消息（客户端应标出）。
- **新成员补历史**：创建者的卡片第一次送达某成员后，创建者把该 room 最近 20 条文本以普通 `text` 信封转发给他，带 `relayed: {"name": <原作者>, "ts": <原时间>}`、`room`、`room_msg`（= 原消息 id，也用作信封 `uuid`，重复发送会被去重）以及原来的线程/类型/标签。接收方把带 `relayed` 的文本存成**已读**、不提醒；不认识 `relayed` 的接收方只会把它当成创建者发来的普通消息。一条 Tox 消息装不下的历史文本会拆成 `part` 分段，信封 `uuid` = `"<room_msg>:<i>"`、`part.id` = `room_msg`；创建者不看成员的 caps 都这样拆，所以没有 `part` cap 的成员会把每个分段存成一条单独的消息。
- 显示：把自己的扇出拷贝（相同 `room_msg`）合并成一条，附每个接收者的状态。

### 3.7 `file`

```json
{"msg_uuid":"…","name":"shot.png","size":9085,"sha256":"…","from_session":"p3",
 "thread":"files","kind":"report"}
```

在对同名同大小的 `TOX_FILE_KIND_DATA` 传输调用 `tox_file_send` **之前立刻**发出。接收方在 300 秒内按（好友、清洗后的文件名、大小）把卡片和传输对上，**先到后到都行**，然后：保存文件（私有权限），把该行的 id 改成发送方的 `msg_uuid`（回执与 `reply_to` 才对得上），复制线程/类型/room 字段与 `from_session`，按扩展名分类（image / voice / video / file），校验 `sha256`（不符则保留文件并加标签 `sha256-mismatch`）。文件没有 `ack`：发送方把传输完成（长度 0 的分块请求）视为 `delivered`；接收方取消视为 `failed`；断线则文件回到队列，重连后从第 0 字节重传。超过 64 KB 的文本正文以 `.txt` 文件按此方式发送，而不是作为 text。

## 4. 多段文本

- 发送方：若整个信封会超过 1372 字节且对端有 `part` 能力，按字符边界把 `body` 切成若干段，使每段的信封（含 `from_session`、线程字段和最坏情况的 `part`）都放得下；每段用自己的 `uuid` 和 `part: {id, i, n}`（同一个 `id`）发送。对端没有 `part` 能力时，发独立的 text 并加 `[i/n] ` 前缀（v1 行为）。发送方分别跟踪每段的 `ack`：**所有段**都被 ack 才算整条 `delivered`；收到针对分段 `id` 的 `receipt` 则整条（所有段）标 `read`。
- 接收方：按（好友、`id`）缓冲；**对每段的 uuid 回 ack**；一条逻辑消息只计一次限流；`n` 段齐了就存**一条**消息，`uuid` = `id`，正文按 `i` 顺序拼接，元数据取第一段。60 秒后把已到的存下，正文前缀 `[toxi: only k of n parts arrived] `。已存过的 `id` 再来分段（迟到的重发）：回 ack 并忽略。总量超过 64 KB：丢弃并记住该 id。
- 只在消息齐全后才通知（hook、唤醒）。

## 5. 状态模型（发送方）

`queued`（对方离线，或 toxcore 拒发）→ `sent` → `delivered`（ack / 传输完成）→ `read`（回执）→ `handled`（`done` 回执）。`sent` 的 text 创建 24 小时后仍无 ack，或文件被拒收，则 `failed`；`queued` 的记录永远不会变成 `failed`。周期性扫描会在 `ack_timeout` 后、对方在线时重发未被 ack 的 `sent` text；重连只冲刷 `queued` 记录（按发送顺序）和欠下的回执。是否入队由发送时向 toxcore 询问在线状态决定，而不是缓存的标志。

回复在本地路由（节点行为，不是线路格式），按以下顺序。以 `@name` 开头的 text 只投给标签为 `name`（`TOXI_SESSION`，否则为其项目目录名）的会话，不区分大小写。否则，节点记住自己哪个会话发出了每条消息（不上线路），收到 `reply_to` 指向它的消息时投递给那个会话，或投递给最近在同一线程里与该联系人对话的会话，有效期为从收到的消息的 `created_at` 起 30 分钟；之后或没有这种关联时，按该对端配置的绑定投递。

## 6. 上限与防滥用

| 项 | 上限 |
|---|---|
| 一条 Tox 消息 | 1372 字节 |
| 重组后的文本正文 | 64 KB（再大走文件） |
| 文件 | 100 MB |
| 每联系人入站文本 | 60 条/分钟（一条消息的分段只计一次）；超出的丢弃且**不回 ack** |
| 入站好友请求 | 5 个/分钟 |
| 分段重组窗口 | 60 秒；文件元信息窗口 300 秒 |
| 字符串 | 见各字段上限；截断而不是拒绝 |

非好友根本发不进来（Tox）。密钥、数据库、配置、卡片和收到的文件都是私有文件（0600 / 0700）。

## 7. 配对（客户端可选）

`toxi pair` 生成邀请：`{"v":1,"tox_id":<76 hex>,"name":<邀请方名字>,"nonce":<32 hex>,"exp":<unix>}` 的 base64url，10 分钟有效，一次性。加入方用好友请求消息 `{"toxi":"toxi-invite","nonce":…,"name":<加入方名字>}` 添加邀请方的 Tox ID；邀请方自动接受带有效 nonce 的请求，并用 `name` 命名联系人。其它好友请求仍然手动。手机客户端可以把邀请显示成二维码，扫码即加入。

## 8. 兼容矩阵

| 对端 | 它认识什么 | v2 节点对它怎么做 |
|---|---|---|
| v1（今天的 ToxiOS、toxi ≤0.3.0） | `text`（只有 body）、`ack`、`contact_share` | 不发 `profile`/`receipt`/`room`/`file`；长文本以 `[i/n] ` 分条；多出的 text 字段对端会忽略 |
| v2 但缺某能力 | 按 `caps` | 按能力门控（`part`、`receipt`） |
| v2 完整 | 以上全部 | — |

v2 节点必须无限期地与 v1 对端正常工作；v1 对端只是少了新功能，不会丢消息。
