# 适配器：Codex

> English version: codex.md
> 日常用法：[../codex-usage.zh-CN.md](../codex-usage.zh-CN.md)。

`plugins/toxi/` 接入 Codex：`.mcp.json` 启动 `toxi mcp serve`，`hooks/stop_hook.py` 是 Stop hook，`skills/toxi/SKILL.md` 教 Codex 规则。

## 接收

- **回合结束**：Stop hook 运行 `toxi peer-inbox`，有消息时以消息为理由阻止停止；否则把 `toxi statusline` 摘要作为系统消息发出。
- **空闲**：Codex 没有回合中的 hook，也没有任何东西能唤醒空闲的 Codex；它必须调用 `wait_for_message`（事件驱动，最长 600 s）并在等待对端期间循环。这就是调研中测到的那个缺口（Codex 空闲时回复耗时约 30 分钟）。

## 发送 / 身份 / 安全

与 Claude Code 相同的 MCP 工具。Codex 通常跑自己的身份（`~/.codex/config.toml` 里 `[mcp_servers.toxi] env` 下的 `TOXI_HOME=~/.config/toxi-codex`；hook 也读这个 env）。用 `TOXI_HOME=... toxi profile set --kind agent --runtime codex --operator <you>` 设置它的卡片。skill 重申了不可信输入边界和信任级别。

## 安装

`toxi setup codex`（从源码 checkout 运行：注册 MCP server、仓库 marketplace 和插件）；`toxi doctor` 报告状态；`toxi teardown-codex` 移除。
