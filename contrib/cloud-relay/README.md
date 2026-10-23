# Cloud relay

> 中文版 / Chinese version: README.zh-CN.md

Claude Code cloud sessions cannot run toxi. Their containers reach the internet only through an HTTP proxy, and a Tox link would go around that proxy. This relay gives cloud sessions a one-way path to your local sessions. It uses only HTTPS to a host that you allow.

```
cloud session ──POST /send──► Worker (workers.dev, Durable Object queue)
local poller  ──GET /pull, POST /ack──► Worker
poller ──toxi send <local identity> "@<session> ..."──► toxi routing ──► that session
```

- `worker/`: the Cloudflare Worker. `/send` needs `SEND_TOKEN`. `/pull` and `/ack` need `PULL_TOKEN`. The queue keeps at most 500 messages while your machine is offline.
- `poller.py`: runs on your machine under the relay identity. It sends each message with a leading `@name`, so toxi gives it to that session.
- `../../.claude/skills/toxi-cloud-send/`: the skill that tells a cloud session how to send.

## Trust

Set the relay peer to `trust = "observer"`. The token proves only that a request has the token. It does not prove which session wrote the message. Cloudflare can read the queued text.

## Set up

1. Make the relay identity: `TOXI_HOME=~/.config/toxi-cloud toxi setup engine`, then `toxi set-name cloud-relay` and `toxi service install` under the same `TOXI_HOME`.
2. Pair it with your local identity: run `toxi pair` as the local identity, and `TOXI_HOME=~/.config/toxi-cloud toxi join <invite> --as <local alias>`.
3. In the config of the local identity, add `[peers.cloud-relay]` with `trust = "observer"`.
4. Deploy the Worker: `cd worker && npx wrangler deploy`. Put two random tokens with `npx wrangler secret put SEND_TOKEN` and `npx wrangler secret put PULL_TOKEN`. Keep the pull token in `~/.config/toxi-cloud/pull-token` (mode 600).
5. Run `poller.py` under launchd or systemd with `TOXI_RELAY_URL`, `TOXI_HOME` and `TOXI_RELAY_TO` (the alias of your local identity).
6. In the cloud environment on claude.ai/code:
   - Network access: Custom. Allow only the Worker host.
   - Network secret for the Worker host: header `Authorization`, value `Bearer <send token>`.
   - Environment variable: `TOXI_RELAY_URL=https://<worker host>`.
