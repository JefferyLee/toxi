# 云中继

> English version: README.md

Claude Code 云会话不能运行 toxi。云容器只能通过 HTTP 代理上网，而 Tox 连接会绕过这个代理。这个中继给云会话一条通往本地会话的单向通道。它只用 HTTPS，而且只访问你放行的一个主机。

```
云会话   ──POST /send──► Worker（workers.dev，Durable Object 队列）
本地拉取 ──GET /pull、POST /ack──► Worker
拉取服务 ──toxi send <本地身份> "@<会话> ..."──► toxi 路由 ──► 那个会话
```

- `worker/`：Cloudflare Worker。`/send` 要 `SEND_TOKEN`。`/pull` 和 `/ack` 要 `PULL_TOKEN`。本机离线时，队列最多保留 500 条消息。
- `poller.py`：在本机以中继身份运行。它给每条消息加上开头的 `@name`，toxi 据此把消息交给那个会话。
- `../../.claude/skills/toxi-cloud-send/`：告诉云会话怎么发消息的 skill。

## 信任

把中继 peer 设为 `trust = "observer"`。token 只能证明请求带着 token，证明不了消息是哪个会话写的。Cloudflare 能读到队列里的文本。

## 配置

1. 建中继身份：`TOXI_HOME=~/.config/toxi-cloud toxi setup engine`，然后在同一个 `TOXI_HOME` 下运行 `toxi set-name cloud-relay` 和 `toxi service install`。
2. 和本地身份配对：本地身份运行 `toxi pair`，再运行 `TOXI_HOME=~/.config/toxi-cloud toxi join <邀请> --as <本地别名>`。
3. 在本地身份的配置里加 `[peers.cloud-relay]`，`trust = "observer"`。
4. 部署 Worker：`cd worker && npx wrangler deploy`。用 `npx wrangler secret put SEND_TOKEN` 和 `npx wrangler secret put PULL_TOKEN` 放两个随机 token。拉取 token 存在 `~/.config/toxi-cloud/pull-token`（权限 600）。
5. 用 launchd 或 systemd 运行 `poller.py`，设好 `TOXI_RELAY_URL`、`TOXI_HOME` 和 `TOXI_RELAY_TO`（本地身份的别名）。
6. 在 claude.ai/code 的云环境里：
   - 网络权限：Custom，只放行 Worker 的主机。
   - 给 Worker 主机加一个 network secret：请求头 `Authorization`，值为 `Bearer <send token>`。
   - 环境变量：`TOXI_RELAY_URL=https://<Worker 主机>`。
