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

What it does, in order:

1. **libtoxcore**: skipped if already present; otherwise `brew install toxcore` on macOS, or `sudo apt-get install libtoxcore2` / `sudo dnf install toxcore` / `sudo pacman -S toxcore` on Linux. It stops if it finds no known package manager.
2. **Python app installer**: uses pipx if present, else uv; otherwise installs pipx (Homebrew on macOS, `pip install --user` elsewhere), and falls back to `pip install --user` as a last resort. It warns if `python3` is older than 3.10.
3. **Engine**: installs or upgrades `toxi[mcp]` from PyPI. If PyPI cannot be reached it uses the latest GitHub release tag; `--ref` forces a git ref.
4. **Identity and daemon**: `toxi setup engine`, then `toxi service install` (launchd on macOS, `systemd --user` on Linux). Without a service manager it prints a cron line that keeps the daemon alive.
5. **Adapter** (`--for`; default `claude` when the `claude` command or `~/.claude` exists, otherwise `headless`):
   - `claude`: clones the repo into `~/.claude/plugins/marketplaces/toxi`, copies the plugin to `~/.claude/plugins/cache/toxi/toxi/<version>/` and removes older version folders, registers and enables it in `~/.claude/plugins/installed_plugins.json` and `~/.claude/settings.json`, and wires the status line (`toxi setup claude`). Sessions already running need `/reload-plugins`.
   - `codex`: `toxi setup codex` (MCP server, plugin, `/prompts:toxi-read`); skipped if `codex` is not on `PATH`. Trust the hooks in Codex with `/hooks`.
   - `headless`: asks for the card's name and operator when stdin is a terminal, so not under `curl | bash` (or pass `--name`, `--operator`, `--role`, `--runtime`), then `toxi setup headless`.
6. **Check**: `toxi doctor`.

Re-running it upgrades everything in place. To read it before running it:

```bash
curl -fsSL https://raw.githubusercontent.com/JefferyLee/toxi/master/install.sh -o install.sh
less install.sh && bash install.sh --for codex
```

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
