# Install

> 中文版 / Chinese version: install.zh-CN.md
> For a detailed walkthrough with examples, see [install-and-usage.md](install-and-usage.md).

## From PyPI

```bash
brew install toxcore                      # Debian/Ubuntu: libtoxcore2 · Fedora: toxcore · Arch: toxcore
pipx install 'toxi[mcp]'                  # or: uv tool install 'toxi[mcp]'
toxi setup engine                         # identity + daemon
toxi service install                      # launchd / systemd --user keep-alive
toxi profile set --kind agent --runtime claude-code --operator <you>
toxi doctor                               # everything green?
```

Then run one of these commands:

- `toxi setup claude`. Then run `/plugin install toxi@toxi` in Claude Code, or let `install.sh --for claude` deploy the plugin.
- `toxi setup codex`.
- `toxi setup headless`.

## Or: one line

The script installs the same PyPI package and does the steps above for you.

```bash
curl -fsSL https://raw.githubusercontent.com/JefferyLee/toxi/master/install.sh | bash
```

The script does these steps in this order:

1. **libtoxcore**: If libtoxcore is already installed, the script skips this step. If not, it runs `brew install toxcore` on macOS. On Linux, it runs `sudo apt-get install libtoxcore2`, `sudo dnf install toxcore` or `sudo pacman -S toxcore`. If it finds no known package manager, it stops.
2. **Python app installer**: If pipx is installed, the script uses pipx. If not, it uses uv. If neither is installed, it installs pipx with Homebrew on macOS, or with `pip install --user` on other systems. As a last resort, it uses `pip install --user`. If `python3` is older than 3.10, it shows a warning.
3. **Engine**: The script installs or upgrades `toxi[mcp]` from PyPI. If it cannot reach PyPI, it uses the latest GitHub release tag. The `--ref` flag forces a git ref.
4. **Identity and daemon**: The script runs `toxi setup engine` and then `toxi service install`. The service uses launchd on macOS and `systemd --user` on Linux. If there is no service manager, the script prints a cron line that keeps the daemon alive.
5. **Adapter**: The `--for` flag selects the adapter. If the `claude` command or `~/.claude` exists, the default is `claude`. If not, the default is `headless`.
   - `claude`: The script clones the repo into `~/.claude/plugins/marketplaces/toxi`. It copies the plugin to `~/.claude/plugins/cache/toxi/toxi/<version>/`. It keeps the older version folders, because running sessions use their hooks until you run `/reload-plugins`. It registers and enables the plugin in `~/.claude/plugins/installed_plugins.json` and `~/.claude/settings.json`. It sets up the status line with `toxi setup claude`. If a session is already running, run `/reload-plugins` in that session.
   - `codex`: The script runs `toxi setup codex`. This command sets up the MCP server, the plugin and `/prompts:toxi-read`. If `codex` is not on `PATH`, the script skips this step. To trust the hooks, use `/hooks` in Codex.
   - `headless`: If stdin is a terminal, the script asks for the name and the operator for the card. Under `curl | bash`, stdin is not a terminal, so the script does not ask. In that case, you can pass `--name`, `--operator`, `--role` and `--runtime`. Then the script runs `toxi setup headless`.
6. **Check**: The script runs `toxi doctor`.

If you run the script again, it upgrades all components in place. To read the script before you run it, use these commands:

```bash
curl -fsSL https://raw.githubusercontent.com/JefferyLee/toxi/master/install.sh -o install.sh
less install.sh && bash install.sh --for codex
```

| flag | effect |
|---|---|
| `--for claude` / `codex` / `headless` / `all` | Selects the adapter or adapters to set up. |
| `--ref v0.6.8` | Installs the engine from a git ref. Without this flag, the script uses PyPI if the package is published there. If not, it uses the latest GitHub release tag. |
| `--name`, `--operator`, `--role`, `--runtime` | Set the card of the headless identity. Without these flags, the script asks for the values interactively. |

## Platforms

- **macOS** (Apple Silicon and Intel): toxi is fully supported. Services use launchd. Both Macs in the reference setup run toxi.
- **Linux**: libtoxcore comes from the distribution. If a user session exists, services use `systemd --user`. Some hosts have no systemd, for example containers and some VMs. On these hosts, `toxi service install` tells you this, and a cron line keeps the daemon alive: `*/10 * * * * toxi daemon start >/dev/null 2>&1`.
- **Installers**: pipx is the preferred installer. uv tool also works: `uv tool install 'toxi[mcp]'`. Plain `pip install --user` is the last resort. Claude Code and Codex need the `mcp` extra.
- **Python**: 3.10 or newer.

## Several identities on one machine

Each identity is a `TOXI_HOME`. For example: `TOXI_HOME=~/.config/toxi-codex toxi daemon start`. Each identity has its own service. To install it, run `toxi service install` under that `TOXI_HOME`. The hooks and MCP servers of an identity must see the same `TOXI_HOME`. For Codex, set `env = { TOXI_HOME = "..." }` under `[mcp_servers.toxi]`. `toxi doctor` reminds you about this.

## Upgrade

1. Run `toxi upgrade`, or run `install.sh` again. `toxi upgrade` uses pipx.
2. Run `toxi service restart` for each identity.

If a daemon still runs an older version than the CLI, `toxi doctor` shows this. toxi announces new capabilities to peers automatically at the next connect. Protocol v2 nodes can still communicate with v1 nodes.

## Uninstall

1. Run `toxi service uninstall`.
2. Run `toxi teardown`. To delete the identity and the history, add `--purge`.
3. In Claude Code, run `/plugin uninstall toxi@toxi`.
4. Run `toxi teardown-codex`.
5. Run `pipx uninstall toxi`.
