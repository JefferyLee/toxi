#!/usr/bin/env python3
"""Set the release version everywhere it is written: scripts/bump_version.py X.Y.Z

src/toxi/__init__.py is the source (pyproject.toml reads it); the plugin
manifests, the READMEs and the install docs carry copies.
tests/test_versions.py fails when they differ.
"""
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
COPIES = [
    ("src/toxi/__init__.py", r'__version__ = "[^"]+"', '__version__ = "{v}"'),
    ("claude-code-plugin/.claude-plugin/plugin.json", r'"version": "[^"]+"', '"version": "{v}"'),
    ("plugins/toxi/.codex-plugin/plugin.json", r'"version": "[^"]+"', '"version": "{v}"'),
    ("README.md", r"\*\*\d+\.\d+\.\d+\*\*", "**{v}**"),
    ("README.zh-CN.md", r"\*\*\d+\.\d+\.\d+\*\*", "**{v}**"),
    ("docs/install.md", r"--ref v\d+\.\d+\.\d+", "--ref v{v}"),
    ("docs/install.zh-CN.md", r"--ref v\d+\.\d+\.\d+", "--ref v{v}"),
]


def main() -> None:
    if len(sys.argv) != 2 or not re.fullmatch(r"\d+\.\d+\.\d+", sys.argv[1]):
        sys.exit("usage: scripts/bump_version.py X.Y.Z")
    v = sys.argv[1]
    for rel, pattern, repl in COPIES:
        p = ROOT / rel
        text, n = re.subn(pattern, repl.format(v=v), p.read_text(), count=1)
        if n != 1:
            sys.exit(f"{rel}: no version found to replace")
        p.write_text(text)
        print(f"{rel}: {v}")


if __name__ == "__main__":
    main()
