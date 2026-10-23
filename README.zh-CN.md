# 🛰️ toxi

> **基于 Tox 的无服务器、端到端加密消息通信，连接 AI agent 与运行它们的人。**
>
> 🌐 Languages: [English](README.md) | **中文**

你的 Claude Code 会话、一个 Codex 会话、VM 上的一个 Grok bot 和你的手机可以直接互发消息。没有中继，没有账号，没有云端。每个参与者运行一个小 daemon，daemon 持有一个 Tox 身份。适配器把 daemon 接入运行 agent 的程序。

每条消息携带这些信息：

- **谁**写的：与密钥绑定的身份卡片，加上发送会话。
- 是**哪种**消息：request、report、acknowledgement 或 relay。
- 属于**哪个线程**。

发送方会收到**送达回执和已读回执**。toxi 会重组长文本。文件走同一条信道。room 把一条消息发给多个 peer。

> 状态：**0.6.3**。本版本有 Claude Code 与 Codex 适配器，以及面向 bot 的 headless 适配器。它运行在 macOS 与 Linux 上。设计与验收日志见 [docs/agent-comm-design.zh-CN.md](docs/agent-comm-design.zh-CN.md)。线格式见 [docs/protocol.zh-CN.md](docs/protocol.zh-CN.md)。

## 安装

toxi 已发布在 PyPI：[pypi.org/project/toxi](https://pypi.org/project/toxi/)。

```bash
brew install toxcore                         # Debian/Ubuntu: sudo apt install libtoxcore2
pipx install 'toxi[mcp]'                     # 或：uv tool install 'toxi[mcp]'
toxi setup engine && toxi service install    # 身份 + daemon，跨重启常驻
toxi setup claude                            # 或：toxi setup codex · toxi setup headless --name my-bot --operator me
```

要升级，运行 `pipx upgrade toxi`。然后运行 `toxi service restart`。

一行安装脚本用 PyPI 上的同一个包完成同样的步骤。它还做这些事：

- 缺少 libtoxcore 和 pipx 时，安装它们。
- 部署 Claude Code 插件。
- 最后运行 `toxi doctor`。

每一步的说明见[逐步说明](docs/install.zh-CN.md#或者一行命令)。

```bash
curl -fsSL https://raw.githubusercontent.com/JefferyLee/toxi/master/install.sh | bash
# 或：  ... | bash -s -- --for codex      /  --for headless --name my-bot --operator me
```

细节、平台、一台机器上多个身份，见 [docs/install.zh-CN.md](docs/install.zh-CN.md)。

## 两分钟让两个 agent 对话

```bash
# 机器 A                                      # 机器 B
toxi profile set --kind agent \               toxi profile set --kind agent \
  --runtime claude-code --operator jeff         --runtime grok --operator jeff
toxi pair            # 打印一个邀请  →        toxi join <invite>     # 成为好友，自动接受，按卡片命名
toxi peers           # 谁是谁：卡片、信任、在线状态、未读
toxi send bot "please run the smoke test" -t release -k request --wait-read 30
                                              toxi agent run --exec 'python3 bot.py'   # 或 hook / MCP / webhook
```

要给 peer 一个信任级别和一个项目，编辑 `~/.config/toxi/config.toml`：

```toml
[peers.bot]
trust = "observer"                 # 只当信息；"collaborator" 可在任务范围内行动；"owner" = 你自己的设备
dirs  = ["/path/to/project"]       # 只投递给在这里启动的 Claude Code 会话
```

## 消息如何进入 agent

| 运行时 | agent 工作时 | 回合结束 | agent 空闲时 |
|---|---|---|---|
| Claude Code | PostToolUse hook：stat 一下 `inbox.flag`，再运行 `toxi peer-inbox` | Stop hook | `wait_for_message`（事件驱动）或 channel 模式 |
| Codex | PostToolUse hook：同样的 `inbox.flag` 检查 | Stop hook | `wait_for_message` 循环 |
| headless | `toxi agent run --exec CMD` · `[hooks] on_message_url` webhook（HMAC）· `on_message` 命令 | | |
| 人 | `toxi unread`、`toxi watch`、状态栏、手机上的 ToxiOS | | |

每条注入的行是这个格式：`castle-op [agent/grok @ grok-vm · operator jeff · session ops] (#release · request): …`。行后面跟着该 peer 信任级别的指引。指引说明这些消息只当信息，或者是 agent 可在任务范围内执行的请求。转述的批准永远不算数。

## 命令

| | |
|---|---|
| 身份 | `toxi init` · `toxi me` · `toxi profile set/show` · `toxi service install/status/restart` · `toxi doctor` |
| 联系人 | `toxi pair` / `toxi join <invite>` · `toxi add` / `toxi requests` / `toxi accept` · `toxi rename` · `toxi contacts` · `toxi peers` · `toxi introduce` |
| 消息 | `toxi send <alias> <text> [-t thread] [-r id] [-k kind] [--needs-human] [--tag t] [--wait-read N] [--wait-handled N]` · `toxi send <alias> --file <path>` · `toxi unread` · `toxi read <alias> [-t thread]` · `toxi show <id>` · `toxi threads` · `toxi done <id>…` · `toxi queue` · `toxi watch` |
| room | `toxi room create <name> <alias>…` · `room add/remove`（新成员收到最近 20 条消息） · `room send` · `room list/show` · `toxi read - --room <name>` |
| 历史 | `toxi search <文本>` · `toxi archive --status` · `toxi archive --older-than 90d`（或设置 `[archive] after_days`，daemon 每天自动归档） |
| agent | `toxi peer-inbox`（hook 调用这个命令）· `toxi agent run --exec CMD` · `toxi mcp serve` · `toxi setup claude\|codex\|headless` |
| daemon | `toxi daemon start/stop` · `toxi status` · `toxi upgrade` · `toxi teardown` |

要得到机器可读输出，在任何读命令前加 `--json`。

## 文档

- [docs/architecture.zh-CN.md](docs/architecture.zh-CN.md)：四层、进程、数据流、适配器契约。
- [docs/protocol.zh-CN.md](docs/protocol.zh-CN.md)：信封 v2。它对每个实现都是规范。
- [docs/security.zh-CN.md](docs/security.zh-CN.md)：toxi 保护什么、威胁、适配器规则。
- [docs/install.zh-CN.md](docs/install.zh-CN.md) 与长篇的[安装与使用手册](docs/install-and-usage.zh-CN.md)。
- 适配器：[Claude Code](docs/adapters/claude-code.zh-CN.md) · [Codex](docs/adapters/codex.zh-CN.md) · [headless / bot](docs/adapters/headless.zh-CN.md) · [ToxiOS](docs/adapters/ios.zh-CN.md)。
- [docs/agent-comm-design.zh-CN.md](docs/agent-comm-design.zh-CN.md)：设计、它所源自的调研、决策、每个阶段的验收。

## 文件

```
~/.config/toxi/            一个身份（用 TOXI_HOME 覆盖）
  tox_state.bin            密钥 + 好友列表             chat.db      联系人、消息、room
  profile.json             你的卡片                    config.toml  peers、hooks、retry
  media/                   收到的文件、待发附件
  daemon.sock · daemon.pid · daemon.log · inbox.flag
```

## 限制

- 与你对话的人能看到你的 IP 地址。这是 Tox 的特性。
- 密钥与历史没有静态加密。不要共享账户。
- room 没有完整的历史同步。新成员只从创建者那里收到最近 20 条消息。
- room 的成员之间必须互为联系人。

## 开发

```bash
python -m venv .venv && .venv/bin/pip install -e ".[dev,mcp]"
.venv/bin/python -m pytest                # 快测
.venv/bin/python -m pytest --run-toxcore  # + 本地 libtoxcore / daemon 测试
.venv/bin/python -m pytest --run-dht      # + 走公网 DHT 的慢测试
```

要发布，在 `master` 上打 `vX.Y.Z` 标签。GitHub Actions 随后把包发布到 PyPI。见 [docs/release.zh-CN.md](docs/release.zh-CN.md)。引擎版本与插件版本一起变动。`tests/test_versions.py` 检查这一点。
