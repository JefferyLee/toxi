# 架构

> English version: architecture.md
> 设计背景与决策：[agent-comm-design.zh-CN.md](agent-comm-design.zh-CN.md)。线格式：[protocol.zh-CN.md](protocol.zh-CN.md)。

toxi 是 agent 与人之间的一层通信层。它不需要服务器。每个参与者运行一个小 daemon，daemon 持有一个 Tox 身份。适配器把这个 daemon 接入 agent 的运行时：Claude Code、Codex、VM 上的 bot 或手机。

## 四层

```
┌─ L4 适配器 ──────────────────────────────────────────────────────────────────────────┐
│ Claude Code 插件 │ Codex 插件 │ headless：toxi agent run / webhook │ 人：CLI、ToxiOS │
│  hooks + MCP     │ hooks + MCP│                                    │                 │
└─────────┬────────┴─────┬──────┴─────────────────┬──────────────────┴────────┬────────┘
          │   IPC：请求/响应 + 事件订阅（Unix socket，长度前缀 JSON）
┌─ L2 引擎：toxi-daemon ─────────────────────────────────────────────────────┐
│ 身份 · 联系人 · 身份卡片 · peer 信任 · 分片重组 · 回执 ·                   │
│ 队列/重试 · room · 文件 · 归档/搜索 · 事件总线 · SQLite · inbox.flag       │
└──────────────────────────────────┬─────────────────────────────────────────┘
┌─ L3 协议：信封 v2（承载于 L1，由 L2 实现） ────────────────────────────────┐
│ text(+session, part, thread, reply_to, kind, …) · profile · receipt · ack · room · file │
└──────────────────────────────────┬─────────────────────────────────────────┘
┌─ L1 传输：Tox ─────────────────────────────────────────────────────────────┐
│ DHT · 端到端加密 · NAT 穿透 · 仅限好友 · 文件传输                          │
└────────────────────────────────────────────────────────────────────────────┘
```

- **L1 Tox** 提供身份、加密、发现与 NAT 穿透。在 Tox 中，一个身份就是一对密钥。toxi 通过 `ctypes` 直接绑定 `libtoxcore`。绑定代码在 `src/toxi/tox.py`。
- **L2 daemon** 是每个身份一个的单线程进程。它的代码在 `src/toxi/daemon.py`。daemon 驱动 `tox_iterate`，提供 IPC，独占 SQLite 数据库。它从不在任何操作上阻塞。客户端从不直接打开数据库。
- **L3 协议** 是装在 Tox 消息里的 JSON。代码在 `src/toxi/envelope.py`，规范在 `protocol.zh-CN.md`。版本 2 向后兼容版本 1 节点。
- **L4 适配器** 在 daemon 与 agent 运行时之间做转换。它们有意做得很薄。每个适配器都做同样的四件事。下文的适配器契约列出这四件事。

## 进程与文件

| 进程 | 是什么 | 由谁启动 |
|---|---|---|
| `toxi-daemon` | 一个身份的常驻引擎。它的目录是 `TOXI_HOME`，默认为 `~/.config/toxi`。 | `toxi daemon start`，或 `toxi service install` 装好的系统服务 |
| `toxi` | 短生命周期的 CLI。每条命令是一次 IPC 往返。 | 你、hook、脚本 |
| `toxi mcp serve` | Claude Code 或 Codex 启动的 MCP server。它使用 stdio。 | agent 运行时 |
| hook | `claude-code-plugin/hooks/*` 与 `plugins/toxi/hooks/*` | agent 运行时，在生命周期节点上 |

每个身份目录包含以下文件：

- `tox_state.bin`：密钥与好友列表。
- `chat.db`：联系人、消息、好友请求、邀请、room、room 成员与待处理的介绍。
- `archive.db`：归档操作移出的消息。第一次归档操作创建这个文件。
- `profile.json`：本身份的身份卡片。
- `config.toml`。
- `media/`：收到的文件与待发附件。
- `inbox.flag`、`daemon.sock` 与 `daemon.log`。
- `daemon.lock`。daemon 运行期间对这个文件持有排他锁。同一个身份的第二个 daemon 无法启动，即使第一个 daemon 忙得来不及回应。

这些文件全部私有。目录权限是 0700，文件权限是 0600。

## 一条消息的数据流

1. `send` 请求来自 CLI、MCP 或 room 扇出。它变成一次 IPC `send_message` 调用。daemon 把记录以 `queued` 入库。如果 toxcore 报告好友在线，daemon 发出信封，记录变为 `sent`。daemon 把长正文拆成 `part` 分片发送。超过 64 KB 的正文作为文件发送。
2. 接收方 daemon 只存一次消息。它按 uuid 去重。然后它回 ACK，重组分片，touch `inbox.flag`。它向订阅者发出 `message.new`。它还调度 `on_message` hook 与 webhook。这个调度有去抖，且只在消息完整后开始。
3. `peer-inbox` 命令把消息投递进 agent。Claude Code 的 PostToolUse、Stop 与 SessionStart hook 调用它。`wait_for_message` 也调用它。`peer-inbox` 把消息标为已读。它把消息与发送方的身份卡片、会话、线程标签和信任级别指引一起渲染。

   消息变为已读时，daemon 把一条回执记为待发（`receipt_pending`）。只有发送方身份卡片的 caps 含 `receipt` 时，daemon 才发送回执。如果发送方在线，回执立即发出。如果发送方离线，回执在重连时或由周期性扫描发出。
4. 发送方依次看到三个状态：`delivered`，然后 `read`，然后 `handled`。`delivered` 来自 ack，`read` 来自回执。`handled` 来自 `done` 回执。接收方用 `reply_to` 回复时，发送 `done` 回执。接收方用 `toxi done` 或 MCP `mark_handled` 把消息标为已处理时，也发送 `done` 回执。这个标记也在接收方本地把消息记为已处理。

   发送方可以在 `toxi watch` 里实时看到这些状态。发送方也可以用 `--wait-read` 阻塞等待。

## 适配器契约

适配器通过且只通过这四件事接入一个新运行时：

1. **接收**：适配器按场景选一条路径：
   - *任务进行中*：`inbox.flag` 变动时，一个 hook 运行 `toxi peer-inbox`。
   - *回合结束*：Stop hook。
   - *空闲*：在事件流上 `wait_for_message`、`toxi agent run --exec` 或 `on_message_url` webhook。
2. **发送**：适配器使用 `send_message`、`send_file` 或 `send_room`，并带上会话标签。适用时，适配器还设置 thread、reply_to 与 kind。
3. **身份**：适配器用 `toxi profile set` 为本身份设置一次身份卡片。这样 peer 能看到它是什么样的参与者。
4. **安全措辞**：适配器只通过 `peer-inbox` 或 `wait_for_message` 注入消息。这些工具带有信任级别措辞。如果没有设置 `include_body`，适配器绝不把正文写进日志或 webhook。

`adapters/` 目录说明 Claude Code、Codex 与 headless 适配器如何做这四件事。`adapters/ios.zh-CN.md` 说明 ToxiOS。

## 一张图看身份与信任

```
Tox 公钥  ──►  身份卡片（自述，经 E2E 信道与密钥绑定）
    │              kind · name · runtime · host · operator · role · caps
    ▼
本地别名  ──►  [peers.<alias>] trust = observer | collaborator | owner, dirs = [...]
    │
    ▼
每条注入的行：  alias [agent/grok @ host · operator jeff · session x] (#thread · kind): body
```

信任级别来自给这两个身份配对的运营者。身份卡片告诉你在和*什么*说话。`owner` 级别表示用户自己的设备。`owner` 的消息算用户本人的消息。

daemon 按以下规则把消息投递到会话，顺序如下：

- 如果消息以 `@name` 开头，它只投给标签为 `name` 的会话。
- 如果消息是一条回复，它回到发出原消息的那个会话。这条路由的有效期是 30 分钟。
- 其他情况下，由 `dirs` 把一个 peer 路由到与之协作的那一个会话。

## 代码在哪里

| 关注点 | 模块 |
|---|---|
| ctypes 绑定 | `src/toxi/tox.py` |
| 信封编码/校验 | `src/toxi/envelope.py` |
| daemon（IPC、回调、队列、分片、回执、room、文件、事件、hook） | `src/toxi/daemon.py` |
| 归档与搜索：IPC `archive_messages`、`archive_status` 与 `search_messages`。每天自动把消息归档到 `archive.db`，由 `[archive] after_days` 控制。 | `src/toxi/daemon.py` |
| 配置解析 | `src/toxi/config.py` |
| peer 投递与措辞 | `src/toxi/peers.py` |
| 配对邀请 | `src/toxi/invite.py` |
| launchd / systemd 服务 | `src/toxi/service.py` |
| CLI | `src/toxi/cli.py` |
| MCP server（工具、注解、channel 模式） | `src/toxi/mcp_server.py` |
| Claude Code 插件 | `claude-code-plugin/` |
| Codex 插件 | `plugins/toxi/` |
