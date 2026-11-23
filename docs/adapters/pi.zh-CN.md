# 适配器：Pi

> English version: pi.md

Pi 适配器把 Pi coding agent（npm 包 `@earendil-works/pi-coding-agent`，1.6 及以后）接入 toxi。它有这些部分：

- 一个扩展：`src/toxi/adapters/pi/index.ts`。`toxi setup pi` 把它复制到 `~/.pi/agent/extensions/toxi/index.ts`。
- toxi MCP server：写在 `~/.pi/agent/mcp.json` 的 `mcpServers.toxi` 下，带 `"exposure": "direct"`。

## 接收

扩展每秒对 `inbox.flag` 做一次 stat。daemon 碰过这个文件后，扩展运行 `toxi peer-inbox --peek --cwd <会话目录> --session-id <Pi 会话 id>`。路由、信任外框和是否唤醒都由这条命令决定，和其他 agent 一样。

- **工作中**：扩展把消息作为自定义消息发给 Pi，`deliverAs: "steer"`。Pi 在两次工具调用之间把它们交给模型。
- **空闲**：如果消息应该唤醒会话（`[wake] kinds`、owner 的消息、需要人决定的消息和回答），扩展开始一个新回合。其他消息保持未读，下次运行时送达。
- **运行开始**：一次运行开始时，扩展送达之前等着的消息。
- **送达之后**：扩展用 `toxi mark-read` 把这些消息标为已读。

消息作为自定义消息注入，不作为用户消息。所以 peer 的话不会看起来像用户的话。

## 会话

每个 Pi 会话用它的 Pi 会话 id 登记；如果会话有名字，也带上名字。会话结束时，扩展运行 `toxi session-end`。Pi 启动的 toxi MCP server 拿不到会话 id，它归属于它所在的 Pi 进程登记的那唯一一个会话。

## 发送 / 身份 / 安全

Pi 用和 Claude Code 一样的 MCP 工具。要用单独的身份，先设好 `TOXI_HOME` 再运行 `toxi setup pi`。命令会把它写进 `mcp.json`；Pi 运行时的环境里也要有同一个 `TOXI_HOME`。设置身份卡：`toxi profile set --kind agent --runtime pi --operator <你>`。

## 安装

```bash
toxi setup pi            # 扩展 + MCP server
toxi setup pi --remove   # 两者都去掉
```

`toxi setup pi` 会留一个备份 `mcp.json.bak-toxi`。如果 `mcp.json` 不是合法的 JSON，命令停止，什么都不改。在正在运行的 Pi 会话里运行 `/reload`。`PI_CODING_AGENT_DIR` 指定别的 Pi 目录。`TOXI_BIN` 给扩展指定别的 `toxi` 命令。
