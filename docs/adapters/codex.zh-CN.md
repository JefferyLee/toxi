# 适配器：Codex

> English version: codex.md
> 日常用法见 [../codex-usage.zh-CN.md](../codex-usage.zh-CN.md)。

`plugins/toxi/` 把 Codex 接入 toxi。它有这些部分：

- `.mcp.json` 启动 `toxi mcp serve`。
- `hooks/hooks.json` 注册 SessionStart、PostToolUse 和 Stop hook。
- `skills/toxi/SKILL.md` 教 Codex 规则。

## 接收

Codex CLI 0.161 及以上运行与 Claude Code 相同的 hook 事件。所以插件用同样的方式投递消息：

- **工作中**：工具调用之后，PostToolUse hook 把路由过来的 peer 消息作为 `additionalContext` 注入。这个 hook 是 `hooks/peer_check.sh`。它先 stat 一下 `inbox.flag`，再运行 `toxi peer-inbox --peek`。它输出消息之后，才把消息标为已读。
- **会话开始 / 恢复**：`hooks/session_start_hook.py` 把路由过来的 peer 收件箱作为开发者上下文输出。
- **回合结束**：Stop hook 运行 `toxi peer-inbox --peek --cwd <会话目录>`，所以只取本会话的消息。有消息时，它以消息阻止停止。没有消息时，它把 `toxi statusline` 摘要作为系统消息发出。
- **空闲**：Codex 没有空闲事件。但 Codex CLI（0.167 及以后）把会话放在一个共享的 app-server daemon 里运行。daemon 的控制 socket `$CODEX_HOME/app-server-control/app-server-control.sock` 走的是 WebSocket 上的 JSON-RPC。SessionStart 和 Stop hook 会启动 `toxi codex-wake`，每个 toxi 身份一个。当发给某个会话的消息应该唤醒它（`[wake] kinds`、owner 的消息、需要人决定的消息和回答），而它的线程又空闲时，唤醒器把消息作为 developer 消息加进线程（`thread/inject_items`），再开始一个没有用户输入的回合（`turn/start`）。所以 peer 的话不会看起来像用户的话。其他消息保持未读，下次运行时送达。唤醒器对同一个线程每小时最多唤醒 20 次，避免两个 agent 无休止地互相唤醒。
- **daemon 里的会话**：daemon 运行所有线程，hook 和 MCP server 也都由它启动。所以 toxi 按线程 id 登记每个会话：hook 传 `session_id`，MCP server 从每次工具调用的 `_meta` 里读 `threadId`。线程在 daemon 里加载期间，唤醒器为登记续期；daemon 卸载线程后，唤醒器注销登记。带 `--no-daemon` 的 Codex 和 ChatGPT 桌面版把线程放在私有 server 里，toxi 连不到。对它们，没有东西能唤醒空闲的会话，会话仍按进程登记，和以前一样。在那里，Codex 调用 `wait_for_message`，并在等待 peer 期间循环。
- **审批**：被唤醒的回合遵循会话的审批策略。如果 `send_message` 需要审批，回合会在 TUI 里等你批准。

hook 的环境里没有 `TOXI_HOME` 时，hook 从 `~/.codex/config.toml` 的 `[mcp_servers.toxi] env` 读取。在 Codex 里用 `/hooks` 审阅并信任 hook，只需一次。

hook 命令通过 Codex 设置的 `PLUGIN_ROOT` 找到插件。脚本不存在时，hook 命令退出 0。原因是 Codex 把 Stop hook 的退出码 2 理解为"不许停"。升级改动了 `hooks.json` 时，Codex 会把 hook 重新显示为未信任。这时，在 `/hooks` 里信任 hook。然后重启会话。

**按需读取**：`toxi setup codex` 安装自定义提示词 `/prompts:toxi-read [alias]`。这个提示词通过 MCP 工具读取未读消息，并标为已读。它不在 shell 里运行 `toxi unread`，因为 Codex 的 shell 里没有 MCP 服务器的 `TOXI_HOME`。

## 发送 / 身份 / 安全

Codex 使用与 Claude Code 相同的 MCP 工具。Codex 通常运行自己的身份。这个身份使用 `~/.codex/config.toml` 里 `[mcp_servers.toxi] env` 下的 `TOXI_HOME=~/.config/toxi-codex`。hook 也读这个 env。要设置 Codex 的卡片，运行 `TOXI_HOME=... toxi profile set --kind agent --runtime codex --operator <you>`。skill 重申了不可信输入边界和信任级别。

## 安装

运行 `toxi setup codex`。从源码 checkout 运行时，它注册 MCP server、仓库 marketplace 和插件。`toxi doctor` 报告状态。`toxi teardown-codex` 移除这些配置。
