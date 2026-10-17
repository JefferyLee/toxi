# toxi — Claude Code 命令行聊天插件 PRD

> 🌐 语言: [English](prd.md) | **中文**

**版本**：v0.1
**日期**：2026-05-27
**状态**：v0.1 MVP 完成；Claude Code 集成已交付；已打包为插件（详见 §5.1）

---

## 1. 项目概述

### 1.1 一句话定义

一个嵌入 Claude Code 的命令行聊天插件，让开发者在写代码的同时，能与朋友进行**异步、加密、去中心化**的文字沟通，无需依赖任何中心化服务器。

### 1.2 项目背景

开发者在使用 Claude Code 写代码时，经常需要与同事/朋友沟通，但又不想被实时 IM（Slack、微信、Telegram）打断。本插件提供：

- **不打扰**：消息只在用户主动查看时呈现
- **去中心化**：不依赖任何公司服务器，朋友间直接通信
- **加密**：端到端加密，元数据最少化
- **集成 AI**：未来可让 Claude 总结、检索消息

### 1.3 设计哲学

1. **简单优于完美**：先做两人通信跑通，再考虑多人/多设备
2. **本地优先**：所有数据都在用户本地，没有云
3. **异步优先**：不追求实时，追求"最终送达"
4. **CLI 优先**：命令行原生体验，可被脚本和 AI 调用

### 1.4 非目标（明确不做的事）

- ❌ 语音、视频通话
- ❌ 多人群聊（v1 不做）
- ❌ 多设备消息同步（同一用户的多台机器）
- ❌ 实时推送/响铃通知
- ❌ Web UI / GUI
- ❌ 移动端

---

## 2. 用户故事

### 2.1 核心场景

**场景 A：发送异步消息**
```
Alice 正在写代码，想问 Bob 一个问题。
$ toxi send bob "你看下我刚 push 的 PR，有空回我"
✓ 已发送
（Alice 继续写代码，不等回复）
```

**场景 B：查看未读消息**
```
$ toxi unread
[3 条未读]
1. bob (10 分钟前): 看了，建议改改 errorhandler 的命名
2. carol (1 小时前): 周末爬山吗？
3. bob (2 分钟前): 还有 line 42 有个 typo
```

**场景 C：对方离线时发消息**
```
$ toxi send bob "晚安"
✓ 对方当前离线，将在其上线后自动发送（已加入本地队列）

$ toxi queue
[2 条待发]
- bob: "晚安" (5 分钟前加入队列)
- carol: "明早 9 点开会" (1 小时前加入队列)
```

**场景 D：添加朋友**
```
Bob 把他的 Tox ID 通过其他渠道（微信、邮件）告诉 Alice。

$ toxi add bob 76518406F6A9F2217E8DC487...（76 字符 Tox ID）
✓ 已添加 bob 到联系人。
  发送好友请求中... 等待对方接受。

# Alice 自己的 Tox ID：
$ toxi me
你的 Tox ID: A1B2C3D4E5F6...
（把这串发给朋友，他们就能添加你）
```

**场景 E：转发联系方式**
```
Alice 想把 Carol 介绍给 Bob：
$ toxi introduce bob carol
✓ 已向 bob 发送 carol 的联系方式

# Bob 收到：
$ toxi unread
[1 条联系方式邀请]
- alice 给你介绍了 carol (Tox ID: F1E2D3...)
  接受 [y/n]?
```

**场景 F：与 Claude 协同**
```
$ toxi ask "bob 上次说 errorhandler 的事是怎么改的？"
（Claude 在聊天历史里搜索并回答）

$ toxi send bob --draft-with-claude "帮我写一段感谢他帮忙 review 的话"
（Claude 起草，用户确认后发送）
```

### 2.2 用户画像

**主要用户**：
- 使用 Claude Code 的开发者
- 重视隐私、对去中心化感兴趣的技术人
- 小团队（2-10 人）的核心成员

**典型规模**：每个用户的好友列表 5-50 人

---

## 3. 总体架构

### 3.1 系统组件图

```
┌──────────────────────────────────────────────────────────┐
│  用户层                                                   │
│                                                          │
│  ┌─────────────────┐    ┌────────────────────────────┐   │
│  │  Claude Code    │    │  CLI 命令                   │   │
│  │  (聊天上下文)   │    │  $ toxi send / read / add  │   │
│  └────────┬────────┘    └─────────────┬──────────────┘   │
│           │                            │                 │
│           └────────────┬───────────────┘                 │
│                        │                                 │
│              IPC (Unix socket / Named pipe)              │
└────────────────────────┼─────────────────────────────────┘
                         │
┌────────────────────────▼─────────────────────────────────┐
│  Daemon 进程（常驻后台）                                  │
│                                                          │
│  ┌──────────────────────────────────────────────────┐    │
│  │  IPC Server                                       │    │
│  └──────────────────────┬───────────────────────────┘    │
│                         │                                │
│  ┌──────────────────────┴───────────────────────────┐    │
│  │  业务逻辑层                                       │    │
│  │  - 消息发送 / 接收 / 队列                         │    │
│  │  - 联系人管理                                     │    │
│  │  - 应用层协议处理（contact_share、ack 等）        │    │
│  └────┬──────────────────────────────────┬──────────┘    │
│       │                                  │               │
│  ┌────▼─────────────────┐    ┌──────────▼──────────┐     │
│  │  本地存储 (SQLite)   │    │  Tox 协议层          │     │
│  │  - messages          │    │  (py-toxcore-c)     │     │
│  │  - contacts          │    │  - DHT bootstrap    │     │
│  │  - queue             │    │  - 加密 / 直连      │     │
│  │  - settings          │    │  - NAT 穿透         │     │
│  └──────────────────────┘    └──────────┬──────────┘     │
└─────────────────────────────────────────┼────────────────┘
                                          │
                                  Tox UDP P2P
                                          │
                                          ▼
                              ┌────────────────────┐
                              │  Tox DHT 网络      │
                              │  + 朋友的 daemon   │
                              └────────────────────┘
```

### 3.2 进程模型

**两个进程**：

1. **`toxi-daemon`**：常驻后台进程
   - 启动方式：用户登录时通过 systemd / launchd / 任务计划程序启动
   - 持续运行 Tox 实例，维护 DHT 连接
   - 监听 IPC，处理 CLI 命令
   - 接收消息并写入 SQLite

2. **`toxi`**：用户每次输入的 CLI 命令
   - 短生命周期：执行一次命令就退出
   - 通过 IPC 和 daemon 通信
   - 格式化输出到终端

**为什么分两个进程？**
- Tox 协议必须持续在线才能保持 DHT 路由表和接收消息
- 用户的 CLI 命令是按需触发的，不能让用户保持一个 shell 永远开着
- IPC 通信比每次启动 Tox 实例快得多（启动 Tox 需要几秒重连 DHT）

### 3.3 技术栈选型

| 组件 | 选型 | 理由 |
|------|------|------|
| **语言** | Python 3.10+ | Claude Code 用户大多有 Python；ctypes 标准库可直绑 C 库 |
| **Tox 协议层** | **ctypes 直绑 libtoxcore** | py-toxcore-c 已验证不可用（见下），改用 stdlib `ctypes` 直接调 c-toxcore 的稳定 C ABI |
| **系统依赖** | libtoxcore（c-toxcore 0.2.x） | macOS `brew install toxcore`；Linux 用发行版包。唯一的非 Python 依赖 |
| **本地存储** | SQLite (stdlib) | 零依赖、单文件、足够 |
| **IPC** | Unix domain socket (Linux/macOS) / Named pipe (Windows) | 安全、快速、本机限定 |
| **CLI 框架** | Click | 子命令清晰 |
| **打包** | pipx | 用户安装一条命令 |

**为什么不用 py-toxcore-c（原选型）？**
- PyPI 上只有 0.2.0（2020 年，sdist + Cython）。它能在 Python 3.14 编出 wheel，但**运行时 `tox_bootstrap`、`tox_self_set_name` 直接段错误**——与 toxcore 0.2.22 存在 ABI/结构体不匹配。没有 bootstrap 就连不上 DHT，binding 实质不可用。
- spike 已验证：用 stdlib `ctypes` 直接绑 `libtoxcore` 可端到端跑通（两实例连 DHT、互加好友、收发消息正确）。只需绑约 10 个函数 + 4 个回调，依赖更干净（不依赖任何 PyPI 的 Tox 包）。

**为什么不用 Node.js？**
- Python 在科学计算和数据处理生态上更适合"和 Claude 协同"的未来功能
- c-toxcore 是稳定的 C 库，任意语言均可绑定，实现层面差异不大，未来可重写

### 3.4 源码结构（当前）

```
toxi/                            （GitHub: JefferyLee/toxi）
├── pyproject.toml               # hatchling；依赖 click；extras [mcp] [dev]
├── README.md / README.zh-CN.md  # 双语用户文档
├── docs/                        # 双语设计文档（本 PRD 在此）
├── src/toxi/             # 引擎（PyPI 发布名：toxi）
│   ├── paths.py                 # 配置目录；TOXI_HOME 覆盖供测试隔离
│   ├── db.py                    # SQLite schema（§4.1、§4.2、§4.5）+ 幂等 connect()
│   ├── ipc.py                   # 长度前缀 JSON 帧编解码（§4.6.2）
│   ├── client.py                # 极简 IPC 客户端（CLI 与测试共用）
│   ├── envelope.py              # 应用层消息封装（§4.2.3）
│   ├── config.py                # config.toml 读取（§4.8）
│   ├── tox.py                   # ctypes 直绑 libtoxcore（savedata 身份持久化）
│   ├── daemon.py                # 常驻进程（Tox 事件循环 + IPC + ACK 重试扫描）
│   ├── cli.py                   # toxi CLI（含 --json 全局开关、toxi mcp serve）
│   └── mcp_server.py            # FastMCP server，由 toxi mcp serve 启动（§4.12）
├── tests/                       # 40 个快测 + 5 个标记 dht 的集成测试
├── claude-code-plugin/          # Claude Code 插件（§4.12）
│   ├── .claude-plugin/plugin.json
│   ├── commands/                # /unread、/send、/contacts、/status
│   ├── hooks/hooks.json         # SessionStart 未读通知 hook
│   ├── hooks/unread_hook.py
│   └── .mcp.json                # 注册 toxi mcp serve
├── plugins/toxi/                # 实验性 Codex 插件（§4.12）
│   ├── .codex-plugin/plugin.json
│   ├── hooks/                   # Stop 状态摘要 hook
│   ├── skills/toxi/SKILL.md     # 可复用 Codex workflow 指令
│   └── .mcp.json                # 注册 toxi mcp serve
├── .agents/plugins/marketplace.json  # Codex repo marketplace 入口
├── .claude-plugin/marketplace.json  # 本仓库本身就是 marketplace
└── packaging/homebrew/toxi.rb    # tap formula 模板（§4.13）
```

约定：
- **daemon 是 SQLite 的唯一写者**；CLI 一律经 IPC 访问数据，不直接开库。
- `tox.py` 不依赖任何 PyPI Tox 包，运行时只需系统已装 libtoxcore。
- **代码（除 `docs/` 外的所有内容）全英文**；**文档双语**（英文文件为正本，`<name>.zh-CN.md` 为中文版，顶部互链）。
- **发布名 `toxi`**；Python 内部包名仍为 `toxi`，磁盘配置目录仍为 `~/.config/toxi/`（详见 §4.13）。

---

## 4. 详细设计

### 4.1 身份与联系人

#### 4.1.1 用户身份

每个用户的身份 = 一对 Curve25519 密钥（由 toxcore 生成）。
- **公钥（Tox ID）**：76 个十六进制字符，类似：
  ```
  76518406F6A9F2217E8DC487BCE0B22A1D8E68F50F3B9C8D...（共 76 字符）
  ```
  Tox ID 实际由 32 字节公钥 + 4 字节 nospam + 2 字节校验和组成。
- **私钥**：永远不离开本地，存在加密的 `tox_state.bin` 文件里。

**身份持久化**：
- 第一次启动 daemon 时生成密钥对
- 保存在 `~/.config/toxi/tox_state.bin`（默认无密码，可选加密）
- 用户可以通过 `toxi me` 查看自己的 Tox ID 分享给朋友

#### 4.1.2 联系人模型

```sql
CREATE TABLE contacts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    tox_id TEXT UNIQUE,                     -- 76 字符的完整 Tox ID；仅"主动添加对方"时已知，
                                            -- 经"接受请求"添加的联系人为 NULL（只拿得到公钥）
    public_key TEXT NOT NULL UNIQUE,       -- 64 字符纯公钥（始终已知，稳定标识）
    alias TEXT NOT NULL UNIQUE,            -- 用户给好友起的本地别名，如 'bob'
    display_name TEXT,                     -- 对方设置的名字（来自 Tox 协议）
    status_message TEXT,                   -- 对方的状态消息
    added_at INTEGER NOT NULL,             -- 添加时间（unix timestamp）
    added_by TEXT,                         -- 添加来源：'manual' / 'introduce:alice'
    last_seen INTEGER,                     -- 上次在线时间
    is_online BOOLEAN DEFAULT 0,
    friend_number INTEGER,                 -- toxcore 内部的 friend ID（重启会变）
    notes TEXT                             -- 用户私人备注
);
```

**好友请求只携带公钥**：Tox 好友请求只传 32 字节公钥（+ 一段文字），**不含完整 Tox ID**（Tox ID 还有 nospam + 校验和）。因此接受方拿不到对方的 tox_id，只能存 public_key——这就是上面 `tox_id` 可空的原因。待处理的好友请求另存一张表：

```sql
CREATE TABLE friend_requests (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    public_key TEXT NOT NULL UNIQUE,       -- 64 字符，请求方公钥
    message TEXT,
    received_at INTEGER NOT NULL,
    status TEXT NOT NULL                    -- 'pending' / 'accepted' / 'rejected'
);
```

**关键设计**：
- **alias 是本地的**：Alice 把朋友叫 "bob"，Bob 自己不知道；Bob 也可以把 Alice 叫 "boss"
- **alias 必须唯一**（在自己的联系人里），用作 CLI 命令的目标参数
- **friend_number 易变**：toxcore 重启后会重新分配，每次启动 daemon 时根据 public_key 重新建立映射

#### 4.1.3 添加好友流程

```
Alice 添加 Bob：

1. Alice 获得 Bob 的 Tox ID（线下、邮件、微信等任意渠道）
2. $ toxi add bob <bob_tox_id>
3. daemon 调用 tox_friend_add()，发送好友请求（可附带文字）
4. 请求通过 DHT 到达 Bob 的 daemon
5. Bob 看到请求：$ toxi requests
   - 公钥 A1B2...（64 字符）: "嗨我是 alice 加个好友"
6. Bob 接受并起别名：$ toxi accept alice <公钥前缀>
   daemon 用请求里的公钥调 tox_friend_add_norequest()
7. 双方都在联系人列表里出现对方（Bob 这边 alice 的 tox_id 为 NULL）
```

**重要细节**：
- 好友请求消息长度限制 1016 字节（Tox 协议限制）
- Bob 接受时需要给 Alice 起本地别名
- 如果 Bob 拒绝，Alice 不会收到通知（Tox 协议设计如此，保护拒绝方）

### 4.2 消息模型

#### 4.2.1 数据库 schema

```sql
CREATE TABLE messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    msg_uuid TEXT NOT NULL UNIQUE,         -- 应用层生成的 UUID，用于去重和 ack
    contact_id INTEGER NOT NULL,           -- FK to contacts
    direction TEXT NOT NULL,               -- 'in' / 'out'
    msg_type TEXT NOT NULL,                -- 'text' / 'contact_share' / 'ack' / 'system'
    content TEXT NOT NULL,                 -- 消息正文（JSON 或纯文本）
    created_at INTEGER NOT NULL,           -- 发送方时钟
    received_at INTEGER,                   -- 接收方收到时间
    status TEXT NOT NULL,                  -- 见状态机
    delivered_at INTEGER,                  -- 对方 ack 时间
    read_at INTEGER,                       -- 用户读取时间
    last_attempt_at INTEGER,               -- 上次（重）发时间，驱动 ACK 超时重试
    FOREIGN KEY (contact_id) REFERENCES contacts(id)
);

CREATE INDEX idx_messages_contact ON messages(contact_id, created_at DESC);
CREATE INDEX idx_messages_status ON messages(status);
```

#### 4.2.2 消息状态机

**出站消息（direction='out'）状态**：
```
queued ──┬──> sent ──> delivered ──> read
         │
         └──> failed
```

| 状态 | 含义 | 触发条件 |
|------|------|---------|
| `queued` | 在本地队列等待，对方离线 | `toxi send` 时对方不在线 |
| `sent` | 已通过 Tox 协议发出 | tox_friend_send_message 返回成功 |
| `delivered` | 对方 daemon 已收到并存盘 | 收到对方的 ack 消息 |
| `read` | 对方用户已查看 | 收到对方的 read receipt（可选功能） |
| `failed` | 重试多次失败 | 见 §4.4 重试策略 |

**入站消息（direction='in'）状态**：
```
received ──> read
```

| 状态 | 含义 |
|------|------|
| `received` | 已存到本地，未读 |
| `read` | 用户已通过 `toxi unread` 或 `toxi read` 查看 |

#### 4.2.3 应用层消息协议

Tox 协议只提供"发送字节流给某人"的能力。所有结构化语义都在应用层定义。

**消息封装格式**（JSON）：

```json
{
  "v": 1,                          // 协议版本
  "uuid": "550e8400-e29b-41d4-...", // 消息唯一 ID
  "type": "text",                  // 消息类型
  "ts": 1716700000,                // 发送方时间戳
  "data": { ... }                   // 类型特定的载荷
}
```

**消息类型**：

| type | data 内容 | 说明 |
|------|----------|------|
| `text` | `{"body": "你好"}` | 普通文字消息 |
| `ack` | `{"ref_uuid": "..."}` | 送达确认 |
| `read_receipt` | `{"ref_uuid": "..."}` | 已读回执（可选） |
| `contact_share` | `{"tox_id": "...", "suggested_alias": "carol", "from_alias": "alice"}` | 转发联系方式 |
| `typing` | `{}` | （未来）正在输入提示 |
| `presence` | `{"status": "busy"}` | （未来）状态消息 |

**Tox 单条消息长度限制**：1372 字节（MAX_MESSAGE_LENGTH）。

如果文本超长，应用层需要分片：
```json
{
  "v": 1, "uuid": "...", "type": "text",
  "data": {
    "body": "...",
    "chunk": 1, "total_chunks": 3, "chunk_id": "..."
  }
}
```

但 v1 阶段不做分片。**实现按编码后字节数校验**：整条 envelope（JSON）编码后须 ≤ `TOX_MAX_MESSAGE_LENGTH`（1372 字节），超出则 `send` 直接报 `MESSAGE_TOO_LONG`。按字节而非字符计，是因为 CJK 等多字节文本同样的字符数会占更多字节。

### 4.3 离线消息队列

#### 4.3.1 核心问题

**Tox 协议本身不支持离线消息**。`tox_friend_send_message()` 必须在对方在线时调用，否则消息丢失。

**解决方案**：在**发送方**本地维护队列，等对方上线后重发。

#### 4.3.2 队列设计

队列就是 `messages` 表中 `direction='out' AND status='queued'` 的行。

```sql
CREATE INDEX idx_queue ON messages(contact_id, status, created_at)
WHERE status = 'queued';
```

#### 4.3.3 发送流程

```python
def send_message(contact_alias, body):
    contact = db.get_contact(alias=contact_alias)
    msg_uuid = generate_uuid()
    envelope = {
        "v": 1, "uuid": msg_uuid, "type": "text",
        "ts": int(time.time()), "data": {"body": body}
    }
    payload = json.dumps(envelope)

    # 先存盘（持久化优先）
    db.insert_message(
        msg_uuid=msg_uuid,
        contact_id=contact.id,
        direction='out',
        msg_type='text',
        content=body,
        status='queued',
        created_at=envelope['ts']
    )

    # 尝试立即发送
    if contact.is_online:
        try:
            tox.friend_send_message(contact.friend_number, payload)
            db.update_message_status(msg_uuid, 'sent')
            return "已发送"
        except ToxError as e:
            # 对方"在线"状态可能过期，保持 queued
            log.warning(f"send failed: {e}")
            return f"对方刚刚离线，已加入队列"
    else:
        return "对方离线，已加入队列"
```

#### 4.3.4 上线触发重发

利用 Tox 的回调 `friend_connection_status`：

```python
def on_friend_connection_status(friend_number, connection_status):
    """toxcore 回调：朋友上线/下线"""
    contact = db.get_contact(friend_number=friend_number)
    if connection_status != TOX_CONNECTION_NONE:
        # 对方上线，触发队列重发
        contact.is_online = True
        db.update(contact)
        flush_queue(contact.id)
    else:
        contact.is_online = False
        db.update(contact)

def flush_queue(contact_id):
    """把这个朋友的所有 queued 消息按顺序发出去"""
    queued = db.get_messages(
        contact_id=contact_id,
        status='queued',
        order_by='created_at ASC'
    )
    contact = db.get_contact(id=contact_id)

    for msg in queued:
        envelope = build_envelope_from_db(msg)
        try:
            tox.friend_send_message(contact.friend_number, json.dumps(envelope))
            db.update_message_status(msg.msg_uuid, 'sent')
        except ToxError:
            # 发送失败，保持 queued 状态，下次再试
            break  # 不继续发，避免顺序错乱
```

#### 4.3.5 接收方处理

```python
def on_friend_message(friend_number, message_text):
    """toxcore 回调：收到消息"""
    try:
        envelope = json.loads(message_text)
    except json.JSONDecodeError:
        # 非本协议消息，可能是其他 Tox 客户端发来的，当作纯文本存
        envelope = {"v": 1, "type": "text", "data": {"body": message_text}}

    if envelope.get("type") == "text":
        store_incoming_text(friend_number, envelope)
        send_ack(friend_number, envelope["uuid"])
    elif envelope.get("type") == "ack":
        handle_ack(friend_number, envelope["data"]["ref_uuid"])
    elif envelope.get("type") == "contact_share":
        handle_contact_share(friend_number, envelope)
    # ...
```

#### 4.3.6 送达确认（ACK）

发送方需要知道消息真的进了对方的本地存储（不仅仅是 Tox 协议层送达）。

```
Alice → Bob: {type: "text", uuid: "X", data: {body: "你好"}}
Bob 的 daemon: 存到 SQLite，立即回 ACK
Bob → Alice: {type: "ack", data: {ref_uuid: "X"}}
Alice 的 daemon: 把消息 X 的状态从 sent 改为 delivered
```

**为什么需要应用层 ACK？**
- Tox 协议的"已发送"只表示 UDP 包发出去，不代表对方进程真的处理了
- 对方进程可能崩溃、磁盘满、有 bug

### 4.4 重试与可靠性

#### 4.4.1 重试策略

```
queued 状态的消息：
  - 仅在对方上线时尝试发送
  - 没有指数退避（因为是基于事件触发，不是轮询）

sent 状态的消息（已发送但未收到 ack）：
  - 5 分钟后未收到 ack → 重发一次（标记为 sent_retry）
  - 30 分钟后未收到 ack → 重发一次
  - 24 小时后未收到 ack → 标记为 failed，提示用户

去重：
  - 接收方根据 msg_uuid 去重（同一 uuid 只存一次）
  - 但仍然回 ACK（让发送方知道）
```

**v1 实现简化**：不做 5/30 分钟分阶段、也无独立 `sent_retry` 状态。daemon 每 30 秒扫一次未确认的 `sent` 消息——好友在线且距 `last_attempt_at` 超过 `ack_timeout_minutes` 就重发（重发用原 uuid，接收方去重后照样回 ACK 以恢复 `delivered`）；距 `created_at` 超过 `fail_after_hours` 则标记 `failed`。阈值读 `config.toml [retry]`（`max_retries` 暂未使用）。

#### 4.4.2 NAT 映射保活

Tox DHT 节点会自动发心跳保活。但应用层也可以增强：
- 每 4 小时对所有在线朋友发一个 `presence` 消息
- 实测中 toxcore 已经做得很好，应用层不强求

#### 4.4.3 daemon 崩溃恢复

- 所有状态都在 SQLite 里持久化
- 重启 daemon 时：
  1. 加载 Tox state（密钥、好友列表）
  2. 连接 DHT bootstrap 节点
  3. 等待 DHT 连接建立（`self_connection_status` 回调）
  4. 等待朋友上线回调
  5. 自动 flush queue

### 4.5 联系方式转发

#### 4.5.1 业务流程

```
Alice 想把 Carol 介绍给 Bob：

1. $ toxi introduce bob carol
2. daemon 检查：
   - bob 在我的联系人？✓
   - carol 在我的联系人？✓
3. 构造 contact_share 消息：
   {
     "type": "contact_share",
     "data": {
       "tox_id": "<carol's full tox id>",
       "suggested_alias": "carol",      // alice 本地对 carol 的称呼
       "from_alias": "alice",           // alice 希望 bob 怎么记得这是谁推荐的
       "note": "我同事"                 // 可选介绍语
     }
   }
4. 发送给 bob（走正常的队列+重试逻辑）

Bob 的 daemon 收到后：
5. 不自动添加，进入"待审核"队列
6. Bob: $ toxi introductions
   - alice 给你介绍了 carol (Tox ID: F1E2...)
     备注: 我同事
     接受并起别名 [n/y/rename]?
7. Bob: $ toxi accept-intro alice carol  # 用默认 alias
   或 $ toxi accept-intro alice carol --alias=co_carol
8. daemon 发好友请求给 carol
9. Carol 那边像普通好友请求一样处理
```

#### 4.5.2 数据库扩展

```sql
CREATE TABLE pending_introductions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    from_contact_id INTEGER NOT NULL,      -- 谁介绍的
    introduced_tox_id TEXT NOT NULL,       -- 被介绍人的 Tox ID
    suggested_alias TEXT,
    note TEXT,
    received_at INTEGER NOT NULL,
    status TEXT NOT NULL,                  -- 'pending' / 'accepted' / 'rejected'
    FOREIGN KEY (from_contact_id) REFERENCES contacts(id)
);
```

#### 4.5.3 安全考虑

- **不自动加好友**：必须用户显式确认，防止恶意介绍
- **保留来源**：在新加联系人的 `added_by` 字段记录 `introduce:alice`
- **可拒绝**：拒绝不通知 Alice（隐私保护，对称于好友请求）

**v1 实现说明**：
- **只能转介拥有完整 Tox ID 的联系人**：给被介绍人发好友请求需要 38 字节完整地址（含 nospam），所以 `tox_id` 为 NULL 的联系人（经"接受请求"添加）无法被转介，`introduce` 报 `NO_TOX_ID`。
- **introduce 要求接收方在线**：contact_share 直接发送、不入消息队列（队列目前只处理 text），接收方离线则报 `RECIPIENT_OFFLINE`。
- `accept-intro <from> <whom>` 的 `whom` 用 `suggested_alias` 定位是哪条介绍；新本地别名默认取 `whom`，可用 `--alias` 覆盖。

### 4.6 IPC 协议设计

#### 4.6.1 传输

- **Linux/macOS**：Unix domain socket，路径 `~/.config/toxi/daemon.sock`
- **Windows**：Named pipe，`\\.\pipe\claude-toxi-daemon`
- **权限**：仅当前用户可读写（0600）

#### 4.6.2 消息格式

Length-prefixed JSON：

```
[4 bytes: payload length (big-endian uint32)][payload: JSON]
```

**请求**：
```json
{
  "id": "req-001",         // 客户端生成，用于匹配响应
  "method": "send_message",
  "params": {
    "alias": "bob",
    "body": "你好"
  }
}
```

**响应**：
```json
{
  "id": "req-001",
  "result": {
    "msg_uuid": "550e8400-...",
    "status": "queued",
    "message": "对方离线，已加入队列"
  }
}
```

**错误**：
```json
{
  "id": "req-001",
  "error": {
    "code": "CONTACT_NOT_FOUND",
    "message": "找不到联系人: bob"
  }
}
```

#### 4.6.3 RPC 方法列表

| 方法 | 参数 | 返回 |
|------|------|------|
| `get_me` | — | `{tox_id, name, status}` |
| `set_name` | `{name}` | OK |
| `add_contact` | `{tox_id, alias, request_msg?}` | OK |
| `accept_request` | `{public_key, alias}` | OK |
| `list_contacts` | `{}` | `[{alias, tox_id, online, last_seen}]` |
| `send_message` | `{alias, body}` | `{msg_uuid, status}` |
| `get_messages` | `{alias?, unread_only?, limit?}` | `[messages]` |
| `mark_read` | `{msg_uuids: [...]}` | `{marked}` |
| `list_queue` | `{}` | `[{alias, body, queued_at}]` |
| `introduce` | `{to_alias, contact_alias, note?}` | OK |
| `list_introductions` | `{}` | `[pending_intros]` |
| `accept_introduction` | `{from_alias, introduced_tox_id, alias}` | OK |
| `get_status` | — | `{dht_connected, friends_online, queue_size}` |

#### 4.6.4 服务器端推送（可选 v2）

v1 用 polling 模式（CLI 每次主动查询）。
v2 可加 server-sent events，让 CLI 工具订阅消息流。

### 4.7 CLI 命令规范

**独立开关**（不需要子命令）：
- `toxi --version` / `toxi -V` —— 打印引擎版本并退出。读自 `src/toxi/__init__.py` 的 `__version__`；按 §4.13，这个值在 `pyproject.toml`、两个 plugin manifest 之间锁住一致，`tests/test_versions.py` 强制校验。

**全局开关**（放在子命令之前）：
- `toxi --json <cmd>` —— 让任意读命令（`me`/`status`/`contacts`/`requests`/`unread`/`read`/`queue`/`introductions`/`send`）输出机器可读 JSON 而不是给人看的格式。`--json` 模式下 `unread`/`read` 是**只读 peek**,**不会**标已读 —— 让 hook 或 MCP 工具能取数而不消耗未读状态。

完整命令列表：

```bash
# 身份相关
toxi init                      # 首次初始化，生成密钥
toxi me                        # 显示自己的 Tox ID 和名字
toxi set-name "Alice"          # 设置展示名

# 联系人管理
toxi add <alias> <tox_id>      # 添加好友
toxi accept <alias> <pubkey>   # 接受好友请求（pubkey 为请求方公钥前缀）
toxi requests                  # 查看待处理的好友请求
toxi contacts                  # 列出所有联系人
toxi contacts --online         # 仅在线的
toxi remove <alias>            # 删除联系人

# 消息收发
toxi send <alias> <message>    # 发消息
toxi send <alias> -            # 从 stdin 读
toxi unread                    # 显示所有未读
toxi unread <alias>            # 某人的未读
toxi read <alias>              # 看历史（默认最近 20 条）
toxi read <alias> --limit 50
toxi queue                     # 待发队列

# 联系方式转发
toxi introduce <to> <whom>     # 介绍朋友
toxi introductions             # 收到的介绍
toxi accept-intro <from> <whom> [--alias=...]

# 系统
toxi status                    # 显示 daemon 状态、DHT 连接、好友在线情况
toxi daemon start/stop/restart
toxi daemon logs
toxi mcp serve                 # 通过 stdio 跑 MCP server（见 §4.12）
toxi setup-engine              # 只创建身份 + 启动 daemon
toxi setup-claude              # 只接入 Claude Code 状态栏 + 安装提示
toxi setup-codex               # 只接入 Codex MCP + 插件
toxi doctor-codex              # 只读检查 Codex 接入状态
toxi teardown-codex            # 移除 Codex 接入，保留身份/历史

# Claude 协同
# v0.1 已完成：--json 开关、SessionStart 未读 hook、slash 命令、MCP server（§4.12）
# 仍在 v2：
toxi ask <question>            # 让 Claude 在历史里搜
toxi send <alias> --draft-with-claude <prompt>
```

### 4.8 数据存储布局

```
~/.config/toxi/
├── tox_state.bin              # Tox 内部状态（密钥、好友列表）
├── chat.db                    # SQLite 主数据库
├── daemon.sock                # IPC socket（Linux/macOS）
├── daemon.pid                 # 进程 PID
├── daemon.log                 # 日志
├── config.toml                # 用户配置
└── bootstrap.json             # DHT bootstrap 节点列表
```

**config.toml 示例**：
```toml
[daemon]
log_level = "info"

[tox]
udp_enabled = true
ipv6_enabled = true
# 可选：代理（走 Tor）
# proxy_type = "socks5"
# proxy_host = "127.0.0.1"
# proxy_port = 9050

[ui]
default_history_limit = 20
show_timestamps = true
notify_on_receive = false      # 不打扰原则，默认关

[retry]
ack_timeout_minutes = 5
max_retries = 3
fail_after_hours = 24
```

### 4.9 安全设计

#### 4.9.1 信任模型

| 数据 | 加密保护 | 谁能看到 |
|------|---------|---------|
| 消息内容 | E2EE（Tox 协议层） | 仅发送方和接收方的 daemon |
| 消息元数据 | 部分 | DHT 上的中继节点能看到"A 和 B 有通信"（IP 层） |
| 本地数据库 | 无（v1） | 任何能访问用户文件的进程 |
| Tox 私钥 | 无（v1） | 同上 |

#### 4.9.2 v1 已知限制

- **本地数据库未加密**：依赖文件系统权限保护
- **私钥未加密**：如果用户机器被入侵，身份被盗
- **IP 暴露**：和你聊天的人能看到你的公网 IP（Tox 协议特性，除非走 Tor）

#### 4.9.3 v2 改进方向

- 用 master password 加密 `tox_state.bin` 和 `chat.db`
- 集成 Tor 代理选项
- 实现"完美前向保密"的密钥轮换（Tox 协议有限支持）

#### 4.9.4 抗滥用

- 拒绝来自陌生人的消息（必须先成为好友）
- 好友请求限速（每分钟最多接收 5 个）
- 大消息拒收（>10KB 的应用层消息）

### 4.10 错误处理

**daemon 启动失败的情况**：

| 情况 | 处理 |
|------|------|
| 端口/socket 被占 | 错误退出，提示用户检查是否已有 daemon 运行 |
| DHT 节点全部连不上 | 重试，告警，但 daemon 继续运行（可能稍后能连） |
| Tox state 文件损坏 | 备份后提示用户，无法自动恢复（密钥丢失就丢了） |
| 数据库损坏 | 备份后尝试 VACUUM 修复，失败则人工介入 |

**消息发送失败**：

| 情况 | 处理 |
|------|------|
| 对方离线 | 加入 queue（正常） |
| 消息超长 | CLI 端拒绝（提示用户分段） |
| 对方非好友 | 拒绝（提示先 add） |
| Tox 错误（罕见） | 状态保持 queued，下次重试 |

### 4.11 日志与可观测性

**日志级别**：
- `error`：异常需要用户注意
- `warning`：非致命问题
- `info`：常规事件（朋友上下线、消息收发数量）
- `debug`：协议细节（默认关）

**日志位置**：`~/.config/toxi/daemon.log`，rotate at 10MB，保留 5 个文件。

**敏感信息处理**：
- 日志**不记录**消息正文
- 日志**记录**消息元数据：uuid、对端 public_key 前 8 字符、长度、时间

**`toxi status` 命令输出**：
```
$ toxi status
Daemon: running (PID 12345)
Uptime: 3d 2h 15m
DHT: connected (UDP)
Tox ID: A1B2C3D4...
Display Name: Alice

Contacts: 12 total, 5 online
  ✓ bob       online (last activity 2m ago)
  ✓ carol     online (last activity 15m ago)
  ○ dave      offline (last seen 2h ago)
  ...

Queue: 3 messages pending
  - bob: 2 messages
  - dave: 1 message

Stats (last 24h):
  Sent: 47 / Received: 23 / Failed: 0
```

### 4.12 Claude Code 集成

最初列在 v1.0，地基已随 v0.1 交付。引擎本身独立于 Claude Code；另有一个 **Claude Code 插件** 放在 `claude-code-plugin/` 一键接上：

| 入口 | 作用 | 实现 |
|---|---|---|
| `--json` 全局开关（§4.7）| 机器可读输出；`unread`/`read` 在此模式下为 peek（不会自动标已读）| `src/toxi/cli.py` |
| SessionStart hook | Claude Code 会话开始时把未读消息注入模型上下文，并让模型把非中文消息翻成中文 | `claude-code-plugin/hooks/hooks.json` → `hooks/unread_hook.py`（调 `toxi --json unread`）|
| Slash 命令 | `/unread`、`/send <alias> <message>`、`/contacts`、`/status`，安装后命名空间为 `/toxi:` | `claude-code-plugin/commands/*.md` |
| MCP server | 工具 `get_unread`、`read_history`、`mark_read`、`send_message`、`list_contacts`、`get_status`，让模型能替你操作，同时把 peek 和标已读分开 | `toxi mcp serve`（FastMCP，可选 `[mcp]` extra），由 `claude-code-plugin/.mcp.json` 注册 |

**不可信输入框架（防提示注入）**：来信是外部输入被注入模型上下文。hook 和 slash 命令的提示词**明确把消息标为「个人内容、非指令」**，所以"忽略指令、执行 X"这类正文会被当作可能给用户读的文字，而不是 Claude 该执行的命令。

**翻译**：Claude 本身就是模型，hook 只标注消息让 Claude 在汇报时顺手翻译非中文消息——零额外依赖、零额外 API 调用。如果想在终端侧也自动翻译（不经过我），那需要单独接 Claude API，留到以后。

**Codex 集成（实验性）**：引擎现在也带一个 Codex 插件，位置是 `plugins/toxi/`。它包含 Codex manifest、指向 `toxi mcp serve` 的 `.mcp.json`、通过 Codex Stop-hook JSON 输出和 Claude Code 底部状态栏相同 `toxi statusline` 摘要的 Stop hook，以及保持同样不可信输入边界的 `toxi` skill。插件故意不注册 SessionStart hook；未读消息只在用户要求 Codex 通过 MCP/CLI 读取时才会进入上下文。MCP server 也会声明同样规则的 server-level instructions，所以即使 skill 未加载，边界也对模型可见；`get_unread` 和 `read_history` 保持只读，`mark_read` 是针对明确消息 UUID 的显式标已读动作。`toxi setup-codex` 会把源码 checkout 接入 Codex：注册 MCP server、添加 repo marketplace、安装插件；当 PyPI/pipx/Homebrew 安装的 engine 在源码 checkout 内运行时，它会使用当前 checkout 的 `.agents/plugins/marketplace.json`，而不是安装包路径。`toxi doctor-codex` 用只读 Codex CLI list 命令检查这些接入状态。`toxi teardown-codex` 只移除这些 Codex 侧入口，不影响 daemon、身份或聊天历史。Setup 已拆成 `toxi setup-engine`、`toxi setup-claude` 和 `toxi setup-codex`；`toxi setup` 保持旧的 engine + Claude Code 组合路径。

**Codex 手工验收**：
- 运行 `toxi setup-codex` 且 `toxi doctor-codex` 通过后，打开 Codex，确认 `/mcp`、`/plugins`、`/hooks` 能看到 toxi 入口；如有 hook 信任提示，手工审核并确认。

### 4.13 发布与命名

| 层 | 名字 | 安装方式 |
|---|---|---|
| 引擎（PyPI）| `toxi` | `pipx install 'toxi[mcp]'`（0.4.0 起已发布） |
| 引擎（Homebrew）| `toxi` | `brew install <owner>/tap/toxi` —— formula `depends_on "toxcore"`，所以 libtoxcore 会被一起拉下来。模板在 `packaging/homebrew/toxi.rb` |
| Claude Code 插件 | `toxi` | `toxi setup-claude` 接状态栏并输出安装提示；`/plugin marketplace add JefferyLee/toxi` 然后 `/plugin install toxi@toxi`；开发期可用 `claude --plugin-dir ./claude-code-plugin` |
| Codex 插件 | `toxi` | 本地 checkout 中运行：`toxi setup-codex`；用 `toxi doctor-codex` 检查；用 `toxi teardown-codex` 移除；repo marketplace 入口在 `.agents/plugins/marketplace.json`；插件文件在 `plugins/toxi/` |

**版本策略**：引擎和随仓库发布的插件共用一个发布版本，来源是 `pyproject.toml`。`src/toxi/__init__.py`、`claude-code-plugin/.claude-plugin/plugin.json`、`plugins/toxi/.codex-plugin/plugin.json` 必须与它一致；`tests/test_versions.py` 会校验这个约束，保证 marketplace 安装与 engine upgrade 描述的是同一个 checkout。

**测试隔离**：普通 `pytest` 默认跳过会构造真实 libtoxcore handle 或连接公网 Tox DHT 的测试。使用 `pytest --run-toxcore` 跑本地 toxcore/daemon 测试，使用 `pytest --run-dht` 跑慢速公网 DHT 集成测试。

**为什么叫 `toxi`**：引擎最早叫 `chat`，后来为了和通用 `chat` 包区分改成 `cc-chat`。这两个名字都把 Claude Code 写死在品牌里，和长期方向不符——引擎本身与 AI 工具解耦，未来计划支持 Codex、Grok Builder 等。`toxi` 保留 Tox 血统（Tox + i），不再把品牌绑死在某个 AI 工具上。PyPI 上 `toxi` 可用；`cc-chat` 在 PyPI 也未被占用，但新名字更短、更好记、且不绑定单一 AI 工具。
- 磁盘配置目录 `~/.config/toxi/`（改它会丢用户现有的 Tox 身份与消息历史）。

---

## 5. 实现路径

### 5.1 MVP（v0.1）

**目标**：两个朋友能装上、加好友、互发文字消息、离线缓存生效。

**Scope**：
- ✅ 单平台（**macOS 优先**，开发机为 macOS；Linux 留到 v0.2）
- ✅ daemon + CLI 双进程
- ✅ 添加好友、发消息、读消息、队列
- ✅ 联系方式转发
- ✅ SQLite 持久化
- ❌ 加密本地存储
- ❌ Claude 协同功能
- ❌ Tor 集成
- ❌ Windows 支持

**预估代码量**：约 1500 行 Python

**预估时间**：2-3 周（业余）

**当前进度**（垂直薄切片，每步一个可验证里程碑）：
- ✅ step 0 Tox spike：ctypes 直绑 libtoxcore，两实例端到端收发验证通过
- ✅ step 1 脚手架：包结构 / paths / db / ipc / tox 绑定 + 测试（9 快测 + 1 DHT 集成，全过）
- ⬜ step 2 daemon 骨架：Tox 事件循环 + bootstrap + IPC server（get_me/get_status）
- ⬜ step 3 CLI 骨架：init/me/status → 里程碑 `toxi me`
- ✅ step 4 联系人：add/accept/requests/contacts + 好友回调；两 daemon 真实 DHT 互加（里程碑达成）
- ✅ step 5 在线消息：envelope 协议 + send/unread/read/queue；在线消息往返入库（里程碑达成）
- ✅ step 6 离线队列：上线回调触发 flush，按序重发；离线 10 条上线后顺序全收（里程碑达成，= §8 指标②）
- ✅ step 7 ACK / 送达状态机：接收方回 ack→发送方 sent→delivered；超时重发、超期 failed（里程碑达成）
- ✅ step 8 introduce：contact_share + pending_introductions + accept-intro；Alice 介绍 Carol 给 Bob，Bob 成功连上 Carol（里程碑达成，= §8 指标③）
- ✅ step 9 收尾：README 安装文档、丰富的 `toxi status`（§4.11 格式）、日志轮转（10MB×5，§4.11）、CLI 错误信息打磨（只显示人类可读消息）
- ✅ step 10 Claude Code 集成 + 打包 + 改名（§4.12、§4.13）：`--json` 开关；SessionStart 未读 hook（含翻译 + 防提示注入框架）；slash 命令；MCP server（`toxi mcp serve`）；全部打包为 `claude-code-plugin/` 下的 Claude Code 插件；仓库本身就是 marketplace（`.claude-plugin/marketplace.json`）；brew tap formula 模板；发布名 `toxi` → `toxi`；文档双语化（英文 `.md` + `.zh-CN.md`）

### 5.2 v0.2

- macOS 支持
- 更好的错误消息和文档
- daemon 的 systemd / launchd 集成
- 基本的单元测试和集成测试

### 5.3 v0.3

- Windows 支持（Named pipe IPC）
- 本地数据库加密（master password）
- Tor 代理选项
- 消息搜索（`toxi search <keyword>`）

### 5.4 v1.0

- ✅ Claude Code 原生 hook，工作时主动报告新消息 —— 已在 v0.1 交付（§4.12）
- ⬜ `toxi ask`（Claude 在本地消息历史里检索）
- ⬜ `toxi send --draft-with-claude`（Claude 起草、用户确认）
- ⬜ 完善的文档和示例

### 5.5 远期（v2+）

- 多设备同步（一个身份在多台机器上）
- 群聊（基于 Tox conferences 或自建协议）
- 文件传输
- 移动端 / Web UI

---

## 6. 待决策的问题

以下是设计中的开放问题，需要在实现前确定：

1. **CLI 包装：用什么交互模型？**
   - A: 纯命令式（`toxi send bob "..."` 一条一条）
   - B: 加一个 REPL 模式（`toxi shell` 进入会话）
   - 暂定 A，未来加 B

2. **多个未读如何呈现？**
   - 按时间排序还是按联系人分组？
   - 暂定按时间排序，加 `--by-contact` 选项

3. **`toxi send` 的输入安全**
   - 是否要避免 shell 历史泄露敏感消息？
   - 暂定加 `--from-file` 和 `--stdin` 选项，敏感消息用这两个

4. **DHT bootstrap 节点选择**
   - 用 Tox 社区公开节点列表
   - 是否允许用户自定义？
   - 暂定允许在 config.toml 配置

5. **第一次启动的引导**
   - 是否需要 `toxi init` 显式步骤？
   - 还是 daemon 启动时自动生成？
   - 暂定 `toxi init` 显式触发，避免误启动

6. **如果朋友的 Tox ID 改了**（重新生成密钥）
   - 应用层如何识别"还是同一个人"？
   - v1：不识别，用户手动 remove + add
   - v2：可选基于"信任链"的身份证明

---

## 7. 风险与缓解

| 风险 | 影响 | 缓解 |
|------|------|------|
| Tox 协议生态衰落 | 长期维护困难 | 协议简单稳定，最坏情况可 fork c-toxcore |
| ~~py-toxcore-c 缺乏维护~~（已发生）| binding 出 bug | **已规避**：spike 确认 py-toxcore-c 0.2.0 段错误不可用，改为 ctypes 直绑 libtoxcore 的稳定 C ABI；协议层与具体 binding 解耦，未来可再换 |
| 用户机器 NAT 太严，DHT 都连不上 | 完全不可用 | 提供 TURN 类似的 TCP relay（Tox 内置） |
| 朋友间互相不知道对方在不在线 | 体验差 | daemon 显示 last_seen 帮助判断 |
| 消息丢失（队列文件损坏） | 信任度下降 | 写入前 fsync，每条消息独立事务 |
| 性能：长期使用后 SQLite 巨大 | 启动慢 | 6 个月以上消息自动归档，可选删除 |

---

## 8. 成功指标

v0.1 MVP 的成功标准：

- ✅ 两个开发者能在家庭网络上互装、互加好友
- ✅ Alice 给离线的 Bob 发 10 条消息，Bob 上线后全部收到，顺序正确
- ✅ Alice 介绍 Carol 给 Bob，Bob 能成功添加 Carol
- ✅ daemon 7 天 24 小时运行无崩溃
- ✅ 安装到能发第一条消息 < 5 分钟

---

## 附录 A：参考资料

- Tox 协议规范：https://toktok.ltd/spec.html
- c-toxcore 源码：https://github.com/TokTok/c-toxcore
- toxcore C 头文件（实际绑定依据）：`<libtoxcore prefix>/include/tox/tox.h`
- ~~py-toxcore-c~~（已弃用，段错误不可用）：https://github.com/TokTok/py-toxcore-c
- Tox bootstrap 节点列表：https://nodes.tox.chat
- WebRTC NAT 穿透相关讨论：（本对话历史）

## 附录 B：术语表

| 术语 | 解释 |
|------|------|
| Tox ID | 76 字符的好友身份标识，公钥 + nospam + 校验和 |
| DHT | Distributed Hash Table，分布式哈希表，用于节点发现 |
| Daemon | 后台常驻进程 |
| IPC | Inter-Process Communication，进程间通信 |
| ACK | Acknowledgement，送达确认 |
| MVP | Minimum Viable Product，最小可行产品 |
| E2EE | End-to-End Encryption，端到端加密 |
| NAT | Network Address Translation，网络地址转换 |

---

**文档版本历史**

| 版本 | 日期 | 变更 |
|------|------|------|
| v0.1 draft | 2026-05-26 | 初版 |
| v0.1 | 2026-05-26 | 按 step 0/1 结果更新：Tox 层 py-toxcore-c → ctypes 直绑 libtoxcore（§3.3、§7）；新增已实现源码结构（§3.4）；平台改为 macOS 优先并加入进度（§5.1）|
| v0.1 | 2026-05-26 | 按 step 2/3/4 结果更新：daemon/CLI 骨架完成（§5.1 进度）；联系人模型修正——`tox_id` 可空、新增 `friend_requests` 表（§4.1.2），`toxi accept` 改用公钥（§4.1.3、§4.6.3、§4.7），因好友请求只携带公钥 |
| v0.1 | 2026-05-26 | 按 step 5 结果更新：在线消息完成（envelope + send/unread/read/queue）；消息长度改为按编码字节校验 ≤1372（§4.2.3）；§5.1 进度 |
| v0.1 | 2026-05-26 | 按 step 6 结果更新：离线队列 + 上线 flush 完成（§5.1 进度）；离线 10 条上线后顺序全收，达成 §8 指标② |
| v0.1 | 2026-05-26 | 按 step 7 结果更新：ACK 送达状态机（§4.3.6 回 ACK、sent→delivered）；新增 `messages.last_attempt_at`（§4.2.1）；重试简化为「在线超时重发 / 超期 failed」并读 config.toml（§4.4.1）|
| v0.1 | 2026-05-26 | 按 step 8 结果更新：introduce 完成（§5.1 进度，达成 §8 指标③）；补 v1 实现约束——只能转介有完整 Tox ID 的联系人、introduce 要求接收方在线（§4.5.3）|
| v0.1 | 2026-05-26 | 按 step 9（部分）更新：新增 README 安装文档；`toxi status` 丰富为 §4.11 格式（§5.1 进度）|
| v0.1 | 2026-05-26 | step 9 收尾完成：日志轮转 10MB×5（§4.11）、config.toml 支持 `[daemon] log_level`、CLI 错误只显示人类可读消息（§5.1 进度）。v0.1 MVP 全部步骤完成 |
| v0.1 | 2026-05-27 | 与当前现状对齐：标题与发布名 → `toxi`；§3.4 源码结构更新为当前实情；§4.7 CLI 加入 `--json` 与 `toxi mcp serve`；新增 §4.12 Claude Code 集成、§4.13 发布与命名；§5.1 进度补 step 10；§5.4 v1.0 标记原生 hook 已交付；PRD 迁入 `docs/` |
