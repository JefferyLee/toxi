# 架构

> English version: architecture.md
> 设计背景与决策：[agent-comm-design.zh-CN.md](agent-comm-design.zh-CN.md)。线格式：[protocol.zh-CN.md](protocol.zh-CN.md)。

toxi 是 agent 与人之间的一层通信层，不需要服务器：每个参与者运行一个持有 Tox 身份的小 daemon，适配器再把这个 daemon 接进运行 agent 的任何环境（Claude Code、Codex、VM 上的 bot、手机）。

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

- **L1 Tox** 提供身份（一对密钥）、加密、发现与 NAT 穿透。toxi 通过 `ctypes` 直接绑定 `libtoxcore`（`src/toxi/tox.py`）。
- **L2 daemon**（`src/toxi/daemon.py`）是每个身份一个的单线程进程：它驱动 `tox_iterate`、提供 IPC、独占 SQLite 数据库，且从不在任何事情上阻塞。客户端从不自己打开数据库。
- **L3 协议**（`src/toxi/envelope.py`，规范见 `protocol.zh-CN.md`）是装在 Tox 消息里的 JSON。版本 2 向后兼容版本 1 节点。
- **L4 适配器** 在 daemon 与 agent 运行时之间做转换。它们有意做得很薄：每个适配器都做同样的四件事（见下）。

## 进程与文件

| 进程 | 是什么 | 由谁启动 |
|---|---|---|
| `toxi-daemon` | 一个身份的常驻引擎（`TOXI_HOME`，默认 `~/.config/toxi`） | `toxi daemon start`，或 `toxi service install` 装好的系统服务 |
| `toxi` | 短生命周期的 CLI；每条命令就是一次 IPC 往返 | 你、hook、脚本 |
| `toxi mcp serve` | Claude Code / Codex 启动的 MCP server（stdio） | agent 运行时 |
| hook | `claude-code-plugin/hooks/*` 与 `plugins/toxi/hooks/*` | agent 运行时，在生命周期节点上 |

每个身份目录下：`tox_state.bin`（密钥 + 好友列表）、`chat.db`（联系人、消息、好友请求、邀请、room、room 成员、待处理的介绍）、`archive.db`（归档移出的消息；第一次归档时创建）、`profile.json`（自己的卡片）、`config.toml`、`media/`（收到的文件、待发附件）、`inbox.flag`、`daemon.sock`、`daemon.log`。全部私有（0700 / 0600）。

## 一条消息的数据流

1. `send`（CLI / MCP / room 扇出）→ IPC `send_message` → 记录以 `queued` 入库 → 若 toxcore 报告好友在线，信封发出，记录变为 `sent`（长正文拆成 `part` 分片，超过 64 KB 的正文作为文件发送）。
2. 接收方 daemon 只存一次（按 uuid 去重），回 ACK，重组分片，touch `inbox.flag`，向订阅者发出 `message.new`，并调度 `on_message` hook / webhook（去抖，且只在消息完整后触发）。
3. 投递进 agent：Claude Code 的 PostToolUse / Stop / SessionStart hook 或 `wait_for_message` 调用 `peer-inbox`，后者把消息标为已读，并连同发送方的卡片、会话、线程标签和信任级别指引一起渲染。标已读会把回执记为待发（`receipt_pending`）；只有发送方卡片的 caps 含 `receipt` 时才发——对方在线就立即发，否则在重连时或由周期性扫描补发。
4. 发送方先看到 `delivered`（ack），再看到 `read`（回执），再看到 `handled`（`done` 回执：接收方用 `reply_to` 回复了它，或用 `toxi done` / MCP `mark_handled` 标为已处理，这也会在接收方本地记为已处理）——可在 `toxi watch` 里实时看到，或用 `--wait-read` 阻塞等待。

## 适配器契约

任何新运行时都通过且只通过这四件事接入：

1. **接收** —— 按场景选一条路径：*任务进行中*（在 `inbox.flag` 变动时运行 `toxi peer-inbox` 的 hook）、*回合结束*（Stop hook）、*空闲*（在事件流上 `wait_for_message`，或 `toxi agent run --exec`，或 `on_message_url` webhook）。
2. **发送** —— `send_message` / `send_file` / `send_room`，带上会话标签，适用时再带 thread / reply_to / kind。
3. **身份** —— 为本身份设置一次卡片（`toxi profile set`），让对端知道自己在和什么样的参与者打交道。
4. **安全措辞** —— 只通过 `peer-inbox` / `wait_for_message` 注入消息，它们会带上信任级别措辞；除非选择了 `include_body`，否则绝不把正文写进日志或 webhook。

Claude Code、Codex 与 headless 适配器如何实现这四件事见 `adapters/`，ToxiOS 见 `adapters/ios.zh-CN.md`。

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

信任来自给这两个身份配对的运营者；卡片告诉你在和*什么*说话；`owner` 表示用户自己的设备，它的消息算用户本人的话。投递到会话：以 `@name` 开头的消息只投给标签为 `name` 的会话；新鲜的回复回到发出被回复消息的那个会话（30 分钟）；否则由 `dirs` 把一个对端路由到与之协作的那一个会话。

## 代码在哪里

| 关注点 | 模块 |
|---|---|
| ctypes 绑定 | `src/toxi/tox.py` |
| 信封编码/校验 | `src/toxi/envelope.py` |
| daemon（IPC、回调、队列、分片、回执、room、文件、事件、hook） | `src/toxi/daemon.py` |
| 归档与搜索（IPC `archive_messages`、`archive_status`、`search_messages`；按 `[archive] after_days` 每天自动归档到 `archive.db`） | `src/toxi/daemon.py` |
| 配置解析 | `src/toxi/config.py` |
| peer 投递与措辞 | `src/toxi/peers.py` |
| 配对邀请 | `src/toxi/invite.py` |
| launchd / systemd 服务 | `src/toxi/service.py` |
| CLI | `src/toxi/cli.py` |
| MCP server（工具、注解、channel 模式） | `src/toxi/mcp_server.py` |
| Claude Code 插件 | `claude-code-plugin/` |
| Codex 插件 | `plugins/toxi/` |
