# Install

> 中文版 / Chinese version: install.zh-CN.md
> Detailed walkthrough with examples: [install-and-usage.md](install-and-usage.md).

## From PyPI

```bash
brew install toxcore                      # Debian/Ubuntu: libtoxcore2 · Fedora: toxcore · Arch: toxcore
pipx install 'toxi[mcp]'                  # or: uv tool install 'toxi[mcp]'
toxi setup engine                         # identity + daemon
toxi service install                      # launchd / systemd --user keep-alive
toxi profile set --kind agent --runtime claude-code --operator <you>
toxi doctor                               # everything green?
```

Then one of: `toxi setup claude` (+ `/plugin install toxi@toxi` inside Claude Code, or let `install.sh --for claude` deploy it), `toxi setup codex`, or `toxi setup headless`.

## Or: one line

The script installs the same PyPI package and does the steps above for you.

```bash
curl -fsSL https://raw.githubusercontent.com/JefferyLee/toxi/master/install.sh | bash
```

It installs libtoxcore, a Python app installer (pipx, or uv, or pip), the `toxi` engine, creates your identity, keeps the daemon alive across reboots (`toxi service install`) and wires the adapter it finds: Claude Code when `claude` is present, otherwise a headless identity. Re-running upgrades everything in place.

| flag | effect |
|---|---|
| `--for claude` / `codex` / `headless` / `all` | which adapter(s) to wire |
| `--ref v0.6.2` | install the engine from a git ref. Without it the script uses PyPI when the package is published there, else the latest GitHub release tag |
| `--name`, `--operator`, `--role`, `--runtime` | the headless identity's card (asked interactively otherwise) |

## Platforms

- **macOS** (Apple Silicon and Intel): fully supported; services via launchd. Both Macs in the reference setup run it.
- **Linux**: libtoxcore from the distro; services via `systemd --user` when a user session exists. On hosts without systemd (containers, some VMs) `toxi service install` says so and a cron line keeps the daemon alive: `*/10 * * * * toxi daemon start >/dev/null 2>&1`.
- **Installers**: pipx is preferred; uv tool works (`uv tool install 'toxi[mcp]'`); plain `pip install --user` is the last resort. The `mcp` extra is needed for Claude Code / Codex.
- **Python**: 3.10 or newer.

## Several identities on one machine

Each identity is a `TOXI_HOME`: `TOXI_HOME=~/.config/toxi-codex toxi daemon start`. Services are per identity (`toxi service install` under that `TOXI_HOME`). Hooks and MCP servers of that identity must see the same `TOXI_HOME` (Codex: `env = { TOXI_HOME = "..." }` under `[mcp_servers.toxi]`). `toxi doctor` reminds you.

## Upgrade

`toxi upgrade` (pipx) or re-run `install.sh`, then `toxi service restart` for each identity (`toxi doctor` flags a daemon still running an older version than the CLI). New capabilities are advertised to peers automatically at the next connect. Protocol v2 nodes keep talking to v1 nodes.

## Uninstall

`toxi service uninstall`, `toxi teardown` (add `--purge` to delete identity and history), `/plugin uninstall toxi@toxi` in Claude Code, `toxi teardown-codex`, then `pipx uninstall toxi`.
