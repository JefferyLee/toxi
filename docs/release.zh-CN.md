# 发布流程

> 🌐 Languages: [English](release.md) | **中文**

`toxi` 发版的维护者参考。版本号只写在一处：`src/toxi/__init__.py`。`pyproject.toml` 从那里读取，一个脚本把它复制到其他显示版本号的文件。GitHub Action 先跑测试再发布；tag 和版本号不一致时，它拒绝发布。

## 0. 前置条件

- PyPI 的 **trusted publisher** 已为 `toxi` 项目配置好，范围是本仓库和 `pypi` GitHub Environment。仓库里没有 API token，只走 OIDC。
- 你可以推送到 `master`，也可以推送 tag。
- 工作区是干净的（`git status` 没有输出）。

## 1. 设定版本号

```bash
scripts/bump_version.py X.Y.Z
```

脚本把版本号写进这些文件：

```
src/toxi/__init__.py                              __version__（唯一来源；pyproject.toml 读取它）
claude-code-plugin/.claude-plugin/plugin.json     "version"
plugins/toxi/.codex-plugin/plugin.json            "version"
README.md、README.zh-CN.md                        状态行
docs/install.md、docs/install.zh-CN.md            --ref 示例
```

然后把发布说明写进 `docs/agent-comm-design.md` §11 及其中文镜像，放在 §12 之前。运行测试：

```bash
.venv/bin/python -m pytest -q          # tests/test_versions.py 检查每一处副本
```

## 2. 提交、打 tag、推送

```bash
git add -A
git commit -m "chore(release): X.Y.Z"
git tag vX.Y.Z
git push origin master vX.Y.Z
```

tag 必须是版本号前面加 `v`。推送 tag 会启动 `.github/workflows/publish.yml`。

## 3. 工作流做什么

1. **测试**：运行 `tests.yml`（Linux 和 macOS 上的 pytest）。有测试失败，就什么都不发布。
2. **核对**：把去掉 `v` 的 tag 与 `src/toxi/__init__.py` 里的 `__version__` 比较。不一致就停止。
3. **构建**：`python -m build` 生成 sdist 和 wheel。
4. **发布**：`pypa/gh-action-pypi-publish` 经 `pypi` environment 和 OIDC 上传。

用 `gh run watch` 观察。某一步失败时，先修好原因，删掉 tag（`git tag -d vX.Y.Z && git push --delete origin vX.Y.Z`），再从第 2 步重来。

## 4. GitHub release 与核实

```bash
gh release create vX.Y.Z --title "vX.Y.Z" --notes "..."
pipx install --force "toxi[mcp]==X.Y.Z" --pip-args="--no-cache-dir"
toxi --version
```

刚上传之后，包索引可能还给出旧版本。如果 `toxi --version` 显示旧版本，等一分钟再装一次。`--no-cache-dir` 让 pip 不用缓存里的旧索引。

## 5. 部署

在每台机器上：

```bash
pipx install --force "toxi[mcp]==X.Y.Z" --pip-args="--no-cache-dir"   # 编辑式安装跳过这一步
TOXI_HOME=<每个身份目录> toxi service install                         # 让每个 daemon 用新代码重启
toxi reinstall-plugin                                                # Claude Code 插件
```

Codex 插件如果来自固定在某个 tag 的 Git marketplace，登记新的 tag 并重新添加插件。然后重启 agent 会话，因为正在运行的 `toxi mcp serve` 一直用它启动时的代码。

## 6. 发布之后

- 如果这次发布改了线路格式，给 ToxiOS 开一个工作项（见 `docs/WORKFLOWS.md`）。

## 不要做

- **修改已发布 tag 的提交。** wheel 记着那个提交，改写会让它对不上。
- **手改版本号的某一处副本。** 用 `scripts/bump_version.py`；任何不一致都会让 `tests/test_versions.py` 失败。
- **手动上传 wheel。** 只有 OIDC trusted publisher 这一条路；PyPI 会拒绝 `twine upload`（没有登记 token）。
