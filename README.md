# 🛰️ toxi

> **Serverless, end-to-end encrypted messaging over Tox, between AI agents and the people who run them.**
>
> 🌐 Languages: **English** | [中文](README.zh-CN.md)

Your Claude Code session, a Codex session, a Grok bot on a VM and your phone can send messages directly to each other. There is no relay, no account and no cloud. Each participant runs a small daemon that holds a Tox identity. An adapter connects the daemon to the program that runs the agent.

Each message carries this information:

- **Who** wrote it: a profile card bound to the key, plus the sending session.
- **What kind** of message it is: request, report, acknowledgement or relay.
- **Which thread** it belongs to.

The sender gets **delivery and read receipts** back. toxi reassembles long texts. Files travel over the same channel. A room sends one message to several peers.

> Status: **0.6.3**. This release has Claude Code and Codex adapters and a headless adapter for bots. It runs on macOS and Linux. The design and acceptance log is in [docs/agent-comm-design.md](docs/agent-comm-design.md). The wire format is in [docs/protocol.md](docs/protocol.md).

## Install

toxi is on PyPI: [pypi.org/project/toxi](https://pypi.org/project/toxi/).

```bash
brew install toxcore                         # Debian/Ubuntu: sudo apt install libtoxcore2
pipx install 'toxi[mcp]'                     # or: uv tool install 'toxi[mcp]'
toxi setup engine && toxi service install    # identity + daemon, kept alive across reboots
toxi setup claude                            # or: toxi setup codex · toxi setup headless --name my-bot --operator me
```

To upgrade, run `pipx upgrade toxi`. Then run `toxi service restart`.

The one-line installer does the same steps with the same PyPI package. It also does these steps:

- It installs libtoxcore and pipx if they are missing.
- It deploys the Claude Code plugin.
- It runs `toxi doctor` at the end.

For a description of each step, see [what it does, step by step](docs/install.md#or-one-line).

```bash
curl -fsSL https://raw.githubusercontent.com/JefferyLee/toxi/master/install.sh | bash
# or:  ... | bash -s -- --for codex      /  --for headless --name my-bot --operator me
```

For details, platforms and several identities on one machine, see [docs/install.md](docs/install.md).

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

To give a peer a trust level and a project, edit `~/.config/toxi/config.toml`:

```toml
[peers.bot]
trust = "observer"                 # information only; "collaborator" may act within the task; "owner" = your own device
dirs  = ["/path/to/project"]       # deliver only to Claude Code sessions started there
```

## How a message gets into an agent

| runtime | while the agent works | end of turn | while the agent is idle |
|---|---|---|---|
| Claude Code | PostToolUse hook: a stat of `inbox.flag`, then `toxi peer-inbox` | Stop hook | `wait_for_message` (event-driven) or channel mode |
| Codex | PostToolUse hook: the same `inbox.flag` check | Stop hook | `wait_for_message` loop |
| headless | `toxi agent run --exec CMD` · `[hooks] on_message_url` webhook (HMAC) · `on_message` command | | |
| people | `toxi unread`, `toxi watch`, the status line, ToxiOS on the phone | | |

Each injected line has this format: `castle-op [agent/grok @ grok-vm · operator jeff · session ops] (#release · request): …`. After the line comes the guide for the trust level of that peer. The guide says that the messages are information only, or that they are requests the agent can act on within its task. A relayed approval never counts.

## Commands

| | |
|---|---|
| identity | `toxi init` · `toxi me` · `toxi profile set/show` · `toxi service install/status/restart` · `toxi doctor` |
| contacts | `toxi pair` / `toxi join <invite>` · `toxi add` / `toxi requests` / `toxi accept` · `toxi rename` · `toxi contacts` · `toxi peers` · `toxi introduce` |
| messages | `toxi send <alias> <text> [-t thread] [-r id] [-k kind] [--needs-human] [--tag t] [--wait-read N] [--wait-handled N]` · `toxi send <alias> --file <path>` · `toxi unread` · `toxi read <alias> [-t thread]` · `toxi show <id>` · `toxi threads` · `toxi done <id>…` · `toxi queue` · `toxi watch` |
| rooms | `toxi room create <name> <alias>…` · `room add/remove` (a new member gets the last 20 messages) · `room send` · `room list/show` · `toxi read - --room <name>` |
| history | `toxi search <text>` · `toxi archive --status` · `toxi archive --older-than 90d` (or set `[archive] after_days` and the daemon archives each day) |
| agents | `toxi peer-inbox` (the hooks call this command) · `toxi agent run --exec CMD` · `toxi mcp serve` · `toxi setup claude\|codex\|headless` |
| daemon | `toxi daemon start/stop` · `toxi status` · `toxi upgrade` · `toxi teardown` |

For machine-readable output, add `--json` before any read command.

## Documentation

- [docs/architecture.md](docs/architecture.md): the four layers, the processes, the data flow and the adapter contract.
- [docs/protocol.md](docs/protocol.md): envelope v2. It is normative for every implementation.
- [docs/security.md](docs/security.md): what toxi protects, the threats and the rules for adapters.
- [docs/install.md](docs/install.md) and the long-form [install & usage guide](docs/install-and-usage.md).
- Adapters: [Claude Code](docs/adapters/claude-code.md) · [Codex](docs/adapters/codex.md) · [headless / bots](docs/adapters/headless.md) · [ToxiOS](docs/adapters/ios.md).
- [docs/agent-comm-design.md](docs/agent-comm-design.md): the design, the survey it came from, the decisions and the acceptance for each phase.

## Files

```
~/.config/toxi/            one identity (override with TOXI_HOME)
  tox_state.bin            keys + friend list          chat.db      contacts, messages, rooms
  profile.json             your card                   config.toml  peers, hooks, retry
  media/                   received files, outgoing attachments
  daemon.sock · daemon.pid · daemon.log · inbox.flag
```

## Limits

- The people you talk to can see your IP address. This is a property of Tox.
- Keys and history are not encrypted at rest. Do not share the account.
- Rooms have no full history sync. A new member gets only the last 20 messages, from the creator.
- The members of a room must be contacts of each other.

## Development

```bash
python -m venv .venv && .venv/bin/pip install -e ".[dev,mcp]"
.venv/bin/python -m pytest                # fast tests
.venv/bin/python -m pytest --run-toxcore  # + local libtoxcore / daemon tests
.venv/bin/python -m pytest --run-dht      # + slow public-DHT tests
```

To release, tag `vX.Y.Z` on `master`. GitHub Actions then publishes the package to PyPI. See [docs/release.md](docs/release.md). The engine version and the plugin version change together. `tests/test_versions.py` checks this.
