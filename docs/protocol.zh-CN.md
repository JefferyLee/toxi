# toxi 线协议 v2 —— 好友消息信封

> English version: protocol.md
> 本规范对 toxi 家族的每个实现都有约束力。这些实现是 toxi 的 Python daemon、ToxiOS 的 Go daemon 和 ToxiOS 的 iOS app。参考实现是 `src/toxi/envelope.py` 和 `src/toxi/daemon.py`。测试 `tests/test_events.py`、`tests/test_threads.py`、`tests/test_rooms.py` 和 `tests/test_files.py` 是可执行的规范。设计背景见 `docs/agent-comm-design.zh-CN.md` §5 和 §11。

## 1. 传输

- 一条 Tox 好友消息（`TOX_MESSAGE_TYPE_NORMAL`）承载一个 JSON 信封。一条 Tox 好友消息最多 **1372 字节**。
- Tox 文件传输（`TOX_FILE_KIND_DATA`）承载文件。
- JSON 使用 UTF-8。非 ASCII 字符原样保留。它们不转义成 `\uXXXX`。
- 只有好友能发送。没有服务器。
- 节点的 Tox 公钥标识该节点。公钥是 64 位大写十六进制。节点的所有身份数据都关联到这把公钥。

## 2. 信封

```json
{"v": 2, "uuid": "550e8400-…", "type": "text", "ts": 1760600000, "data": { … }}
```

| 字段 | 类型 | 规则 |
|---|---|---|
| `v` | int | 协议版本。v1 对端发送 `1`。v1 对端只认识 `text`、`ack` 和 `contact_share`。 |
| `uuid` | string，**≤64 字符** | 发送方选定的 id。推荐使用 UUID4（36 字符）。接收方按这个 id 去重（`INSERT OR IGNORE`）。如果 id 超过 64 字符，接收方在*所有做 id 匹配的地方*把它截到 64 字符。这些地方是 uuid、`reply_to`、`ref_uuid`、分片 id 和文件 msg_uuid。如果 uuid 缺失，接收方自行生成一个。 |
| `type` | string | 见 §3。**接收方静默丢弃未知类型。它至多记录日志。** |
| `ts` | int | 发送方的 Unix 时间，单位为秒。 |
| `data` | object | 内容取决于类型。**接收方忽略未知字段。**未知字段绝不是错误。 |

有些输入不是带 `type` 的 JSON 对象。例如其他 Tox 客户端发来的纯文本消息。接收方把这种输入当作 `text`，原始字节即 `body`。

## 3. 类型

### 3.1 `text`

| data 字段 | 类型 / 上限 | 含义 |
|---|---|---|
| `body` | string | 消息正文。此字段必填。 |
| `from_session` | ≤64 | 发送方的会话或项目标签。例如 Claude Code 的项目目录名。接收方把它显示为 `别名@会话`。 |
| `part` | `{id ≤64, i, n}` | 本信封是逻辑消息 `id` 的第 `i`/`n` 个分片（§4）。 |
| `thread` | ≤64 | 线程 id 或话题 id（`pr-412`）。 |
| `reply_to` | ≤64 | 本消息所回复的 `uuid`。它也可以是分片 id 或文件 msg_uuid。 |
| `kind` | `request` `report` `ack` `relay` | 如果此字段缺省，消息是普通 `msg`。`request` 期待回复。`report` 是状态汇报。`ack` 只是"收到"。`ack` **绝不能产生通知或唤醒任何人**。`relay` 转述第三方的话（传闻）。 |
| `needs_human` | bool | 必须由人来决定，不能由 agent 决定。 |
| `tags` | ≤8 个 ≤24 字符的字符串 | 自由标签（`WARN`、`OUTAGE`）。 |
| `room` | ≤64 | 本消息所属 room 的 id（§3.6）。 |
| `room_msg` | ≤64 | 同一条 room 消息的所有扇出拷贝共享的 id。 |
| `relayed` | `{name ≤64, ts}` | room 创建者用此字段转发 room 历史。此字段说明原消息的作者和时间（§3.6）。接收方把消息存为已读。接收方不提醒。 |

接收方必须做这些事：

- 每个 `uuid` 只存一次。
- **对收到的每条 text 发送 `ack`，重复的也要发。**`ack` 告诉发送方停止重试。
- 执行限流（§6）。
- 显示 `from_session` 以及线程标签和类型标签。
- 按上表的说明对待 `needs_human` 和 `relay`。

### 3.2 `ack`

`data: {"ref_uuid"}`。`ack` 是传输层的送达确认。发送方收到 `ack` 时，把 `ref_uuid` 从 `sent` 改为 `delivered`。节点绝不对 `ack` 发送 `ack`。

- 如果一条 text 在 `ack_timeout` 内一直没有 ack（状态 `sent`），发送方重发这条 text。`ack_timeout` 是 5 分钟。发送方只在联系人在线时重发。
- 如果一条 `sent` 的 text 已经存在 24 小时，发送方停止重发，并把它标为 `failed`。这 24 小时从发送方创建该 text 时开始计算。
- `queued` 的 text 永不失败。它一直等到联系人连上。

### 3.3 `receipt`

`data: {"ref_uuid", "kind": "read" | "done"}`。节点只向 `caps` 含 `receipt` 的对端发送回执。回执绝不唤醒任何人。

- `read`：消息**已展示给人，或已送进 agent 的上下文**。发送方把 `ref_uuid` 标为 `read`。如果 ack 丢失，发送方同时把 `ref_uuid` 标为 `delivered`。
- `done`：接收方**已处理**该消息。"已处理"有两种含义。第一种含义是接收方用指向该消息的 `reply_to` 做了回复。这种情况下回执是自动的。第二种含义是接收方显式地把消息标为已处理（`toxi done`、MCP `mark_handled`）。
- 发送方收到 `done` 时，把消息标为 `handled`。`done` 蕴含 `read`。之后到来的 `read` 绝不把 `handled` 降级。
- 如果节点不认识 `done`，它把收到的 `done` 当作 `read`。

对多段消息，`ref_uuid` 是分片 id。发送方标记每一个分片。对文件，`ref_uuid` 是发送方的文件 `msg_uuid`（§3.7）。

### 3.4 `profile`

```json
{"kind":"agent","name":"castle-op","runtime":"grok","model":"grok-4","host":"grok-vm",
 "operator":"jeff","role":"Power-Up-Chess ops bot","proto":2,"caps":["profile","session","receipt","part"]}
```

每个字符串 ≤120。`kind ∈ agent|human|service`。`caps` 最多 16 个字符串，每个字符串 ≤32。

- 如果节点有卡片，它在**联系人每次连上时**发送卡片。卡片每次变更时，它也发送卡片。
- 接收方为每个联系人保存最新的卡片。接收方用 `caps` 做能力门控。
- 节点启动时必须用自身构建刷新 `proto` 和 `caps`。它绝不能从保存的卡片取这些值。
- 卡片内容是自述的。但卡片走端到端加密信道。因此只有持钥者能发出它。

每个能力名说明节点**能接收并处理**什么。声明某项能力绝不要求节点发送对应字段。

| cap | 本节点…… | 因此对端可以…… |
|---|---|---|
| `profile` | 接受并展示卡片 | 发送它的卡片 |
| `session` | 存储并展示 `from_session` | ——。节点任何时候都可以发送可选字段。接收方忽略未知字段。 |
| `receipt` | 发送 `read` 和 `done` 回执，并理解收到的回执 | 期待 `read` 和 `handled` 状态 |
| `part` | 重组 `part` 分片 | 把长文本按分片发送，而不是按 `[i/n] ` 分条发送 |

只有 `part` 和 `receipt` 影响发送行为。`profile` 和 `session` 只是信息性的。

### 3.5 `contact_share`

此类型与 v1 相同：`{"tox_id"（76 位十六进制，必填）, "suggested_alias", "note"（可为空）}`。接收方绝不自动添加联系人。接收方把它存为待处理的介绍。用户手动接受它（`toxi accept-intro`）。

### 3.6 `room`

```json
{"op":"create","id":"457707e9-…","name":"p3-room",
 "members":[{"pk":"8FBE…","name":"ada"},{"pk":"A6CD…","name":"belinda-bot"}]}
```

**room 卡片**是应用层 room 的完整成员表。`id` 和 `name` ≤64。`members` 最多 64 项。每一项中，`pk` 是 64 位大写十六进制，`name` ≤64。

规则：

- 节点只接受联系人发来的卡片。
- 如果本地已有该 room，且它的 `created_by` 不是发送方的公钥，节点忽略该卡片。只有创建者修改成员。
- 如果节点自己的公钥不在 `members` 里，节点在本地删除该 room。这表示创建者移除了该节点。历史保留。
- 其他情况下，节点 upsert 该 room，并把 `created_by` 设为发送方的公钥。节点**整体替换**成员表。
- 成员连上时，创建者向该成员重发卡片。`add` 或 `remove` 之后，创建者向所有成员重发卡片。
- room 消息是普通 `text`。它的 `room` 是 room id。它的 `room_msg` 是每条逻辑消息一个新 id。
- 发送方把同一正文的拷贝发给每个是其联系人的成员。
- 没有传输层群组。如果两个成员互不是联系人，他们看不到彼此的消息。客户端应标出这一点。
- **新成员补历史**：
  - 创建者的卡片第一次送达某成员时，创建者把该 room 最近 20 条文本转发给该成员。
  - 每条转发的文本都是普通 `text` 信封。它带有 `relayed: {"name": <原作者>, "ts": <原时间>}`、`room`、`room_msg` 以及原来的线程、类型和标签。
  - `room_msg` 是原消息 id。信封 `uuid` 也是这个 id。因此接收方会对重复发送去重。
  - 接收方把转发的文本存为**已读**。接收方绝不为它提醒。
  - 如果接收方不认识 `relayed`，它看到的是创建者发来的普通消息。
  - 如果一条历史文本装不进一条 Tox 消息，创建者把它拆成 `part` 分片。每个分片的信封 `uuid` = `"<room_msg>:<i>"`，`part.id` = `room_msg`。
  - 创建者对所有成员都这样拆，不看成员的 caps。因此没有 `part` cap 的成员把每个分片存为一条单独的消息。
- 显示：节点把自己相同 `room_msg` 的扇出拷贝显示为一条。这一条显示每个接收方的状态。

### 3.7 `file`

```json
{"msg_uuid":"…","name":"shot.png","size":9085,"sha256":"…","from_session":"p3",
 "thread":"files","kind":"report"}
```

发送方在 `tox_file_send` **之前立刻**发出此卡片。该传输是 `name` 和 `size` 相同的 `TOX_FILE_KIND_DATA` 传输。

接收方在 300 秒内按（好友、清洗后的文件名、大小）匹配卡片和传输。卡片和传输**到达顺序不限**。匹配后，接收方做这些步骤：

- 它用私有权限保存文件。
- 它把该行的 id 设为发送方的 `msg_uuid`。因此回执和 `reply_to` 指向同一个 id。
- 它复制线程、类型和 room 字段以及 `from_session`。
- 它按扩展名给文件分类（image / voice / video / file）。
- 它校验 `sha256`。如果哈希不符，接收方保留文件，并加标签 `sha256-mismatch`。

文件没有 `ack`。

- 传输完成时，发送方把文件标为 `delivered`。完成信号是长度为 0 的分块请求。
- 如果接收方取消传输，文件为 `failed`。
- 如果链路断开，文件回到队列。重连后，传输从第 0 字节重新开始。

如果文本正文超过 64 KB，发送方按此方式把它作为 `.txt` 文件发送。发送方不把它作为 text 发送。

## 4. 多段文本

发送方：

- 如果整个信封超过 1372 字节，且对端有 `part` 能力，发送方把 `body` 拆成分片。发送方按字符边界拆分。
- 发送方确定每个分片的大小，使每个分片的信封都放得下。这个大小包括 `from_session`、线程字段和最坏情况的 `part`。
- 发送方用各自的 `uuid` 和 `part: {id, i, n}` 发送每个分片。所有分片使用同一个 `id`。
- 如果对端没有 `part` 能力，发送方发送独立的 text，并加前缀 `[i/n] `。这是 v1 行为。
- 发送方分别跟踪每个分片的 `ack`。只有**每个**分片都收到 ack，逻辑消息才是 `delivered`。
- 收到针对分片 `id` 的 `receipt` 时，逻辑消息是 `read`。这个回执标记所有分片。

接收方：

- 接收方按（好友、`id`）缓冲分片。
- 接收方对**每个分片的 uuid** 发送 ack。
- 一条逻辑消息只计一次限流。
- 所有 `n` 个分片到齐时，接收方存**一条**消息。它的 `uuid` 是 `id`。它的正文是按 `i` 顺序拼接的分片。它的元数据取自接收方看到的第一个分片。
- 60 秒后，接收方存下已到达的分片。它给正文加前缀 `[toxi: only k of n parts arrived] `。
- 如果某个分片所属的 `id` 已经存过，接收方发送 ack 并忽略该分片。这种情况是迟到的重发。
- 如果总量超过 64 KB，接收方丢弃该消息并记住该 id。

通知：

- 接收方只在消息齐全时才通知。通知包括 hook 和唤醒。

## 5. 状态模型（发送方）

`queued` → `sent` → `delivered` → `read` → `handled`

- `queued`：联系人离线，或 toxcore 拒发了该消息。
- `delivered`：收到了 ack，或文件传输完成。
- `read`：收到了回执。
- `handled`：收到了 `done` 回执。
- `failed`：`sent` 的 text 创建 24 小时后仍没有 ack。被拒收的文件传输也是 `failed`。
- `queued` 的记录永远不会变成 `failed`。

周期性扫描在 `ack_timeout` 后重发没有 ack 的 `sent` text。扫描只在联系人在线时这样做。重连只冲刷 `queued` 记录和欠下的回执。重连按发送顺序冲刷 `queued` 记录。为决定是否入队，发送方在发送时向 toxcore 询问在线状态。发送方不使用缓存的标志。

节点在本地路由回复。这是节点行为，不是线路格式。节点按以下顺序应用这些规则：

1. 如果 text 以 `@name` 开头，节点只把它投递给标签为 `name` 的会话。标签是 `TOXI_SESSION`。如果没有设置 `TOXI_SESSION`，标签是项目目录名。匹配不区分大小写。
2. 否则，节点使用自己的记录：每条消息由自己的哪个会话发出。这个记录绝不上线路。节点把收到的 `reply_to` 投递给那个会话，或投递给最近在同一线程里与该联系人对话的会话。这个关联从收到的消息的 `created_at` 起 30 分钟有效。
3. 30 分钟之后，或没有这种关联时，节点按该对端配置的绑定投递。

## 6. 上限与防滥用

| 项 | 上限 |
|---|---|
| 一条 Tox 消息 | 1372 字节 |
| 重组后的文本正文 | 64 KB。更大的正文走文件。 |
| 文件 | 100 MB |
| 每联系人入站文本 | 60 条/分钟。一条消息的分片只计一次。接收方丢弃超出的 text，且**不回 ack**。 |
| 入站好友请求 | 5 个/分钟 |
| 分片重组窗口 | 60 秒。文件元信息窗口是 300 秒。 |
| 字符串 | 见各字段的上限。截断过长的字符串。不要拒绝它。 |

非好友根本不能发送。Tox 强制执行这一点。密钥、数据库、配置、卡片和收到的文件都是私有文件（0600 / 0700）。

## 7. 配对（客户端可选）

`toxi pair` 生成邀请。邀请是 `{"v":1,"tox_id":<76 hex>,"name":<邀请方名字>,"nonce":<32 hex>,"exp":<unix>}` 的 base64url。邀请 10 分钟有效。邀请只能使用一次。

- 加入方用好友请求添加邀请方的 Tox ID。好友请求消息是 `{"toxi":"toxi-invite","nonce":…,"name":<加入方名字>}`。
- 如果请求带有有效的 nonce，邀请方自动接受它。邀请方用 `name` 命名该联系人。
- 其他好友请求仍然手动处理。
- 手机客户端可以把邀请显示成二维码。手机客户端扫描邀请二维码即可加入。

## 8. 兼容矩阵

| 对端 | 它认识什么 | v2 节点对它怎么做 |
|---|---|---|
| v1（今天的 ToxiOS、toxi ≤0.3.0） | `text`（只有 body）、`ack`、`contact_share` | 不发送 `profile`、`receipt`、`room` 或 `file`。把长文本按 `[i/n] ` 分条发送。对端忽略多出的 text 字段。 |
| v2 但缺某能力 | 它的 `caps` 声明的内容 | 按每项能力门控（`part`、`receipt`）。 |
| v2 完整 | 以上全部 | — |

v2 节点必须无限期地与 v1 对端正常工作。v1 对端只失去新功能。v1 对端绝不丢失消息。
