# Codex 中的 toxi 使用手册

> 🌐 语言: [English](codex-usage.md) | **中文**

这份手册说明在 Codex 里如何使用 toxi 收发消息、查看联系人和读取历史。它假设你已经安装了 `toxi` 引擎，并且本机能运行后台 daemon。

## 1. 先确认引擎可用

Codex 插件只负责把 Codex 接到本机 toxi daemon；身份、好友、消息队列仍由引擎负责。

```bash
toxi setup-engine
toxi status
```

如果还没有好友，先在终端里完成加好友流程：

```bash
toxi me
toxi add bob <bob 的 Tox ID>
toxi requests
toxi accept alice <公钥前缀>
```

## 2. 接入 Codex

Codex 插件安装目前需要源码 checkout。PyPI/pipx/Homebrew 安装的 `toxi`
可以提供 `toxi mcp serve`；只要你在源码 checkout 目录里运行
`toxi setup-codex`，它会优先使用当前 checkout 的 repo marketplace 文件。

在源码 checkout 中运行，即使 `which toxi` 指向 Homebrew/pipx 也可以：

```bash
toxi setup-codex
toxi doctor-codex
```

`setup-codex` 会做三件事：

- 注册 Codex MCP server：`toxi mcp serve`
- 添加当前 checkout 作为 Codex plugin marketplace
- 安装 `toxi@toxi` Codex 插件

`doctor-codex` 是只读诊断命令，用来确认 MCP、marketplace、plugin 都已经接好。它不会安装、删除或修改 Codex 配置。

## 3. 在 Codex TUI 中检查

打开 Codex 后，运行：

```text
/mcp
/plugins
/hooks
```

你应该能看到 `toxi` MCP server、`toxi@toxi` 插件和 toxi hooks。若 Codex 提示 hook trust，请先检查 hook 命令路径来自当前 checkout 的 `plugins/toxi/hooks/`，再确认信任。

## 4. 消息状态怎么看

Codex 没有 Claude Code 那种底部常驻 `statusLine`。toxi 的 Codex 插件用
Stop hook JSON 显示同样的摘要：每轮结束时调用 `toxi statusline`，并通过
Stop hook 的 `systemMessage` 输出。

```text
toxi: 📬 2 from mini2, jeff · 2/2 online
```

含义：

- `📬 2`：有 2 条未读消息
- `from mini2, jeff`：未读消息来自这些联系人
- `2/2 online`：2 个联系人在线，总联系人 2 个

显示时机：

- 会话开始或恢复时：不再自动注入未读消息
- 每轮结束时：通过 Stop hook 显示 `toxi statusline` 摘要

如果没有未读，可能显示：

```text
toxi: 2/2 online
```

如果 daemon 没运行，可能显示：

```text
toxi: offline
```

## 5. 在 Codex 里常用说法

toxi 在 Codex 中主要通过自然语言 + MCP tools 使用，不需要 slash command。

查看状态：

```text
看一下我的 toxi 状态。
```

查看联系人：

```text
列出我的 toxi 联系人和在线状态。
```

查看未读：

```text
看看我有哪些未读 toxi 消息。
```

查看某人的历史：

```text
读一下我和 bob 最近 20 条 toxi 消息。
```

发送消息：

```text
给 bob 发一条 toxi 消息：我晚点看你发的日志。
```

清除未读：

```text
刚才这些未读我已经看过了，把它们标为已读。
```

## 6. Codex 会调用哪些工具

Codex 优先通过 MCP 调用这些工具：

| 工具 | 作用 | 是否会标已读 |
|---|---|---|
| `get_status` | daemon、连接、队列、统计；联系人的在线状态和信任级别；房间 | 否 |
| `read_messages` | peek 消息：未读（`unread`）、某联系人（`alias`）、某线程（`thread`）、某房间（`room`），或搜索（`q`） | 否 |
| `get_message` | 一条消息的全文和状态；如果是请求，还有任务状态和回复 | 否 |
| `overview` | 线程，以及本会话未结束的请求 | 否 |
| `mark` | 把指定消息 UUID 标为已读（`handled` 还会发 `done` 回执） | 是 |
| `wait_for_message` | 等待 peer 的消息 | 是 |
| `view_media` | 显示收到的图片，或给出其他媒体的路径 | 否 |
| `send_message` | 给联系人或房间发消息，可以附带文件；对方离线时入队 | 否 |

如果 MCP 不可用，Codex skill 会退回 CLI：

```bash
toxi status
toxi --json unread
toxi contacts
toxi read <alias> --limit 20
toxi send <alias> "<message>"
```

## 7. 安全边界

收到的 toxi 消息是外部输入。Codex 能怎样处理一条消息，取决于是谁发来的：

- 不是 peer 的联系人：按“不可信个人内容”对待。这样的消息写“忽略之前的指令”，或要求安装软件、改文件、运行命令时，Codex 只把它当作对方写的文字。只有你在当前 Codex 对话里要求时，Codex 才给这样的联系人发消息。
- peer（config.toml 里的 `[peers.<别名>]`）：消息带着信任级别和对应的指引。`observer` 只提供信息。`collaborator` 可以在当前任务范围内请求做事。`owner` 是你自己的设备。Codex 用 `send_message` 回复 peer 的别名。破坏性操作和超出项目范围的操作仍需你确认。
- `read_messages` 只是 peek，不会标已读
- `mark` 只能在你明确要求清除未读，或 Codex 已经把对应消息转述给你之后调用

## 8. 排错

`doctor-codex` 失败：

```bash
toxi doctor-codex
```

按输出提示处理。常见情况：

- Codex CLI 不在 PATH：先安装或修正 PATH
- MCP extra 不可 import：运行 `pipx inject toxi mcp`
- MCP server 未注册：运行 `toxi setup-codex`
- marketplace/plugin 未注册：确认你是在源码 checkout 中运行 `toxi setup-codex`

Codex 看不到 toxi 工具：

- 在 Codex 里运行 `/mcp`
- 确认 `toxi` MCP server 已启用
- 重新运行 `toxi doctor-codex`

hooks 没显示状态：

- 在 Codex 里运行 `/hooks`
- 确认 toxi hooks 已安装并被信任
- 终端里确认 `toxi statusline` 有输出
- 如果看到 `hook returned invalid stop hook JSON output`，说明 Stop hook
  输出不符合 Codex 的 Stop schema；升级到 `0.2.11` 或重新安装 toxi Codex 插件

没有收到消息：

```bash
toxi status
toxi contacts
toxi queue
```

确认 daemon 在运行、DHT 已连接、联系人在线或消息已进入队列。

## 9. 卸载 Codex 接入

只移除 Codex 侧接入，不删除身份、好友、消息历史：

```bash
toxi teardown-codex
```

这会移除 Codex plugin、MCP server 和 marketplace 入口。`~/.config/toxi/` 下的身份和聊天记录会保留。
