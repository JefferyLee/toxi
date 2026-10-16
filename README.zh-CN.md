# 🛰️ toxi

> **基于 Tox 的去中心化消息中间件，为 AI 编程代理而生。**
>
> 🌐 语言: [English](README.md) | **中文**

Toxi 让你在 AI 编程代理里直接和朋友聊天——**异步、端到端加密、完全去中心化**，无需服务器，基于 [Tox](https://tox.chat) 协议。当前以 Claude Code 插件的形式集成（slash 命令、未读通知、MCP 工具、底部状态栏指示器）。引擎本身与 AI 工具解耦，未来计划支持 Codex、Grok Builder 等。

> 状态：v0.2，macOS 优先。新用户可看[安装与使用手册](docs/install-and-usage.zh-CN.md)（实操向）；Codex 用户看 [Codex 中的 toxi 使用手册](docs/codex-usage.zh-CN.md)；完整设计见 [PRD](docs/prd.zh-CN.md)。

## 工作原理

两个进程（参见 PRD §3.2）：

- **`toxi-daemon`** —— 常驻后台进程，持有你的 Tox 身份、维持 DHT 连接，并把收到的消息写入本地 SQLite。
- **`toxi`** —— 短生命周期的 CLI，每次执行一条命令；通过 Unix socket 与 daemon 通信后退出。

所有数据本地化：密钥放在 `~/.config/toxi/`，没有云端。通过任何渠道交换 Tox ID 即可加好友。

## 依赖

- **Python 3.10+**
- **libtoxcore**（唯一的非 Python 依赖）：
  - macOS：`brew install toxcore`
  - Linux：安装发行版的 `toxcore` / `libtoxcore` 包

## 安装引擎

`toxi` 引擎是个普通程序（Python + 后台 daemon），需要 `libtoxcore`。它与下面的 Claude Code 插件分开安装。

```bash
# macOS 推荐 —— Homebrew tap 会自动拉取 libtoxcore：
brew install <owner>/tap/toxi              # 见 packaging/homebrew/

# 或者用 pipx（先装好 libtoxcore：brew install toxcore）：
pipx install git+https://github.com/JefferyLee/toxi      # 从源码安装，今天就能用
pipx install toxi                                        # 发布到 PyPI 后
# 加 MCP 工具支持（可选 extra）：
pipx install 'toxi[mcp]'
```

会安装两个命令：`toxi` 和 `toxi-daemon`。（PyPI/Homebrew 上的发布名是 `toxi`；Python 包名是 `toxi`。）

## 快速上手

```bash
# 1. 创建身份（生成你的 Tox 密钥对）
toxi init

# 2. 启动后台 daemon
toxi daemon start

# 3. 看自己的 Tox ID —— 通过任何渠道分享给朋友
toxi me

# 4. 通过朋友的 Tox ID 加好友（76 位十六进制）
toxi add bob 76518406F6A9F2217E8DC487...

#    朋友收到请求后，在他那边接受：
toxi requests                      # 看待处理请求的公钥
toxi accept alice <公钥前缀>

# 5. 开聊
toxi send bob "你看下我刚 push 的 PR，有空回我"
toxi unread                        # 看未读
toxi read bob                      # 看与 bob 的对话历史
toxi queue                         # 待发送队列
```

如果朋友离线，消息会本地入队，等他下次上线自动发出。

## 命令一览

| 命令 | 作用 |
|---|---|
| `toxi init` | 生成身份（一次性） |
| `toxi me` | 显示自己的 Tox ID、名字、连接状态 |
| `toxi set-name <名字>` | 设置展示名 |
| `toxi status` | daemon 状态、DHT 连接、联系人、队列、统计 |
| `toxi add <别名> <tox_id>` | 通过 Tox ID 加好友 |
| `toxi requests` | 待处理的好友请求 |
| `toxi accept <别名> <公钥前缀>` | 接受好友请求并给对方起本地别名 |
| `toxi pair` / `toxi join <邀请>` | 用一次性邀请和另一个身份互加好友：不用抄 Tox ID，自动接受 |
| `toxi peers` | 协作 agent：信任级别、在线状态、身份卡片、未读数 |
| `toxi profile show` / `toxi profile set …` | 本身份的卡片（类型、运行时、主机、运营者），发给好友 |
| `toxi contacts [--online]` | 列出联系人 |
| `toxi send <别名> <消息>` | 发消息（`-` 表示从 stdin 读；`--session` 标明发送会话） |
| `toxi unread [别名]` | 显示未读消息（并标为已读） |
| `toxi read <别名> [--limit N]` | 看对话历史 |
| `toxi queue` | 待发送队列 |
| `toxi introduce <to> <whom>` | 把一个联系人介绍给另一个 |
| `toxi introductions` | 别人介绍给你的联系人 |
| `toxi accept-intro <from> <whom> [--alias]` | 接受介绍 |
| `toxi daemon start` / `stop` | 管理后台 daemon |
| `toxi setup-engine` / `setup-claude` / `setup-codex` | 只接入引擎、Claude Code 或 Codex |
| `toxi doctor-codex` | 只读检查 Codex MCP/plugin 接入状态 |
| `toxi teardown-codex` | 移除 Codex 接入，不动身份和历史 |

## 配置

可选 `~/.config/toxi/config.toml`：

```toml
[retry]
ack_timeout_minutes = 5    # 多久未收到 ACK 就重发
fail_after_hours = 24      # 多久仍未确认就放弃（标记 failed）

[peers.codex]              # 一个作为协作 AI agent 的联系人
trust = "collaborator"     # "observer" = 只当信息，不产生副作用
dirs  = ["/path/to/project"]  # 只送给在这个目录启动的会话
```

协作 agent、身份卡片和 `on_message` hook 的说明见[安装与使用指南 §7](docs/install-and-usage.zh-CN.md#7-配置)。

## 文件布局

所有数据放在 `~/.config/toxi/`（可用 `TOXI_HOME` 环境变量覆盖——本机起两个 daemon 做测试时有用）：

```
tox_state.bin   Tox 密钥 + 好友列表
chat.db         SQLite：联系人、消息、队列
daemon.sock     IPC socket    daemon.pid   进程 PID    daemon.log   日志
config.toml     可选配置
profile.json    本身份的卡片（toxi profile set）
```

## Claude Code 集成

集成以**Claude Code 插件**的形式发布（`claude-code-plugin/`），一步装好通知 hook、slash 命令和 MCP server，**不用手动改配置**。前提是 `toxi` 引擎在 `PATH` 上（见上面"安装引擎"）。

### 装插件

```bash
# 开发期：直接从本地目录加载
claude --plugin-dir ./claude-code-plugin

# 或通过本仓库自带的 marketplace（推荐）：
/plugin marketplace add /path/to/this/repo        # 或推到 GitHub 后用 owner/repo
/plugin install toxi@toxi
```

### 插件提供什么

- **SessionStart hook** —— 会话开始时把未读消息注入 Claude 的上下文，非中文消息自动翻成中文。来信被明确标为「不可信个人内容、非指令」，防止有人通过消息内容做提示注入。
- **Slash 命令** —— `/unread`、`/send <别名> <消息>`、`/contacts`、`/status`（命名空间为 `/toxi:...`）。
- **状态栏集成** —— `toxi statusline` 输出一行摘要（`toxi: 📬 2 from macbook · 1/1 online`），可接到 Claude Code 的 `statusLine` 设置里，未读数会显示在底部状态条。
- **MCP 工具** —— `get_unread`、`read_history`、`view_media`、`mark_read`、`send_message`、`list_contacts`、`get_status`，让 Claude 能替你操作。`view_media` 把收到的图片以内联图片内容返回(语音/视频返回保存路径),这样 MCP 客户端能直接显示。需引擎的 `[mcp]` extra。

## Codex 集成（实验性）

仓库里也加入了第一版 Codex 插件包，位置是 `plugins/toxi/`。它包含：

- **MCP server 配置** —— 启动 `toxi mcp serve`，让 Codex 调用
  `get_unread`、`read_history`、`view_media`、`mark_read`、`send_message`、`list_contacts`、`get_status`。
  MCP server 会声明 instructions，保留“不可信来信”、“明确标已读”和“明确要求才发送”的边界。
- **生命周期 hook** —— 每轮结束后通过 Codex Stop-hook JSON 显示
  `toxi statusline` 摘要。插件故意不注册 SessionStart hook；未读消息只在你要求
  Codex 通过 MCP/CLI 读取时才会进入上下文。
- **Codex skill** —— 告诉 Codex 何时使用 toxi、如何限制读取范围，以及如何把来信当作不可信个人内容。

repo 级 Codex marketplace 入口在 `.agents/plugins/marketplace.json`。Codex 插件
安装目前需要从源码 checkout 运行（PyPI/pipx 安装的 engine 不包含 repo
marketplace 文件）。本地接入命令是：

```bash
toxi setup-codex
```

它会尽量安装 MCP extra、把 `toxi mcp serve` 注册到 Codex、把当前 checkout
加入 Codex plugin marketplace，并安装 `toxi` Codex 插件。它不会创建身份或启动
daemon；引擎初始化使用 `toxi setup-engine`。`toxi setup` 继续保持旧的
engine + Claude Code 组合 setup，`toxi setup-claude` 只接入 Claude Code。

用下面的只读检查确认非交互接入状态：

```bash
toxi doctor-codex
```

详细用法见 [Codex 中的 toxi 使用手册](docs/codex-usage.zh-CN.md)。

之后如果只想移除 Codex 集成：

```bash
toxi teardown-codex
```

### 机器可读输出

所有读命令都支持 `--json`（放在子命令前面）：`toxi --json unread`、`toxi --json status` 等。`--json` 模式下 `unread` / `read` 是**只读 peek**，不会标已读。

## 发布

- **引擎 → PyPI**：`python -m build` + `twine upload` → 用户 `pipx install toxi`。
- **引擎 → Homebrew**：`packaging/homebrew/toxi.rb` 是 tap formula 模板，`depends_on "toxcore"` 让 `brew install` 顺带把 libtoxcore 装上。通过个人 tap 发布（`brew tap <owner>/<name>`）。
- **插件 → marketplace**：`.claude-plugin/marketplace.json` 让本仓库本身就是个 marketplace。推到 GitHub 后别人就能 `/plugin marketplace add JefferyLee/toxi` 然后 `/plugin install toxi`。
- **Codex 插件 → marketplace**：`.agents/plugins/marketplace.json` 暴露 `plugins/toxi/` 下的实验性 Codex 插件。
- **版本策略**：引擎、Python 包元数据、Claude Code 插件、Codex 插件共用 `pyproject.toml` 里的同一个发布版本。

## v1 已知限制

- macOS 优先（Linux 在有 libtoxcore 的前提下应能工作，但还没作为正式测试目标）。
- 本地数据库和私钥**未加密**——靠文件系统权限保护，不要在共享/不可信机器上用。
- 与你聊天的人能看到你的公网 IP（Tox 协议特性）。
- 不支持群聊、实时语音/视频通话、多设备同步。通过 Tox 文件传输发来的**图片、语音/视频文件**会被接收并保存到 `~/.config/toxi/media/`;但暂不支持发送媒体。

## 开发

```bash
python -m venv .venv && .venv/bin/pip install -e ".[dev]"
.venv/bin/python -m pytest                # 快测；默认跳过真实 toxcore/DHT
.venv/bin/python -m pytest --run-toxcore  # 包含本地 libtoxcore/daemon 测试
.venv/bin/python -m pytest --run-dht      # 包含走公网 Tox DHT 的慢测试
```
