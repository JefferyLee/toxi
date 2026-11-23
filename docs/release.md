# Release flow

> 🌐 Languages: **English** | [中文](release.zh-CN.md)

Maintainer reference for a `toxi` release. The version lives in one place, `src/toxi/__init__.py`. `pyproject.toml` reads it from there, and a script copies it to the other files that show it. The GitHub Action runs the tests before it publishes, and it refuses to publish when the tag and the version differ.

## 0. Prerequisites

- PyPI **trusted publisher** is configured for the `toxi` project, for this repo and the `pypi` GitHub Environment. There is no API token in the repo: OIDC only.
- You can push to `master` and push tags.
- The working tree is clean (`git status` shows nothing).

## 1. Set the version

```bash
scripts/bump_version.py X.Y.Z
```

The script writes the version to these files:

```
src/toxi/__init__.py                              __version__ (the source; pyproject.toml reads it)
claude-code-plugin/.claude-plugin/plugin.json     "version"
plugins/toxi/.codex-plugin/plugin.json            "version"
README.md, README.zh-CN.md                        the status line
docs/install.md, docs/install.zh-CN.md            the --ref example
```

Then add the release notes to `docs/agent-comm-design.md` §11 and to its zh mirror, before §12. Run the tests:

```bash
.venv/bin/python -m pytest -q          # tests/test_versions.py checks every copy
```

## 2. Commit, tag and push

```bash
git add -A
git commit -m "chore(release): X.Y.Z"
git tag vX.Y.Z
git push origin master vX.Y.Z
```

The tag must be the version with a leading `v`. The push of the tag starts `.github/workflows/publish.yml`.

## 3. What the workflow does

1. **Tests**: it runs `tests.yml` (pytest on Linux and macOS). If a test fails, nothing is published.
2. **Verify**: it compares the tag without `v` with `__version__` in `src/toxi/__init__.py`. If they differ, it stops.
3. **Build**: `python -m build` makes the sdist and the wheel.
4. **Publish**: `pypa/gh-action-pypi-publish` uploads them through the `pypi` environment and OIDC.

Watch it with `gh run watch`. If a step fails, fix the cause, delete the tag (`git tag -d vX.Y.Z && git push --delete origin vX.Y.Z`), and start again from step 2.

## 4. GitHub release and verification

```bash
gh release create vX.Y.Z --title "vX.Y.Z" --notes "..."
pipx install --force "toxi[mcp]==X.Y.Z" --pip-args="--no-cache-dir"
toxi --version
```

Right after the upload, the package index can still give the old version. If `toxi --version` shows it, wait a minute and run the install again. `--no-cache-dir` stops pip from using an old cached index.

## 5. Deploy

On each machine:

```bash
pipx install --force "toxi[mcp]==X.Y.Z" --pip-args="--no-cache-dir"   # an editable install skips this
TOXI_HOME=<each home> toxi service install                           # restarts each daemon on the new code
toxi reinstall-plugin                                                # Claude Code plugin
```

For a Codex plugin from a Git marketplace pinned to a tag, register the new tag and add the plugin again. Then restart the agent sessions, because a running `toxi mcp serve` keeps the code it started with.

## 6. After the release

- If the release changed the wire format, open a ToxiOS work item (see `docs/WORKFLOWS.md`).

## Do not

- **Amend a published tag's commit.** The wheel names that commit; a rewrite strands it.
- **Edit a version copy by hand.** Use `scripts/bump_version.py`; `tests/test_versions.py` fails on any mismatch.
- **Upload wheels by hand.** The OIDC trusted publisher is the only path; PyPI rejects `twine upload` (no token is registered).
