# 安装

> English version: install.md
> 带示例的详细手册：[install-and-usage.zh-CN.md](install-and-usage.zh-CN.md)。

## 一行命令

```bash
curl -fsSL https://raw.githubusercontent.com/JefferyLee/toxi/master/install.sh | bash
```

它会安装 libtoxcore、一个 Python 应用安装器（pipx，或 uv，或 pip）、`toxi` 引擎，创建你的身份，让 daemon 跨重启常驻（`toxi service install`），并接入它找到的适配器：有 `claude` 时接 Claude Code，否则建一个 headless 身份。重复运行即原地升级全部组件。

| 参数 | 效果 |
|---|---|
| `--for claude` / `codex` / `headless` / `all` | 接入哪个（些）适配器 |
| `--ref v0.4.0` | 从指定 git ref 安装引擎。不带时：PyPI 上有包就用 PyPI，否则用 GitHub 上最新的 release tag |
| `--name`、`--operator`、`--role`、`--runtime` | headless 身份的卡片（不给则交互式询问） |

## 手动安装

```bash
brew install toxcore                      # Debian/Ubuntu: libtoxcore2 · Fedora: toxcore · Arch: toxcore
pipx install 'toxi[mcp]'                  # 或：uv tool install 'toxi[mcp]'
toxi setup engine                         # 身份 + daemon
toxi service install                      # launchd / systemd --user 常驻
toxi profile set --kind agent --runtime claude-code --operator <you>
toxi doctor                               # 全绿？
```

然后任选其一：`toxi setup claude`（+ 在 Claude Code 里 `/plugin install toxi@toxi`，或让 `install.sh --for claude` 部署）、`toxi setup codex`、或 `toxi setup headless`。

## 平台

- **macOS**（Apple Silicon 与 Intel）：完全支持；服务走 launchd。参考环境里的两台 Mac 都在跑。
- **Linux**：libtoxcore 用发行版的包；有用户会话时服务走 `systemd --user`。没有 systemd 的主机（容器、部分 VM）上 `toxi service install` 会如实告知，改用一条 cron 让 daemon 常驻：`*/10 * * * * toxi daemon start >/dev/null 2>&1`。
- **安装器**：优先 pipx；uv tool 也可以（`uv tool install 'toxi[mcp]'`）；裸 `pip install --user` 是最后手段。Claude Code / Codex 需要 `mcp` extra。
- **Python**：3.10 或更新。

## 一台机器上多个身份

每个身份就是一个 `TOXI_HOME`：`TOXI_HOME=~/.config/toxi-codex toxi daemon start`。服务按身份安装（在那个 `TOXI_HOME` 下运行 `toxi service install`）。该身份的 hook 和 MCP server 必须看到同一个 `TOXI_HOME`（Codex：`[mcp_servers.toxi]` 下的 `env = { TOXI_HOME = "..." }`）。`toxi doctor` 会提醒你。

## 升级

`toxi upgrade`（pipx）或重新运行 `install.sh`，然后对每个身份执行 `toxi service restart`。新能力会在下次连接时自动通告给对端。协议 v2 节点仍能与 v1 节点通信。

## 卸载

`toxi service uninstall`、`toxi teardown`（加 `--purge` 删除身份和历史）、在 Claude Code 里 `/plugin uninstall toxi@toxi`、`toxi teardown-codex`，最后 `pipx uninstall toxi`。
