"""The release version is written in one place (src/toxi/__init__.py) and copied
by scripts/bump_version.py; every copy must match it."""
import json
import re
import tomllib
from pathlib import Path

import toxi


ROOT = Path(__file__).resolve().parents[1]


def _json(path: Path) -> dict:
    return json.loads(path.read_text())


def test_every_copy_of_the_version_matches_the_package():
    version = toxi.__version__
    project = tomllib.loads((ROOT / "pyproject.toml").read_text())["project"]
    assert "version" in project.get("dynamic", []) and "version" not in project
    assert _json(ROOT / "claude-code-plugin" / ".claude-plugin" / "plugin.json")["version"] == version
    assert _json(ROOT / "plugins" / "toxi" / ".codex-plugin" / "plugin.json")["version"] == version
    for readme in ("README.md", "README.zh-CN.md"):
        assert f"**{version}**" in (ROOT / readme).read_text(), readme
    for doc in ("docs/install.md", "docs/install.zh-CN.md"):
        assert re.search(rf"--ref v{re.escape(version)}\b", (ROOT / doc).read_text()), doc
