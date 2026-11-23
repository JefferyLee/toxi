# 适配器：Claude Code

> English version: claude-code.md

`claude-code-plugin/` 里的插件为 Claude Code 实现了[适配器契约](../architecture.zh-CN.md#适配器契约)。

## 接收

| 场景 | 机制 | 延迟 |
|---|---|---|
| Claude 正在工作 | **PostToolUse** hook：`hooks/peer_check.sh` stat 一下 `inbox.flag`。它在 `$TMPDIR` 里为每个会话保留一个标记（hook 输入里没有会话 id 时，为每个项目保留一个）。只有 flag 变动了，才运行 `post_tool_hook.py` → `toxi peer-inbox --peek --cwd $CLAUDE_PROJECT_DIR` → `additionalContext` → `toxi mark-read`。对子 agent 的工具调用，它什么也不做。 | 一次工具调用 |
| 一个回合结束 | **Stop** hook：`stop_hook.py` 运行 `peer-inbox --peek`。有消息时，它以消息为理由阻止停止，让 Claude 继续。没有消息时，它打印一行未读摘要。 | 回合结束 |
| 会话开始 / 恢复 / 压缩之后 | **SessionStart** hook：`unread_hook.py` 先投递路由过来的 peer 消息。然后给出好友的未读摘要，并按不可信内容加措辞。 | 立即 |
| Claude 空闲 | **唤醒 hook**：SessionStart 和 Stop 在后台启动 `wake_hook.py`（`async` + `asyncRewake`）。它盯着 `inbox.flag`。有本会话的消息到达时，它输出消息、标为已读并以退出码 2 结束，Claude Code 随即带着这些消息开始一个回合。UserPromptSubmit 会结束它，因为忙着的会话由上面几个 hook 收消息。每个空闲会话只跑一个等待进程。24 小时没有消息它就结束，下一回合会再起一个。在你两次输入之间，一个会话最多被唤醒 20 次，所以两个 agent 不会在你不在时互相唤醒个没完；超过之后，消息留到你下一次输入时再送达。`[wake] kinds` 决定哪些消息会唤醒它（默认：`msg`、`request`、`relay`；`owner` 的消息、需要用户决定的消息、对本会话的回复总会唤醒）。`wait_for_message`（MCP）和 channel 模式（`TOXI_CHANNEL=1 claude --dangerously-load-development-channels plugin:toxi@toxi`）仍然可用。 | 1–2 秒 |

每个 hook 先输出消息，然后才把它们标为已读。如果 hook 在这两步之间停止，这些消息会在下一个 hook 再次送达。

路由使用以下规则。取第一条命中的规则：

1. 文本以 `@name` 开头时，只到达名为 `name` 的会话。名字是 `TOXI_SESSION`，否则是你用 `/rename` 给会话起的名字，否则是项目目录名。同名的第二个在运行的会话得到 `-2`，直到第一个停止。
2. 消息回复某个会话发出的内容时，30 分钟内只到达那个会话。回复是指 `reply_to` 指向那条消息，或者处在该会话最近与该联系人对话的线程里。
3. 否则，`[peers.<alias>] dirs = [...]` 让一个 peer 的消息只到达在那些目录启动的那个会话。没有 `dirs` 的 peer 匹配每个会话。
4. 如果没有会话匹配，或有多于一个会话匹配，toxi 把文本退回给 peer（`bounce`）。非 peer 联系人的文本进入公共收件箱。没有会话会自动拿到它。

`wait_for_message` 使用同样的规则，给了别名时也一样。

每个会话只看到自己的消息：hook、`/toxi:read` 和状态栏里都是。`/toxi:read --all` 同时显示公共收件箱。

`trust = "owner"` 的 peer 是你自己的设备，例如手机上的 ToxiOS。toxi 把它的消息当作你本人的话加措辞，Claude 在那台设备上回答。toxi 静默消费 `kind = ack` 的消息。

## 发送

用于发送的 MCP 工具是 `send_message(alias, body, thread?, reply_to?, kind?, needs_human?, tags?, wait_read?, wait_handled?)`、`send_file` 和 `send_room_message`。插件自动用会话名填充会话标签。工具注解把这些工具标为非破坏性，所以 auto 模式不会拦下它们。如果你用严格白名单，在权限里放行 `mcp__plugin_toxi_toxi__send_message`。

## 身份

每个身份运行一次 `toxi profile set --kind agent --runtime claude-code --operator <you> --role "..."`。

## 安全措辞

`peer-inbox` 把每条消息显示为 `session@alias [card] (#thread · kind · age): body`。每个信任级别的消息放在两行带随机标记的分隔行之间，正文的每一行都缩进。因此发送方写不出看起来像 toxi 标题行或别的信任级别的行。消息后面跟着来自 `peers.GUIDES` 的信任级别指引。SessionStart 摘要把好友的消息标为不可信个人内容。toxi 把长正文摘要为 600 字符，加上 `toxi show <id>`。`/toxi:read` 不经确认只能运行 `toxi unread` 和 `toxi show`。

## 插件里还有

- `/toxi:read`：读取本会话的消息并标为已读，显示图片，播放语音消息。`/toxi:read --all` 同时读取公共收件箱。
- `/toxi:send-terminal`。
- 底部栏里的 `toxi statusline`。
- MCP 工具 `get_unread`、`read_history`、`get_threads`、`get_message_status`、`search_messages`、`get_peers`、`list_contacts`、`list_rooms`、`read_room`、`view_media`、`mark_read`、`mark_handled` 和 `get_status`。

## 安装 / 更新

1. 运行 `install.sh --for claude`。它把插件部署到 `~/.claude/plugins/cache/toxi/toxi/<version>/` 并注册。
2. 运行 `/reload-plugins`。hooks.json 的改动需要这一步。

Bash 沙箱会拦截 Unix socket。请使用 MCP 工具，或放行 `~/.config/toxi/daemon.sock`。
