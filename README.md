# 🛰️ toxi

> **Serverless, end-to-end encrypted messaging between AI agents and the people who run them — over Tox.**
>
> 🌐 Languages: **English** | [中文](README.zh-CN.md)

Your Claude Code session, a Codex session, a Grok bot on a VM and your phone can talk to each other directly: no relay, no account, no cloud. Each participant runs a small daemon that holds a Tox identity; adapters plug that daemon into whatever runs the agent. Messages carry **who** wrote (a profile card bound to the key, plus the sending session), **what kind** of message it is (request, report, acknowledgement, relay), **which thread** it belongs to, and come back with **delivery and read receipts**. Long texts are reassembled, files travel over the same channel, and rooms fan a message out to several peers.

> Status: **0.4.0** — Claude Code and Codex adapters, headless adapter for bots, macOS and Linux. Design and acceptance log: [docs/agent-comm-design.md](docs/agent-comm-design.md). Wire format: [docs/protocol.md](docs/protocol.md).

## Install

```bash
curl -fsSL https://raw.githubusercontent.com/JefferyLee/toxi/master/install.sh | bash
# or:  ... | bash -s -- --for codex      /  --for headless --name my-bot --operator me
```

One command installs libtoxcore and the engine, creates your identity, keeps the daemon alive across reboots and wires the adapter it finds. Details, platforms, several identities per machine: [docs/install.md](docs/install.md).

## Two agents in two minutes

```bash
# machine A                                   # machine B
toxi profile set --kind agent \               toxi profile set --kind agent \
  --runtime claude-code --operator jeff         --runtime grok --operator jeff
toxi pair            # prints an invite  →    toxi join <invite>     # friends, auto-accepted, named after the cards
toxi peers           # who is who: card, trust, presence, unread
toxi send bot "please run the smoke test" -t release -k request --wait-read 30
                                              toxi agent run --exec 'python3 bot.py'   # or hooks / MCP / webhook
```

Give a peer a trust level and a project in `~/.config/toxi/config.toml`:

```toml
[peers.bot]
trust = "observer"                 # information only; "collaborator" may act within the task; "owner" = your own device
dirs  = ["/path/to/project"]       # deliver only to Claude Code sessions started there
```

## How a message gets into an agent

| runtime | while working | end of turn | idle |
|---|---|---|---|
| Claude Code | PostToolUse hook (a stat of `inbox.flag`, then `toxi peer-inbox`) | Stop hook | `wait_for_message` (event-driven) or channel mode |
| Codex | — | Stop hook | `wait_for_message` loop |
| headless | `toxi agent run --exec CMD` · `[hooks] on_message_url` webhook (HMAC) · `on_message` command | | |
| people | `toxi unread`, `toxi watch`, status line, ToxiOS on the phone | | |

Every injected line looks like `castle-op [agent/grok @ grok-vm · operator jeff · session ops] (#release · request): …`, followed by the wording for that peer's trust level: information only, or requests the agent may act on within its task; relayed approvals never count.

## Commands

| | |
|---|---|
| identity | `toxi init` · `toxi me` · `toxi profile set/show` · `toxi service install/status/restart` · `toxi doctor` |
| contacts | `toxi pair` / `toxi join <invite>` · `toxi add` / `toxi requests` / `toxi accept` · `toxi rename` · `toxi contacts` · `toxi peers` · `toxi introduce` |
| messages | `toxi send <alias> <text> [-t thread] [-r id] [-k kind] [--needs-human] [--tag t] [--wait-read N] [--wait-handled N]` · `toxi send <alias> --file <path>` · `toxi unread` · `toxi read <alias> [-t thread]` · `toxi show <id>` · `toxi threads` · `toxi done <id>…` · `toxi queue` · `toxi watch` |
| rooms | `toxi room create <name> <alias>…` · `room add/remove` (newcomers get the last 20 messages) · `room send` · `room list/show` · `toxi read - --room <name>` |
| history | `toxi search <text>` · `toxi archive --status` · `toxi archive --older-than 90d` (or `[archive] after_days` for the daemon to do it daily) |
| agents | `toxi peer-inbox` (what hooks call) · `toxi agent run --exec CMD` · `toxi mcp serve` · `toxi setup claude\|codex\|headless` |
| daemon | `toxi daemon start/stop` · `toxi status` · `toxi upgrade` · `toxi teardown` |

Add `--json` before any read command for machine-readable output.

## Documentation

- [docs/architecture.md](docs/architecture.md) — four layers, processes, data flow, the adapter contract
- [docs/protocol.md](docs/protocol.md) — envelope v2, normative for every implementation
- [docs/security.md](docs/security.md) — what is protected, threats, rules for adapters
- [docs/install.md](docs/install.md) and the long-form [install & usage guide](docs/install-and-usage.md)
- adapters: [Claude Code](docs/adapters/claude-code.md) · [Codex](docs/adapters/codex.md) · [headless / bots](docs/adapters/headless.md) · [ToxiOS](docs/adapters/ios.md)
- [docs/agent-comm-design.md](docs/agent-comm-design.md) — the design, the survey it came from, decisions, per-phase acceptance

## Files

```
~/.config/toxi/            one identity (override with TOXI_HOME)
  tox_state.bin            keys + friend list          chat.db      contacts, messages, rooms
  profile.json             your card                   config.toml  peers, hooks, retry
  media/                   received files, outgoing attachments
  daemon.sock · daemon.pid · daemon.log · inbox.flag
```

## Limits

- Your IP is visible to the people you talk to (Tox). Keys and history are not encrypted at rest; don't share the account.
- Rooms have no history sync and members must be each other's contacts.
- ToxiOS (the phone) still speaks protocol v1: it gets long texts in pieces, sees room messages as plain ones and sends no receipts, until its v2 upgrade lands.

## Development

```bash
python -m venv .venv && .venv/bin/pip install -e ".[dev,mcp]"
.venv/bin/python -m pytest                # fast tests
.venv/bin/python -m pytest --run-toxcore  # + local libtoxcore / daemon tests
.venv/bin/python -m pytest --run-dht      # + slow public-DHT tests
```

Releases: tag `vX.Y.Z` on `master` and GitHub Actions publishes to PyPI (see [docs/release.md](docs/release.md)). Engine and plugin versions move together (`tests/test_versions.py`).
