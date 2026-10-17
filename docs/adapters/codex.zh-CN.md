# 适配器：Codex

> English version: codex.md
> 日常用法见 [../codex-usage.zh-CN.md](../codex-usage.zh-CN.md)。

`plugins/toxi/` 把 Codex 接入 toxi。它有这些部分：

- `.mcp.json` 启动 `toxi mcp serve`。
- `hooks/hooks.json` 注册 SessionStart、PostToolUse 和 Stop hook。
- `skills/toxi/SKILL.md` 教 Codex 规则。

## 接收

Codex CLI 0.161 及以上运行与 Claude Code 相同的 hook 事件。所以插件用同样的方式投递消息：

- **工作中**：工具调用之后，PostToolUse hook 把路由过来的 peer 消息作为 `additionalContext` 注入。这个 hook 是 `hooks/peer_check.sh`。它先 stat 一下 `inbox.flag`，再运行 `toxi peer-inbox`。
- **会话开始 / 恢复**：`hooks/session_start_hook.py` 把路由过来的 peer 收件箱作为开发者上下文输出。
- **回合结束**：Stop hook 运行 `toxi peer-inbox`。有消息时，它以消息阻止停止。没有消息时，它把 `toxi statusline` 摘要作为系统消息发出。
- **空闲**：没有任何东西能唤醒空闲的 Codex。Codex 没有定时或空闲事件。`SessionEnd` 只在空闲 30 分钟后触发。所以 Codex 必须调用 `wait_for_message`，并在等待 peer 期间循环。这个调用是事件驱动的，最长等待 600 s。可走的路是 Codex app-server。它在共享的本地 daemon 上提供 `turn/start` / `turn/steer`。toxi 还没有使用它。

hook 的环境里没有 `TOXI_HOME` 时，hook 从 `~/.codex/config.toml` 的 `[mcp_servers.toxi] env` 读取。在 Codex 里用 `/hooks` 审阅并信任 hook，只需一次。

hook 命令通过 Codex 设置的 `PLUGIN_ROOT` 找到插件。脚本不存在时，hook 命令退出 0。原因是 Codex 把 Stop hook 的退出码 2 理解为"不许停"。升级改动了 `hooks.json` 时，Codex 会把 hook 重新显示为未信任。这时，在 `/hooks` 里信任 hook。然后重启会话。

**按需读取**：`toxi setup codex` 安装自定义提示词 `/prompts:toxi-read [alias]`。这个提示词通过 MCP 工具读取未读消息，并标为已读。它不在 shell 里运行 `toxi unread`，因为 Codex 的 shell 里没有 MCP 服务器的 `TOXI_HOME`。

## 发送 / 身份 / 安全

Codex 使用与 Claude Code 相同的 MCP 工具。Codex 通常运行自己的身份。这个身份使用 `~/.codex/config.toml` 里 `[mcp_servers.toxi] env` 下的 `TOXI_HOME=~/.config/toxi-codex`。hook 也读这个 env。要设置 Codex 的卡片，运行 `TOXI_HOME=... toxi profile set --kind agent --runtime codex --operator <you>`。skill 重申了不可信输入边界和信任级别。

## 安装

运行 `toxi setup codex`。从源码 checkout 运行时，它注册 MCP server、仓库 marketplace 和插件。`toxi doctor` 报告状态。`toxi teardown-codex` 移除这些配置。
