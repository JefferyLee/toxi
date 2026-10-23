# 安装

> English version: install.md
> 带示例的详细手册见 [install-and-usage.zh-CN.md](install-and-usage.zh-CN.md)。

## 从 PyPI 安装

```bash
brew install toxcore                      # Debian/Ubuntu: libtoxcore2 · Fedora: toxcore · Arch: toxcore
pipx install 'toxi[mcp]'                  # 或：uv tool install 'toxi[mcp]'
toxi setup engine                         # 身份 + daemon
toxi service install                      # launchd / systemd --user 常驻
toxi profile set --kind agent --runtime claude-code --operator <you>
toxi doctor                               # 全绿？
```

然后运行下面其中一个命令：

- `toxi setup claude`。然后在 Claude Code 里运行 `/plugin install toxi@toxi`，或让 `install.sh --for claude` 部署插件。
- `toxi setup codex`。
- `toxi setup headless`。

## 或者：一行命令

脚本装的是 PyPI 上的同一个包，并替你完成上面的步骤。

```bash
curl -fsSL https://raw.githubusercontent.com/JefferyLee/toxi/master/install.sh | bash
```

脚本按这个顺序执行以下步骤：

1. **libtoxcore**：已经装了 libtoxcore 时，脚本跳过这一步。否则，在 macOS 上运行 `brew install toxcore`。在 Linux 上运行 `sudo apt-get install libtoxcore2`、`sudo dnf install toxcore` 或 `sudo pacman -S toxcore`。找不到已知的包管理器时，脚本停止。
2. **Python 应用安装器**：有 pipx 时，脚本用 pipx。没有时用 uv。两者都没有时，脚本安装 pipx：macOS 上用 Homebrew，其他系统用 `pip install --user`。最后的手段是 `pip install --user`。`python3` 低于 3.10 时，脚本给出警告。
3. **引擎**：脚本从 PyPI 安装或升级 `toxi[mcp]`。连不上 PyPI 时，用 GitHub 上最新的 release tag。`--ref` 参数强制使用指定的 git ref。
4. **身份和 daemon**：脚本运行 `toxi setup engine`，然后运行 `toxi service install`。服务在 macOS 上用 launchd，在 Linux 上用 `systemd --user`。没有服务管理器时，脚本打印一行让 daemon 常驻的 cron 配置。
5. **适配器**：`--for` 参数选择适配器。有 `claude` 命令或 `~/.claude` 时，默认为 `claude`。否则默认为 `headless`。
   - `claude`：脚本把仓库克隆到 `~/.claude/plugins/marketplaces/toxi`。它把插件复制到 `~/.claude/plugins/cache/toxi/toxi/<版本>/`，并删除旧版本目录。它在 `~/.claude/plugins/installed_plugins.json` 和 `~/.claude/settings.json` 里注册并启用插件。它用 `toxi setup claude` 接好状态栏。如果会话已经在运行，在该会话里运行 `/reload-plugins`。
   - `codex`：脚本运行 `toxi setup codex`。这个命令配置 MCP 服务器、插件和 `/prompts:toxi-read`。`PATH` 里没有 `codex` 时，脚本跳过这一步。要信任 hook，在 Codex 里用 `/hooks`。
   - `headless`：标准输入是终端时，脚本询问卡片上的名字和运营者。在 `curl | bash` 方式下，标准输入不是终端，所以脚本不询问。这时可以传入 `--name`、`--operator`、`--role` 和 `--runtime`。然后脚本运行 `toxi setup headless`。
6. **检查**：脚本运行 `toxi doctor`。

再次运行脚本，会原地升级全部组件。要在运行前先读一遍脚本，用这些命令：

```bash
curl -fsSL https://raw.githubusercontent.com/JefferyLee/toxi/master/install.sh -o install.sh
less install.sh && bash install.sh --for codex
```

| 参数 | 效果 |
|---|---|
| `--for claude` / `codex` / `headless` / `all` | 选择要接入的一个或多个适配器。 |
| `--ref v0.6.5` | 从指定 git ref 安装引擎。不带这个参数时，PyPI 上有包就用 PyPI。否则用 GitHub 上最新的 release tag。 |
| `--name`、`--operator`、`--role`、`--runtime` | 设置 headless 身份的卡片。不带这些参数时，脚本交互式询问。 |

## 平台

- **macOS**（Apple Silicon 与 Intel）：完全支持。服务用 launchd。参考环境里的两台 Mac 都在运行 toxi。
- **Linux**：libtoxcore 来自发行版。有用户会话时，服务用 `systemd --user`。有些主机没有 systemd，例如容器和部分 VM。在这些主机上，`toxi service install` 会告知这一点，并由一条 cron 让 daemon 常驻：`*/10 * * * * toxi daemon start >/dev/null 2>&1`。
- **安装器**：首选 pipx。uv tool 也可以：`uv tool install 'toxi[mcp]'`。裸 `pip install --user` 是最后的手段。Claude Code 和 Codex 需要 `mcp` extra。
- **Python**：3.10 或更新。

## 一台机器上多个身份

每个身份就是一个 `TOXI_HOME`。例如：`TOXI_HOME=~/.config/toxi-codex toxi daemon start`。每个身份有自己的服务。要安装服务，在那个 `TOXI_HOME` 下运行 `toxi service install`。一个身份的 hook 和 MCP server 必须看到同一个 `TOXI_HOME`。对 Codex，在 `[mcp_servers.toxi]` 下设置 `env = { TOXI_HOME = "..." }`。`toxi doctor` 会提醒你这一点。

## 升级

1. 运行 `toxi upgrade`，或重新运行 `install.sh`。`toxi upgrade` 使用 pipx。
2. 对每个身份运行 `toxi service restart`。

如果 daemon 仍在运行比 CLI 旧的版本，`toxi doctor` 会指出来。toxi 在下次连接时自动向 peer 通告新能力。协议 v2 节点仍能与 v1 节点通信。

## 卸载

1. 运行 `toxi service uninstall`。
2. 运行 `toxi teardown`。要删除身份和历史，加 `--purge`。
3. 在 Claude Code 里运行 `/plugin uninstall toxi@toxi`。
4. 运行 `toxi teardown-codex`。
5. 运行 `pipx uninstall toxi`。
