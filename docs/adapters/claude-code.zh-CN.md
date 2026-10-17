# 适配器：Claude Code

> English version: claude-code.md

`claude-code-plugin/` 里的插件为 Claude Code 实现了[适配器契约](../architecture.zh-CN.md#适配器契约)。

## 接收

| 场景 | 机制 | 延迟 |
|---|---|---|
| Claude 正在工作 | **PostToolUse** hook：`hooks/peer_check.sh` stat 一下 `inbox.flag`。它在 `$TMPDIR` 里为每个项目保留一个标记。只有 flag 变动了，才运行 `post_tool_hook.py` → `toxi peer-inbox --cwd $CLAUDE_PROJECT_DIR` → `additionalContext`。 | 一次工具调用 |
| 一个回合结束 | **Stop** hook：`stop_hook.py` 运行 `peer-inbox`。有消息时，它以消息为理由阻止停止，让 Claude 继续。没有消息时，它打印一行未读摘要。 | 回合结束 |
| 会话开始 / 恢复 / 压缩之后 | **SessionStart** hook：`unread_hook.py` 先投递路由过来的 peer 消息。然后给出好友的未读摘要，并按不可信内容加措辞。 | 立即 |
| Claude 空闲 | 什么都不会自己运行。`wait_for_message`（MCP）阻塞在 daemon 的事件流上，消息到达后几毫秒内返回。或者启用 **channel 模式**：`TOXI_CHANNEL=1 claude --dangerously-load-development-channels plugin:toxi@toxi`。MCP server 随即推送一条唤醒提示，再由 hook 投递消息。提示只说谁写了，从不带正文。 | 毫秒 / 下一回合 |

路由使用以下规则。取第一条命中的规则：

1. 文本以 `@name` 开头时，只到达标签为 `name` 的会话。标签是 `TOXI_SESSION`，否则是项目目录名。
2. 消息回复某个会话发出的内容时，30 分钟内只到达那个会话。回复是指 `reply_to` 指向那条消息，或者处在该会话最近与该联系人对话的线程里。
3. 否则，`[peers.<alias>] dirs = [...]` 让一个 peer 的消息只到达在那些目录启动的会话。这样同一身份上的多个会话不会互相争抢。

`trust = "owner"` 的 peer 是你自己的设备，例如手机上的 ToxiOS。toxi 把它的消息当作你本人的话加措辞，Claude 在那台设备上回答。toxi 静默消费 `kind = ack` 的消息。

## 发送

用于发送的 MCP 工具是 `send_message(alias, body, thread?, reply_to?, kind?, needs_human?, tags?, wait_read?, wait_handled?)`、`send_file` 和 `send_room_message`。插件自动用 `CLAUDE_PROJECT_DIR` 的名字填充会话标签。工具注解把这些工具标为非破坏性，所以 auto 模式不会拦下它们。如果你用严格白名单，在权限里放行 `mcp__plugin_toxi_toxi__send_message`。

## 身份

每个身份运行一次 `toxi profile set --kind agent --runtime claude-code --operator <you> --role "..."`。

## 安全措辞

`peer-inbox` 把每条消息显示为 `alias [card · session] (#thread · kind): body`。消息后面跟着来自 `peers.GUIDES` 的信任级别指引。SessionStart 摘要把好友的消息标为不可信个人内容。toxi 把长正文摘要为 600 字符，加上 `toxi show <id>`。

## 插件里还有

- `/toxi:read`：读取消息并标为已读，显示图片，播放语音消息。
- `/toxi:send-terminal`。
- 底部栏里的 `toxi statusline`。
- MCP 工具 `get_unread`、`read_history`、`get_threads`、`search_messages`、`get_peers`、`list_contacts`、`list_rooms`、`read_room`、`view_media`、`mark_read`、`mark_handled` 和 `get_status`。

## 安装 / 更新

1. 运行 `install.sh --for claude`。它把插件部署到 `~/.claude/plugins/cache/toxi/toxi/<version>/` 并注册。
2. 运行 `/reload-plugins`。hooks.json 的改动需要这一步。

Bash 沙箱会拦截 Unix socket。请使用 MCP 工具，或放行 `~/.config/toxi/daemon.sock`。
