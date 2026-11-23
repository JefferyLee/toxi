# 适配器：OpenCode

> English version: opencode.md

OpenCode 适配器把 OpenCode V2（2.3 及以后）接入 toxi。它有这些部分：

- 一个 server 插件：`src/toxi/adapters/opencode/index.ts`。`toxi setup opencode` 把它复制到 `~/.config/opencode/plugins/toxi/index.ts`。
- toxi MCP server：写在 `~/.config/opencode/opencode.json` 的 `mcp.servers.toxi` 下，带 `"codemode": false`。

## 接收

一个用户的所有 OpenCode 会话都在同一个 OpenCode server 里。插件运行在这个 server 里，每个项目目录一份。

插件每秒对 `inbox.flag` 做一次 stat。daemon 碰过这个文件后，插件对每个在线会话运行 `toxi peer-inbox --peek --cwd <目录> --session-id <OpenCode 会话 id>`。路由、信任外框和是否唤醒都由这条命令决定，和其他 agent 一样。

- **工作中**：插件用 `session.synthetic` 发送消息，`delivery: "steer"`。OpenCode 在下一个步骤边界把它们交给模型。
- **空闲**：如果消息应该唤醒会话（`[wake] kinds`、owner 的消息、需要人决定的消息和回答），这条合成消息会开始一次新的运行。其他消息保持未读，下次运行时送达。
- **运行开始**：一次运行开始时（`session.execution.started`），插件送达之前等着的消息。
- **送达之后**：插件用 `toxi mark-read` 把这些消息标为已读。

## 在线会话

OpenCode 会一直保存会话。所以由插件决定哪些会话对 toxi 算在线：

- 插件所在目录的顶层会话，有活动后就在线。子会话（subagent）永远不算在线。
- 插件启动时，这个目录里最近 12 小时有过活动的会话算在线。
- 插件每 10 分钟刷新一次每个在线会话的 toxi 登记。
- 会话在这些情况下下线：12 小时没有活动；你删除或归档了它；server 停止。

发给已下线会话的消息会退回给发送方。

每个会话用它的 OpenCode 会话 id 和标题登记。同一目录有多个在线会话时，peer 要在消息开头写 `@<会话标题>`。否则 toxi 会退回这条消息，和一个身份下有多个 Claude Code 会话时一样。

## 发送 / 身份 / 安全

OpenCode 在每次 MCP 工具调用的 `_meta` 里带上会话 id。toxi MCP server 用它确定是哪个会话，所以回复从正确的会话发出。

OpenCode server 是后台服务，它没有你 shell 里的 `PATH` 和 `TOXI_HOME`。所以 `toxi setup opencode` 把 `toxi` 的完整路径写进插件和 MCP 配置。运行命令时如果设了 `TOXI_HOME`，它也一起写进去。设置身份卡：`toxi profile set --kind agent --runtime opencode --operator <你>`。

## 安装

```bash
toxi setup opencode            # 插件 + MCP server
opencode service restart       # 加载插件
toxi setup opencode --remove   # 两者都去掉
```

`toxi setup opencode` 会留一个备份 `opencode.json.bak-toxi`。如果配置文件里有注释（JSONC），命令不改它，而是打印出要手动加的 MCP 配置。`OPENCODE_CONFIG_DIR` 指定别的 OpenCode 配置目录。
