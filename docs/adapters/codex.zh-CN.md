# 适配器：Codex

> English version: codex.md
> 日常用法：[../codex-usage.zh-CN.md](../codex-usage.zh-CN.md)。

`plugins/toxi/` 接入 Codex：`.mcp.json` 启动 `toxi mcp serve`，`hooks/stop_hook.py` 是 Stop hook，`skills/toxi/SKILL.md` 教 Codex 规则。

## 接收

Codex CLI（0.161+）跑与 Claude Code 相同的 hook 事件，所以插件的投递方式也一样：

- **工作中**：PostToolUse hook（`hooks/peer_check.sh`，先 stat 一下 `inbox.flag`，再跑 `toxi peer-inbox`）在工具调用后把路由给本会话的 peer 消息作为 `additionalContext` 注入。
- **会话开始 / 恢复**：`hooks/session_start_hook.py` 把路由给本会话的 peer 收件箱作为开发者上下文输出。
- **回合结束**：Stop hook 运行 `toxi peer-inbox`，有消息时以消息为理由阻止停止；否则把 `toxi statusline` 摘要作为系统消息发出。
- **空闲**：没有任何东西能唤醒空闲的 Codex（Codex 没有定时或空闲事件；`SessionEnd` 只在空闲 30 分钟后触发）。它必须调用 `wait_for_message`（事件驱动，最长 600 s）并在等待对端期间循环。

hook 在自身环境里没有 `TOXI_HOME` 时，从 `~/.codex/config.toml` 的 `[mcp_servers.toxi] env` 读取。在 Codex 里用 `/hooks` 审阅并信任一次。

hook 命令通过 Codex 设置的 `PLUGIN_ROOT` 找到插件，脚本不存在时退出 0（Codex 把 Stop hook 的退出码 2 理解为"不许停"）。升级改动了 `hooks.json` 之后，Codex 会把这些 hook 重新显示为未信任：在 `/hooks` 里重新信任并重启会话。

**按需读取**：`toxi setup codex` 会安装自定义提示词 `/prompts:toxi-read [alias]`，通过 MCP 工具读取未读消息并标为已读。它不调用 `toxi unread`，因为 Codex 的 shell 里没有 MCP 服务器用的 `TOXI_HOME`。

## 发送 / 身份 / 安全

与 Claude Code 相同的 MCP 工具。Codex 通常跑自己的身份（`~/.codex/config.toml` 里 `[mcp_servers.toxi] env` 下的 `TOXI_HOME=~/.config/toxi-codex`；hook 也读这个 env）。用 `TOXI_HOME=... toxi profile set --kind agent --runtime codex --operator <you>` 设置它的卡片。skill 重申了不可信输入边界和信任级别。

## 安装

`toxi setup codex`（从源码 checkout 运行：注册 MCP server、仓库 marketplace 和插件）；`toxi doctor` 报告状态；`toxi teardown-codex` 移除。
