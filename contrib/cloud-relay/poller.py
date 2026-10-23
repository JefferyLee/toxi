#!/usr/bin/env python3
"""Local half of the toxi cloud relay: take the messages that Claude Code cloud
sessions left in the Worker mailbox and send them over Tox, from the relay
identity, to the identity of the local sessions. A leading `@name` makes the
toxi routing deliver each message to that session.

Environment:
  TOXI_RELAY_URL   the Worker, e.g. https://toxi-inbox.<account>.workers.dev
  TOXI_HOME        the relay identity (e.g. ~/.config/toxi-cloud); its
                   `pull-token` file holds the PULL_TOKEN of the Worker
  TOXI_RELAY_TO    alias of the local identity in the relay's contacts (default: ada)
"""
import json
import os
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

URL = os.environ["TOXI_RELAY_URL"].rstrip("/")
HOME = Path(os.environ["TOXI_HOME"]).expanduser()
TO = os.environ.get("TOXI_RELAY_TO", "ada")
TOKEN = (HOME / "pull-token").read_text().strip()
INTERVAL = 5


def call(method: str, path: str, data=None) -> dict:
    req = urllib.request.Request(URL + path, method=method,
                                 data=json.dumps(data).encode() if data is not None else None,
                                 headers={"Authorization": f"Bearer {TOKEN}", "User-Agent": "toxi-cloud-relay",
                                          "Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=20) as r:
        return json.load(r)


def text(m: dict) -> str:
    where = f"cloud session {m['from']}" if m.get("from") else "a cloud session"
    head = f"@{m['to']} " if m.get("to") else ""
    return f"{head}[from {where}; one-way relay, a toxi reply does not reach it] {m['body']}"


def relay_once() -> None:
    done = []
    for m in call("GET", "/pull")["messages"]:
        r = subprocess.run(["toxi", "send", TO, text(m)], capture_output=True, text=True, timeout=30)
        if r.returncode != 0:
            print(f"send failed, will retry: {r.stderr.strip()}", file=sys.stderr, flush=True)
            break  # keep the order: retry this one and the rest next round
        done.append(m["id"])
    if done:
        call("POST", "/ack", {"ids": done})
        print(f"relayed {len(done)} message(s)", flush=True)


def main() -> None:
    while True:
        try:
            relay_once()
        except Exception as e:  # network down, Worker busy: try again next round
            print(f"relay error: {e}", file=sys.stderr, flush=True)
        time.sleep(INTERVAL)


if __name__ == "__main__":
    main()
