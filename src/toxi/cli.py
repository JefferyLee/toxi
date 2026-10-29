"""The `toxi` CLI (PRD §4.7). Short-lived: each command talks to the daemon over
IPC and exits. Only `init` and `daemon start` touch the filesystem/process
directly; everything else goes through the daemon.
"""
import json
import os
import shutil
import socket
import subprocess
import sys
import time

import click
from pathlib import Path

from . import __version__, bootstrap, client, config, envelope, invite, paths, peers, service, sessions
from .tox import Tox

_CONNECTION = {0: "offline", 1: "connected (TCP)", 2: "connected (UDP)"}


def _call(method: str, params: dict | None = None):
    try:
        return client.request(method, params)
    except client.DaemonNotRunning as e:
        raise click.ClickException(str(e))
    except client.DaemonError as e:
        raise click.ClickException(e.message)  # human-readable; code is for programs


def _emit(ctx, data) -> bool:
    """In --json mode, print `data` as JSON and return True so the caller stops.
    Otherwise return False and let the caller print the human-readable version."""
    if ctx.obj and ctx.obj.get("json"):
        click.echo(json.dumps(data, ensure_ascii=False))
        return True
    return False


def _default_session() -> str | None:
    """Label of the session sending a message (design §4.1): $TOXI_SESSION, else
    the name of the agent session that runs this command (see sessions.py), else
    the Claude Code project dir name. The CLI never guesses from the cwd."""
    s = os.environ.get("TOXI_SESSION")
    me = None if s else sessions.current()  # its unique name, so that a reply finds it
    if me:
        s = me["name"]
    elif not s and os.environ.get("CLAUDE_PROJECT_DIR"):
        s = os.path.basename(os.path.normpath(os.environ["CLAUDE_PROJECT_DIR"]))
    return (s or "")[:64] or None


def _default_session_dir() -> str | None:
    """The project dir of the session sending this, for reply routing (local only).
    Claude Code's Bash tool does not set CLAUDE_PROJECT_DIR, so the registered
    session's dir comes first."""
    me = sessions.current()
    return (me or {}).get("cwd") or os.environ.get("CLAUDE_PROJECT_DIR") or None


def _sender(m: dict) -> str:
    """`alias` or `session@alias` when the sender tagged the message with a session."""
    return f"{m['from_session']}@{m['alias']}" if m.get("from_session") else m["alias"]


@click.group()
@click.version_option(__version__, "--version", "-V", prog_name="toxi")
@click.option("--json", "as_json", is_flag=True, help="Output machine-readable JSON.")
@click.pass_context
def cli(ctx, as_json):
    """Decentralized, encrypted, asynchronous chat over the Tox protocol."""
    ctx.ensure_object(dict)
    ctx.obj["json"] = as_json


@cli.command()
def init():
    """Initialize your identity (generates your Tox keypair)."""
    paths.ensure_config_dir()
    sp = paths.tox_state_path()
    if sp.exists():
        t = Tox(savedata=sp.read_bytes())
        click.echo(f"Already initialized.\nYour Tox ID: {t.self_get_address_hex()}")
        t.kill()
        return
    t = Tox()
    address = t.self_get_address_hex()
    sp.write_bytes(t.get_savedata())
    sp.chmod(0o600)
    t.kill()
    click.echo(
        f"Initialized toxi.\n"
        f"Your Tox ID: {address}\n\n"
        f"Share this ID with friends so they can add you.\n"
        f"Next: start the daemon with `toxi daemon start`."
    )


@cli.command()
@click.pass_context
def me(ctx):
    """Show your Tox ID and display name."""
    info = _call("get_me")
    if _emit(ctx, info):
        return
    click.echo(f"Your Tox ID: {info['tox_id']}")
    if info["name"]:
        click.echo(f"Display name: {info['name']}")
    click.echo(f"Connection: {_CONNECTION.get(info['connection'], 'unknown')}")
    if info.get("dht_id"):
        click.echo(f"DHT node: {info['dht_id']} udp {info['udp_port']}  "
                   f"(another daemon can [tox] bootstrap to this)")


@cli.command(name="set-name")
@click.argument("name")
def set_name(name):
    """Set your display name."""
    _call("set_name", {"name": name})
    click.echo(f"Display name set to: {name}")


def _uptime(seconds: int) -> str:
    d, rem = divmod(seconds, 86400)
    h, rem = divmod(rem, 3600)
    m = rem // 60
    parts = [f"{d}d" if d else "", f"{h}h" if h else "", f"{m}m"]
    return " ".join(p for p in parts if p)


@cli.command()
@click.pass_context
def status(ctx):
    """Show daemon status, DHT connection, contacts, queue and recent stats."""
    st = _call("get_status")
    if _emit(ctx, st):
        return
    dht = _CONNECTION.get(st["connection"], "offline") if st["dht_connected"] else "not connected"
    click.echo(f"Daemon: running (PID {st['pid']})")
    click.echo(f"Uptime: {_uptime(st['uptime_seconds'])}")
    click.echo(f"DHT: {dht}")
    click.echo(f"Tox ID: {st['tox_id']}")
    if st["name"]:
        click.echo(f"Display Name: {st['name']}")

    click.echo(f"\nContacts: {st['contacts_total']} total, {st['contacts_online']} online")
    for c in st["contacts"]:
        mark = "✓" if c["is_online"] else "○"
        state = "online" if c["is_online"] else "offline"
        seen = f" (last seen {_ago(c['last_seen'])})" if c["last_seen"] else ""
        click.echo(f"  {mark} {c['alias']:12} {state}{seen}")

    click.echo(f"\nQueue: {st['queue_size']} messages pending")
    s = st["stats_24h"]
    click.echo(f"Stats (last 24h): sent {s['sent']} / received {s['received']} / failed {s['failed']}")


@cli.command()
@click.argument("alias")
@click.argument("tox_id")
def add(alias, tox_id):
    """Add a friend by their Tox ID: toxi add <alias> <tox_id>."""
    _call("add_contact", {"alias": alias, "tox_id": tox_id})
    click.echo(f"Added {alias}. Friend request sent — waiting for them to accept.")


@cli.command()
@click.argument("alias")
@click.option("--yes", "-y", is_flag=True, help="Skip the confirmation prompt.")
def remove(alias, yes):
    """Remove a contact and delete your message history with them."""
    if not yes:
        click.confirm(
            f"Remove '{alias}' and delete all messages with them?", abort=True
        )
    _call("remove_contact", {"alias": alias})
    click.echo(f"✓ Removed {alias}.")


@cli.command()
@click.pass_context
def requests(ctx):
    """Show pending friend requests."""
    reqs = _call("list_requests")["requests"]
    if _emit(ctx, reqs):
        return
    if not reqs:
        click.echo("No pending friend requests.")
        return
    click.echo(f"[{len(reqs)} pending friend request(s)]")
    for r in reqs:
        click.echo(f"  {r['public_key']}")
        if r["message"]:
            click.echo(f"    \"{r['message']}\"")
    click.echo("\nAccept with: toxi accept <alias> <public-key-prefix>")


@cli.command()
@click.argument("alias")
@click.argument("public_key")
def accept(alias, public_key):
    """Accept a friend request: toxi accept <alias> <public-key-prefix>."""
    _call("accept_request", {"alias": alias, "public_key": public_key})
    click.echo(f"Accepted. Added as '{alias}'.")


@cli.command()
@click.option("--online", "online_only", is_flag=True, help="Only show online contacts.")
@click.pass_context
def contacts(ctx, online_only):
    """List your contacts."""
    cs = _call("list_contacts", {"online_only": online_only})["contacts"]
    if _emit(ctx, cs):
        return
    if not cs:
        click.echo("No contacts yet.")
        return
    for c in cs:
        mark = "✓" if c["is_online"] else "○"
        state = "online" if c["is_online"] else "offline"
        click.echo(f"  {mark} {c['alias']:12} {state}")


def _ago(ts: int) -> str:
    d = max(0, int(time.time()) - ts)
    if d < 60:
        return f"{d}s ago"
    if d < 3600:
        return f"{d // 60}m ago"
    if d < 86400:
        return f"{d // 3600}h ago"
    return f"{d // 86400}d ago"


_MEDIA_LABEL = {"image": "📷 image", "voice": "🎤 voice message",
                "video": "🎬 video", "file": "📎 file"}


def _content(m: dict) -> str:
    """Render a message body for the terminal: thread/kind tags first, media rows
    show type + saved path."""
    tag = peers.meta_tag(m).strip()
    prefix = f"{tag} " if tag else ""
    if m.get("msg_type", "text") == "text":
        return prefix + m["content"]
    label = _MEDIA_LABEL.get(m["msg_type"], "📎 file")
    return f"{prefix}[{label}] {m['content']}"


@cli.command()
@click.argument("alias")
@click.argument("message", required=False)
@click.option("--file", "file_path", type=click.Path(exists=True, dir_okay=False), default=None,
              help="Send this file (any MESSAGE goes first as a text in the same thread).")
@click.option("--session", default=None,
              help="Label of the session/project sending this (default: $TOXI_SESSION, "
                   "else the Claude Code project dir name).")
@click.option("--wait-read", "wait_read", default=0, metavar="SECONDS",
              help="Wait up to this long for the recipient's read receipt.")
@click.option("--wait-handled", "wait_handled", default=0, metavar="SECONDS",
              help="Wait up to this long for the recipient's `done` receipt (answered / handled).")
@click.option("-t", "--thread", default=None, help="Thread / topic id, e.g. pr-123.")
@click.option("-r", "--reply-to", "reply_to", default=None, metavar="MSG_ID",
              help="The message id this answers.")
@click.option("-k", "--kind", type=click.Choice(list(envelope.KINDS)), default="msg",
              show_default=True,
              help="request = expects an answer; report = status; ack = 'got it' (never wakes "
                   "the peer); relay = you are passing on someone else's words.")
@click.option("--needs-human", is_flag=True,
              help="Flag that a person, not the peer agent, must decide this.")
@click.option("--tag", "tags", multiple=True, help="Free-form tag (repeatable), e.g. WARN.")
@click.pass_context
def send(ctx, alias, message, file_path, session, wait_read, wait_handled, thread, reply_to, kind,
         needs_human, tags):
    """Send a message: toxi send <alias> <message> (use - to read from stdin), or a file
    with --file. Bodies over 64 KB go as a text file automatically."""
    if message == "-":
        message = sys.stdin.read().rstrip("\n")
    if message is None and not file_path:
        raise click.UsageError("give a MESSAGE, or --file PATH, or both")
    meta = {"session": session or _default_session(), "session_dir": _default_session_dir(),
            "thread": thread, "reply_to": reply_to, "kind": kind, "needs_human": needs_human,
            "tags": list(tags)}
    if message is not None:
        res = _call("send_message", {"alias": alias, "body": message, **meta})
    if file_path:
        fres = _call("send_file", {"alias": alias, "path": file_path, **meta})
        if message is None:
            res = fres
        elif _emit_quiet(ctx):
            res = {"text": res, "file": fres, "msg_uuid": res["msg_uuid"], "status": res["status"]}
        else:
            click.echo(f"✓ file {fres['name']} ({fres['size']} bytes): {fres['status']}")
    if res.get("as_file"):
        click.echo(f"ℹ body over 64 KB — sent as a text file ({res['size']} bytes)", err=True)
    if wait_handled > 0:
        res["status"] = _wait_status(res["msg_uuid"], wait_handled, ("handled", "failed"))
    elif wait_read > 0:
        res["status"] = _wait_status(res["msg_uuid"], wait_read, ("read", "handled", "failed"))
    if wait_read or wait_handled:
        res["read"] = res["status"] in ("read", "handled")
        res["handled"] = res["status"] == "handled"
    if _emit(ctx, res):
        return
    parts = f" ({res['parts']} parts)" if res.get("parts") else ""
    if res["status"] == "handled":
        click.echo(f"✓ handled by {alias}{parts}")
    elif res["status"] == "read":
        click.echo(f"✓ read by {alias}{parts}" + (
            f" — not handled within {wait_handled}s" if wait_handled > 0 else ""))
    elif res["status"] == "sent" or res["status"] == "delivered":
        click.echo(f"✓ {res['status']}{parts}" + (
            f" — not read within {wait_read}s" if wait_read > 0 else ""))
    else:
        click.echo(f"✓ {alias} is offline — queued{parts} (will send when they come online)")


def _emit_quiet(ctx) -> bool:
    return bool(ctx.obj and ctx.obj.get("json"))


def _wait_status(msg_uuid: str, seconds: int, until=("read", "handled", "failed")) -> str:
    """Poll a sent message until its status is one of `until` or `seconds` pass."""
    deadline = time.monotonic() + seconds
    status = "sent"
    while True:
        m = _call("get_message", {"msg_uuid": msg_uuid}).get("message") or {}
        status = m.get("status", status)
        if status in until or time.monotonic() >= deadline:
            return status
        time.sleep(0.5)


def _parse_age(text: str) -> int:
    """'90d' / '12w' / '6m' / '1y' -> days."""
    t = text.strip().lower()
    mult = {"d": 1, "w": 7, "m": 30, "y": 365}
    try:
        if t[-1] in mult:
            return int(t[:-1]) * mult[t[-1]]
        return int(t)
    except (ValueError, IndexError):
        raise click.BadParameter("use a number of days, or e.g. 90d, 12w, 6m, 1y")


@cli.command()
@click.option("--older-than", "older", default="90d", show_default=True,
              help="Age of the messages to archive: 90d, 12w, 6m, 1y, or a number of days.")
@click.option("--alias", default=None, help="Only this contact's messages.")
@click.option("--delete", is_flag=True, help="Delete instead of moving to archive.db (DESTRUCTIVE).")
@click.option("--dry-run", is_flag=True, help="Only count what would move.")
@click.option("--status", "show_status", is_flag=True, help="Show database and archive sizes.")
@click.pass_context
def archive(ctx, older, alias, delete, dry_run, show_status):
    """Move old delivered/received messages into archive.db so chat.db stays small.

    Undelivered messages are never archived. `[archive] after_days = N` in
    config.toml makes the daemon do this daily by itself.
    """
    if show_status:
        st = _call("archive_status")
        if _emit(ctx, st):
            return
        oldest = _ago(st["oldest"]) if st["oldest"] else "-"
        click.echo(f"chat.db: {st['messages']} messages ({st['db_bytes'] // 1024} KB), oldest {oldest}\n"
                   f"archive.db: {st['archived']} messages ({st['archive_bytes'] // 1024} KB)\n"
                   f"auto-archive: " + (f"after {st['auto_after_days']} days" if st["auto_after_days"] else "off"))
        return
    days = _parse_age(older)
    if delete and not dry_run and not (ctx.obj and ctx.obj.get("json")):
        click.confirm(f"Permanently delete messages older than {days} days?", abort=True)
    res = _call("archive_messages", {"older_than_days": days, "alias": alias, "delete": delete,
                                     "dry_run": dry_run})
    if _emit(ctx, res):
        return
    verb = "would be " + ("deleted" if delete else "archived") if dry_run else ("deleted" if delete else "moved to archive.db")
    click.echo(f"✓ {res['matched']} message(s) older than {days} days {verb}")


@cli.command()
@click.argument("query")
@click.option("--alias", default=None, help="Only this contact.")
@click.option("-t", "--thread", default=None, help="Only this thread.")
@click.option("--limit", default=20, show_default=True)
@click.option("--archived", is_flag=True, help="Also search archive.db.")
@click.pass_context
def search(ctx, query, alias, thread, limit, archived):
    """Find messages whose text contains QUERY (case-insensitive)."""
    res = _call("search_messages", {"q": query, "alias": alias, "thread": thread, "limit": limit,
                                    "archived": archived})
    if _emit(ctx, res):
        return
    if not res["messages"]:
        click.echo("No matches.")
        return
    for m in res["messages"]:
        who = "you" if m["direction"] == "out" else m["alias"]
        peer = f" → {m['alias']}" if m["direction"] == "out" else ""
        tag = f" #{m['thread']}" if m.get("thread") else ""
        arc = " (archived)" if m.get("archived") else ""
        click.echo(f"  {who}{peer} ({_ago(m['created_at'])}){tag}{arc}: {m['content'][:120]}")


@cli.command()
@click.argument("msg_uuids", nargs=-1, required=True)
def done(msg_uuids):
    """Mark incoming messages handled: the senders get a `done` receipt. (A reply
    with -r <id> does this automatically.)"""
    res = _call("mark_handled", {"msg_uuids": list(msg_uuids)})
    click.echo(f"✓ {res['handled']} message(s) marked handled")


_RESET = "\033[0m"
_BOLD_RED = "\033[1;31m"
_YELLOW = "\033[33m"
_DIM = "\033[2m"


def _toxi_segment() -> str:
    """The toxi part of the status line (colored, width-aware). Never raises."""
    try:
        st = client.request("get_status")
        msgs = client.request("get_messages", {"unread_only": True, "limit": 100})["messages"]
        msgs, shared = peers.split(msgs, sessions.current())
    except client.DaemonNotRunning:
        return f"{_YELLOW}toxi: offline{_RESET}"
    except Exception:  # a status line must never crash the prompt with a traceback
        return f"{_YELLOW}toxi: error{_RESET}"

    name = st.get("name", "").strip()
    prefix = f"toxi({name})" if name else "toxi"
    online = f"{st['contacts_online']}/{st['contacts_total']} online"
    if shared:  # messages for no session in particular (design: session routing)
        online = f"📥 {len(shared)} shared · {online}"
    if not msgs:
        return f"{prefix}: {_DIM}{online}{_RESET}"

    senders = []
    for m in msgs:
        if m["alias"] not in senders:
            senders.append(m["alias"])
    # Collapse the sender list on narrow terminals so the row doesn't overflow.
    max_names = 1 if shutil.get_terminal_size((80, 24)).columns < 60 else 2
    if len(senders) <= max_names:
        who = ", ".join(senders)
    else:
        who = ", ".join(senders[:max_names]) + f" +{len(senders) - max_names}"
    count = f"{_BOLD_RED}📬 {len(msgs)}{_RESET}"
    return f"{prefix}: {count} from {who} {_DIM}· {online}{_RESET}"


def _run_wrapped_statusline() -> str:
    """Run the user's pre-existing statusLine command, if toxi wrapped one."""
    cmd = bootstrap.wrapped_statusline_command()
    if not cmd:
        return ""
    # Claude Code pipes session JSON on stdin; forward it to the wrapped command.
    stdin_data = ""
    try:
        if not sys.stdin.isatty():
            stdin_data = sys.stdin.read()
    except (OSError, ValueError):
        pass
    try:
        r = subprocess.run(cmd, shell=True, input=stdin_data,
                           capture_output=True, text=True, timeout=3)
    except (subprocess.SubprocessError, OSError):
        return ""
    return r.stdout.strip()


@cli.command()
@click.option("--segment", is_flag=True,
              help="Print only the toxi chunk (no newline, no wrapped command) to splice into your own status line.")
def statusline(segment):
    """One-line summary for Claude Code's statusLine setting (never fails noisily)."""
    seg = _toxi_segment()
    # color=True keeps our ANSI codes — statusLine runs piped, and click.echo
    # would otherwise strip them as if writing to a non-terminal.
    if segment:
        click.echo(seg, color=True, nl=False)
        return
    wrapped = _run_wrapped_statusline()
    if wrapped:
        click.echo(f"{seg} {_DIM}│{_RESET} {wrapped}", color=True)
    else:
        click.echo(seg, color=True)


@cli.command()
@click.argument("alias", required=False)
@click.option("--all", "show_all", is_flag=True,
              help="In an agent session: also show the shared inbox.")
@click.pass_context
def unread(ctx, alias, show_all):
    """Show unread messages (optionally just from one contact).

    In an agent session, shows only the messages for this session; the
    messages of other sessions on this identity are never shown."""
    msgs = _call("get_messages", {"alias": alias, "unread_only": True, "limit": 100})["messages"]
    mine, shared = peers.split(msgs, sessions.current())
    keep = {id(m) for m in (mine + shared if show_all else mine)}
    msgs = [m for m in msgs if id(m) in keep]
    hidden = 0 if show_all else len(shared)
    # --json is a programmatic peek (e.g. the Claude Code hook); it must not mark read.
    if _emit(ctx, msgs):
        return
    if not msgs:
        click.echo("No unread messages.")
    else:
        click.echo(f"[{len(msgs)} unread]")
        for m in reversed(msgs):  # oldest first
            click.echo(f"  {_sender(m)} ({_ago(m['created_at'])}): {_content(m)}")
        _call("mark_read", {"msg_uuids": [m["msg_uuid"] for m in msgs]})
    if hidden:
        click.echo(f"{hidden} more in the shared inbox: `toxi unread --all` shows them.")


@cli.command()
@click.argument("alias")
@click.option("--limit", default=20, help="How many recent messages to show.")
@click.option("--oldest-first", is_flag=True,
              help="With --json, list oldest first (the terminal view always does).")
@click.option("-t", "--thread", default=None, help="Only this thread.")
@click.option("--room", default=None, help="Messages of this room (ALIAS may be -).")
@click.pass_context
def read(ctx, alias, limit, oldest_first, thread, room):
    """Show conversation history with a contact (or, with --room, a room)."""
    params = {"limit": limit, "thread": thread}
    if room:
        params["room"] = room
    if alias and alias != "-":
        params["alias"] = alias
    msgs = _call("get_messages", params)["messages"]
    if oldest_first:
        msgs = list(reversed(msgs))
    if _emit(ctx, msgs):  # programmatic peek: don't mark read
        return
    if oldest_first:
        msgs = list(reversed(msgs))  # the loop below reverses again
    if not msgs:
        click.echo("No messages yet.")
        return
    for m in reversed(msgs):  # oldest first
        who = "you" if m["direction"] == "out" else _sender(m)
        if m.get("recipients"):
            who = "you → " + ", ".join(r["alias"] for r in m["recipients"])
        click.echo(f"  {who} ({_ago(m['created_at'])}): {_content(m)}")
    unread_in = [m["msg_uuid"] for m in msgs if m["direction"] == "in" and m["read_at"] is None]
    if unread_in:
        _call("mark_read", {"msg_uuids": unread_in})


@cli.command(name="peer-inbox")
@click.option("--alias", help="Only this contact instead of all peer agents.")
@click.option("--cwd", help="Dir the session was started in, matched against [agents.dirs].")
@click.pass_context
def peer_inbox(ctx, alias, cwd):
    """Deliver unread peer-agent messages (marks them read). Used by Stop hooks."""
    msgs = peers.take(alias, cwd)
    if _emit(ctx, msgs) or not msgs:
        return
    click.echo(peers.as_prompt(msgs))


@cli.command(name="peers")
@click.pass_context
def peers_cmd(ctx):
    """Peer agents: trust level, presence, profile card, unread count."""
    try:
        rows = peers.overview()
    except client.DaemonNotRunning as e:
        raise click.ClickException(str(e))
    if _emit(ctx, rows):
        return
    if not rows:
        click.echo("No peers configured. Add a [peers.<alias>] table to config.toml.")
        return
    for r in rows:
        mark = "✓" if r["online"] else "○"
        prof = r.get("profile") or {}
        card = "/".join(x for x in (prof.get("kind"), prof.get("runtime")) if x)
        if prof.get("host"):
            card += f" @ {prof['host']}"
        if prof.get("operator"):
            card += f" · operator {prof['operator']}"
        if prof.get("role"):
            card += f" · {prof['role']}"
        extra = f" · 📬 {r['unread']}" if r["unread"] else ""
        if not r["known"]:
            extra += " (not a contact yet)"
        click.echo(f"  {mark} {r['alias']:12} {(r['trust'] or 'contact'):12} "
                   f"{card or '(no profile card yet)'}{extra}")
        if r["dirs"]:
            click.echo(f"      ↳ delivered only to sessions started in: {', '.join(r['dirs'])}")


@cli.group()
def profile():
    """This identity's profile card: who you are, what runtime, who operates you."""


@profile.command("show")
@click.pass_context
def profile_show(ctx):
    """Show the card friends receive from you."""
    prof = _call("get_profile")["profile"]
    if _emit(ctx, prof):
        return
    if not prof:
        click.echo("No profile yet. Set one with, e.g.:\n"
                   "  toxi profile set --kind agent --runtime claude-code --operator <you>")
        return
    for k in envelope.PROFILE_KEYS:
        if k in prof:
            v = prof[k]
            click.echo(f"  {k:9} {', '.join(v) if isinstance(v, list) else v}")


@profile.command("set")
@click.option("--kind", type=click.Choice(list(envelope.PROFILE_KINDS)))
@click.option("--name", help="Display name (default: your Tox name).")
@click.option("--runtime", help="claude-code | codex | grok | toxi-cli | toxios | other")
@click.option("--model")
@click.option("--host", help="Machine name (default: this hostname).")
@click.option("--operator", help="Who runs this identity.")
@click.option("--role", help="One line on what this identity does.")
def profile_set(**fields):
    """Set or update fields of your card; online friends get it immediately."""
    fields = {k: v for k, v in fields.items() if v is not None}
    if not fields:
        raise click.UsageError("nothing to set — see `toxi profile set --help`")
    if "host" not in fields and not _call("get_profile")["profile"].get("host"):
        fields["host"] = socket.gethostname()
    res = _call("set_profile", {"profile": fields})
    click.echo(f"✓ profile updated (sent to {res['sent_to']} online contact(s))")


@cli.command()
@click.option("--ttl", default=600, show_default=True, help="Seconds the invite stays valid.")
@click.pass_context
def pair(ctx, ttl):
    """Mint a one-time invite; the other side runs `toxi join <invite>` and you're friends."""
    res = _call("create_invite", {"ttl": ttl})
    if _emit(ctx, res):
        return
    click.echo(f"Invite (valid {ttl // 60} min, single use) — share it over a channel you trust:\n"
               f"\n  {res['invite']}\n\nOn the other machine:\n  toxi join {res['invite']}")


@cli.command()
@click.argument("invite_text", metavar="INVITE")
@click.option("--as", "alias", default=None,
              help="Local alias for them (default: the name in the invite).")
def join(invite_text, alias):
    """Accept a `toxi pair` invite: adds them, and their daemon accepts automatically."""
    try:
        data = invite.decode(invite_text)
    except invite.InvalidInvite as e:
        raise click.ClickException(str(e))
    alias = alias or (invite.safe_alias(data["name"]) if data.get("name") else None)
    if not alias:
        raise click.ClickException("the invite carries no name — pass --as <alias>")
    me = (_call("get_profile")["profile"].get("name") or _call("get_me")["name"]
          or socket.gethostname())
    _call("add_contact", {"alias": alias, "tox_id": data["tox_id"],
                          "request_msg": invite.request_message(data["nonce"], me)})
    click.echo(f"✓ Added {alias}. Their daemon accepts automatically; `toxi contacts` shows "
               f"them online once the DHT connects (usually under a minute).")


def _meta_options(fn):
    for opt in reversed([
        click.option("-t", "--thread", default=None, help="Thread / topic id, e.g. pr-123."),
        click.option("-k", "--kind", type=click.Choice(list(envelope.KINDS)), default="msg",
                     show_default=True, help="request / report / ack / relay (see `toxi send --help`)."),
        click.option("--needs-human", is_flag=True, help="A person, not the peer agent, must decide this."),
        click.option("--tag", "tags", multiple=True, help="Free-form tag (repeatable)."),
        click.option("--session", default=None, help="Label of the sending session."),
    ]):
        fn = opt(fn)
    return fn


@cli.group()
def room():
    """Rooms: one message to several contacts at once (app-layer fan-out)."""


@room.command("create")
@click.argument("name")
@click.argument("aliases", nargs=-1, required=True)
@click.pass_context
def room_create(ctx, name, aliases):
    """Create a room with these contacts; each gets a member card (when online)."""
    res = _call("create_room", {"name": name, "aliases": list(aliases)})
    if _emit(ctx, res):
        return
    told = ", ".join(res["card_sent_to"]) or "nobody yet (offline members get it when they connect)"
    click.echo(f"✓ room {res['name']} ({res['room_id'][:8]}) with {res['members']} members; "
               f"card sent to: {told}\n  Members must be contacts of each other to see each "
               f"other's messages (`toxi introduce` helps).")


@room.command("add")
@click.argument("room_ref", metavar="ROOM")
@click.argument("aliases", nargs=-1, required=True)
@click.pass_context
def room_add(ctx, room_ref, aliases):
    """Add contacts to a room you created; every member gets the updated card."""
    res = _call("room_members", {"room": room_ref, "add": list(aliases)})
    if _emit(ctx, res):
        return
    click.echo(f"✓ {res['name']} now has {len(res['members'])} members; card sent to: "
               f"{', '.join(res['card_sent_to']) or 'nobody online yet'}")


@room.command("remove")
@click.argument("room_ref", metavar="ROOM")
@click.argument("aliases", nargs=-1, required=True)
@click.pass_context
def room_remove(ctx, room_ref, aliases):
    """Remove contacts from a room you created."""
    res = _call("room_members", {"room": room_ref, "remove": list(aliases)})
    if _emit(ctx, res):
        return
    click.echo(f"✓ {res['name']} now has {len(res['members'])} members")


@room.command("list")
@click.pass_context
def room_list(ctx):
    """Your rooms with member count, unread and last activity."""
    rooms = _call("list_rooms", {})["rooms"]
    if _emit(ctx, rooms):
        return
    if not rooms:
        click.echo("No rooms. Create one: toxi room create <name> <alias>...")
        return
    for r in rooms:
        unread = f" · 📬 {r['unread']}" if r["unread"] else ""
        last = f" · {_ago(r['last_at'])}" if r["last_at"] else ""
        mine = " · yours" if r["mine"] else ""
        click.echo(f"  {r['name']:20} {r['room_id'][:8]}  {r['members']} members{unread}{last}{mine}")


@room.command("show")
@click.argument("room_ref", metavar="ROOM")
@click.pass_context
def room_show(ctx, room_ref):
    """Members of a room and whether you can reach them."""
    r = _call("get_room", {"room": room_ref})["room"]
    if _emit(ctx, r):
        return
    click.echo(f"{r['name']} ({r['room_id']}){' · yours' if r['mine'] else ''}")
    for m in r["members"]:
        if m["me"]:
            click.echo(f"  • {m['name'] or 'me'} (you)")
        elif m["alias"]:
            click.echo(f"  {'✓' if m['online'] else '○'} {m['alias']} ({m['name']})")
        else:
            click.echo(f"  ✗ {m['name'] or m['public_key'][:8]} — not your contact; you will not "
                       f"see each other's messages until you are")


@room.command("send")
@click.argument("room_ref", metavar="ROOM")
@click.argument("message")
@_meta_options
@click.pass_context
def room_send(ctx, room_ref, message, thread, kind, needs_human, tags, session):
    """Send a message to every member of a room (use - to read from stdin)."""
    if message == "-":
        message = sys.stdin.read().rstrip("\n")
    res = _call("send_room", {"room": room_ref, "body": message, "thread": thread, "kind": kind,
                              "needs_human": needs_human, "tags": list(tags),
                              "session": session or _default_session(),
                              "session_dir": _default_session_dir()})
    if _emit(ctx, res):
        return
    done = ", ".join(f"{s['alias']} ({s['status']})" for s in res["sent"]) or "nobody"
    click.echo(f"✓ {res['name']}: {done}")
    if res["unreachable"]:
        click.echo(f"⚠ not your contacts, skipped: {', '.join(res['unreachable'])}")


@cli.command()
@click.argument("alias", required=False)
@click.pass_context
def threads(ctx, alias):
    """Threads in your conversations: messages, unread, pending decisions, last activity."""
    rows = _call("list_threads", {"alias": alias})["threads"]
    if _emit(ctx, rows):
        return
    if not rows:
        click.echo("No threads yet (send with -t <thread> to start one).")
        return
    for r in rows:
        flags = []
        if r["unread"]:
            flags.append(f"📬 {r['unread']}")
        if r["needs_human"]:
            flags.append(f"⚑ {r['needs_human']} needs your decision")
        if r.get("open_requests"):
            flags.append(f"⏳ {r['open_requests']} awaiting an answer")
        click.echo(f"  #{r['thread']:20} {r['alias']:12} {r['count']:3} msgs · "
                   f"{_ago(r['last_at'])}" + (" · " + " · ".join(flags) if flags else ""))


@cli.command()
@click.argument("msg_uuid")
@click.pass_context
def show(ctx, msg_uuid):
    """Print one message in full by its id (e.g. a long one injected as a summary)."""
    m = _call("get_message", {"msg_uuid": msg_uuid}).get("message")
    if m is None:
        raise click.ClickException(f"no message with id {msg_uuid}")
    if _emit(ctx, m):
        return
    who = "you" if m["direction"] == "out" else _sender(m)
    click.echo(f"{who} · {_ago(m['created_at'])} · {m['status']} · {m['msg_uuid']}")
    click.echo(_content(m))


@cli.command()
@click.option("--alias", "aliases", multiple=True, help="Only events about this contact (repeatable).")
@click.option("--context", default=0, metavar="N",
              help="Attach the last N messages with the sender to each new-message event.")
@click.pass_context
def watch(ctx, aliases, context):
    """Stream daemon events as they happen (new messages, presence, delivery, receipts)."""
    as_json = bool(ctx.obj and ctx.obj.get("json"))
    try:
        for ev in client.subscribe(aliases, context):
            if as_json:
                click.echo(json.dumps(ev, ensure_ascii=False))
                continue
            d, t = ev.get("data") or {}, time.strftime("%H:%M:%S", time.localtime(ev.get("ts", 0)))
            if ev["event"] == "message.new":
                click.echo(f"{t} message   {_sender(d)}: {_content(d)}")
            elif ev["event"] in ("contact.online", "contact.offline"):
                click.echo(f"{t} {ev['event'].split('.')[1]:9} {d.get('alias')}")
            else:
                click.echo(f"{t} {ev['event']:9} {d.get('alias')} {d.get('msg_uuid', '')} "
                           f"{d.get('reason', '')}".rstrip())
    except client.DaemonNotRunning as e:
        raise click.ClickException(str(e))
    except client.DaemonError as e:
        raise click.ClickException(e.message)
    except (KeyboardInterrupt, client.ipc.IPCError):
        pass


@cli.group(name="service")
def service_cmd():
    """Keep the daemon running across logins and reboots (launchd / systemd --user)."""


@service_cmd.command("install")
def service_install():
    """Install and start the background service for this identity."""
    if bootstrap.daemon_running():
        _call("shutdown")  # the service manager starts its own instance
        for _ in range(50):
            if not bootstrap.daemon_running():
                break
            time.sleep(0.1)
    try:
        p = service.install()
    except service.ServiceError as e:
        raise click.ClickException(str(e))
    click.echo(f"✓ service installed: {p}")
    for _ in range(50):
        if bootstrap.daemon_running():
            click.echo("✓ daemon is up under the service manager")
            return
        time.sleep(0.2)
    click.echo("⚠ service installed but the daemon has not answered yet — check "
               f"{paths.config_dir() / 'service.log'}")


@service_cmd.command("uninstall")
def service_uninstall():
    """Stop and remove the background service (identity and history are kept)."""
    if not service.uninstall():
        click.echo("no service installed for this identity")
    elif bootstrap.daemon_running():
        click.echo("✓ service definition removed; the daemon was not under a service manager "
                   "and keeps running")
    else:
        click.echo("✓ service removed (the daemon is stopped; `toxi daemon start` runs it by hand)")


@service_cmd.command("restart")
def service_restart():
    """Restart the daemon under the service manager (e.g. after an upgrade)."""
    if not service.installed():
        raise click.ClickException("no service installed — use `toxi service install`")
    try:
        service.restart()
    except service.ServiceError as e:
        raise click.ClickException(str(e))
    click.echo("✓ daemon restarted")


@service_cmd.command("status")
@click.pass_context
def service_status(ctx):
    """Is the service installed and running?"""
    st = service.status()
    if _emit(ctx, st):
        return
    if not st["installed"]:
        click.echo("not installed (`toxi service install` keeps the daemon alive across reboots)")
        return
    state = f"running (pid {st['pid']})" if st["running"] else "installed but not running"
    click.echo(f"{state} — {st['path']}")


@cli.command()
@click.argument("alias")
@click.argument("new_alias")
def rename(alias, new_alias):
    """Rename a contact locally (e.g. to match the name on their profile card)."""
    res = _call("rename_contact", {"alias": alias, "new_alias": new_alias})
    click.echo(f"✓ {res['was']} is now {res['alias']}")
    if alias in config.peer_config():
        click.echo(f"⚠ config.toml still has [peers.{alias}] — rename that table to "
                   f"[peers.{new_alias}] so routing and trust keep working.")


@cli.group()
def agent():
    """Run a headless agent (a bot or script) against toxi without MCP or hooks."""


@agent.command("run")
@click.option("--exec", "command", required=True, metavar="CMD",
              help="Shell command run once per peer message: it gets a JSON payload on stdin "
                   "and whatever it prints is sent back as the reply, in the same thread.")
@click.option("--alias", "aliases", multiple=True,
              help="Only these contacts (default: every peer in config.toml).")
@click.option("--context", default=5, show_default=True,
              help="Recent messages with the sender included in the payload.")
@click.option("--session", default="agent", show_default=True, help="Session label on replies.")
@click.option("--timeout", default=600, show_default=True, help="Seconds CMD may run.")
def agent_run(command, aliases, context, session, timeout):
    """Deliver each incoming peer message to CMD and send its stdout back.

    Payload: {"message": {...alias, content, thread, kind, from_session, profile...},
    "trust": ..., "guide": ..., "context": [recent messages, oldest first]}.
    Messages are marked read on delivery (so the sender gets a read receipt);
    acknowledgements are never delivered. Reconnects if the daemon restarts.
    """
    wanted = set(aliases) or None

    def handle(m: dict, ctx: list) -> None:
        payload = {"message": m, "trust": m.get("trust"),
                   "guide": peers.GUIDES.get(m.get("trust", "observer")), "context": ctx}
        try:
            r = subprocess.run(command, shell=True, input=json.dumps(payload, ensure_ascii=False),
                               capture_output=True, text=True, timeout=timeout)
        except subprocess.TimeoutExpired:
            click.echo(f"{time.strftime('%H:%M:%S')} {m['alias']}: command timed out", err=True)
            return
        reply = r.stdout.strip()
        if reply:
            _call("send_message", {"alias": m["alias"], "body": reply, "session": session,
                                   "thread": m.get("thread"), "reply_to": m["msg_uuid"],
                                   "kind": "report"})  # the reply_to also sends `done`
        elif r.returncode == 0:
            _call("mark_handled", {"msg_uuids": [m["msg_uuid"]]})
        note = " replied" if reply else ""
        click.echo(f"{time.strftime('%H:%M:%S')} {m['alias']}: {m['content'][:60]!r} "
                   f"-> rc={r.returncode}{note}")
        if r.returncode and r.stderr.strip():
            click.echo(r.stderr.strip()[-400:], err=True)

    def deliverable(alias: str) -> bool:
        return alias in wanted if wanted else alias in config.peer_config()

    click.echo(f"toxi agent: waiting for messages from {', '.join(sorted(wanted)) if wanted else 'peers'}"
               f" — Ctrl-C to stop")
    while True:
        try:
            for alias in (sorted(wanted) if wanted else [None]):
                for m in peers.take(alias):  # anything that arrived while we were down
                    handle(m, [])
            for ev in client.subscribe(sorted(wanted) if wanted else None, context):
                d = ev.get("data") or {}
                if ev.get("event") != "message.new" or d.get("direction") != "in":
                    continue
                if not deliverable(d["alias"]):
                    continue
                for m in peers.take(d["alias"]):
                    handle(m, d.get("context", []))
        except client.DaemonNotRunning:
            click.echo("daemon is not running; retrying in 10s", err=True)
            time.sleep(10)
        except (client.ipc.IPCError, client.DaemonError, OSError) as e:
            click.echo(f"connection lost ({e}); reconnecting", err=True)
            time.sleep(2)
        except KeyboardInterrupt:
            return


def _doctor_checks() -> list:
    """Every check as {"ok": True|False|None(info), "check": str, "detail": str}."""
    out = []

    def add(ok, check, detail=""):
        out.append({"ok": ok, "check": check, "detail": detail})

    add(True, "engine", f"toxi {__version__} — {sys.executable}")
    try:
        from .tox import Tox as _Tox  # noqa: F401
        add(True, "libtoxcore", "loadable")
    except Exception as e:  # ToxError or import failure
        add(False, "libtoxcore", f"{e}")
    home = paths.config_dir()
    add(bootstrap.identity_initialized(), "identity", f"{paths.tox_state_path()}"
        + ("" if bootstrap.identity_initialized() else " — run `toxi init` or `toxi setup engine`"))
    if os.environ.get("TOXI_HOME"):
        add(None, "TOXI_HOME", f"{home} — MCP servers and hooks must see the same value")
    for f in (paths.tox_state_path(), paths.db_path(), paths.config_path(), paths.profile_path()):
        if f.exists() and (f.stat().st_mode & 0o077):
            add(False, "permissions", f"{f.name} is group/world readable — the daemon fixes this on start")
    running = bootstrap.daemon_running()
    if not running and bootstrap.daemon_locked():
        add(False, "daemon", "a daemon holds the lock but does not answer — busy or hung; "
            "`toxi service restart`")
    else:
        add(running, "daemon", "running" if running else "not running — `toxi daemon start`, or "
            "`toxi service install` to keep it alive")
    st = service.status()
    if st["installed"]:
        add(st["running"], "service", f"{'running' if st['running'] else 'installed but not running'} — {st['path']}")
    else:
        add(None, "service", "not installed — `toxi service install` survives reboots")
    if running:
        try:
            status = _call("get_status")
            dv = status.get("version")
            if dv != __version__:
                add(False, "daemon version", f"daemon runs {dv or 'an older build'}, CLI is "
                    f"{__version__} — `toxi service restart` (or daemon stop/start)")
            add(bool(status["dht_connected"]), "dht",
                "connected" if status["dht_connected"] else "not connected yet (give it a minute)")
            add(None, "contacts", f"{status['contacts_online']}/{status['contacts_total']} online, "
                f"{status['queue_size']} queued")
            prof = _call("get_profile")["profile"]
            add(bool(prof), "profile", (f"{prof.get('kind')}/{prof.get('runtime')} {prof.get('name')}"
                                        if prof else "none — `toxi profile set --kind agent ...`"))
            known = {c["alias"] for c in _call("list_contacts")["contacts"]}
        except (click.ClickException, KeyError) as e:
            add(False, "daemon rpc", str(e))
            known = set()
    else:
        known = None
    for alias, spec in config.peer_config().items():
        problems = [d for d in spec["dirs"] if not Path(d).expanduser().is_dir()]
        if known is not None and alias not in known:
            problems.append("not a contact")
        add(not problems, f"peer {alias}", f"{spec['trust']}" + (f" — {', '.join(problems)}" if problems else ""))
    plugin = bootstrap.toxi_plugin_cache_root()
    if plugin.exists():
        versions = sorted(p.name for p in plugin.iterdir() if p.is_dir())
        hooks = plugin / (versions[-1] if versions else "") / "hooks" / "hooks.json"
        has_post = hooks.exists() and "PostToolUse" in hooks.read_text()
        add(has_post, "claude plugin", f"{versions[-1] if versions else '?'} installed"
            + ("" if has_post else " — old hooks; rerun install.sh and /reload-plugins"))
    elif Path.home().joinpath(".claude").exists():
        add(None, "claude plugin", "not installed — `install.sh --for claude`")
    if shutil.which("codex"):
        ok, text = _codex_stdout(["mcp", "list"])
        add(ok and "toxi" in text, "codex", "MCP server registered" if ok and "toxi" in text
            else "codex found but toxi MCP not registered — `toxi setup codex`")
    return out


@cli.command()
@click.pass_context
def doctor(ctx):
    """Check this machine's toxi setup end to end and say what to fix."""
    checks = _doctor_checks()
    if _emit(ctx, checks):
        return
    for c in checks:
        mark = "✓" if c["ok"] else ("⚠" if c["ok"] is False else "ℹ")
        click.echo(f"  {mark} {c['check']:14} {c['detail']}")
    bad = [c for c in checks if c["ok"] is False]
    click.echo(f"\n{len(bad)} problem(s)." if bad else "\nAll good.")
    if bad:
        sys.exit(1)


@cli.command()
@click.pass_context
def queue(ctx):
    """Show messages waiting to be delivered."""
    q = _call("list_queue")["queue"]
    if _emit(ctx, q):
        return
    if not q:
        click.echo("Queue is empty.")
        return
    click.echo(f"[{len(q)} queued]")
    for m in q:
        click.echo(f"  {m['alias']}: {m['content']} ({_ago(m['created_at'])})")


@cli.command()
@click.argument("to")
@click.argument("whom")
@click.option("--note", default="", help="A short note about who this is.")
def introduce(to, whom, note):
    """Share a contact's details: toxi introduce <to> <whom>."""
    _call("introduce", {"to_alias": to, "contact_alias": whom, "note": note})
    click.echo(f"✓ Sent {whom}'s contact to {to}.")


@cli.command()
@click.pass_context
def introductions(ctx):
    """Show contacts other people have introduced to you."""
    intros = _call("list_introductions")["introductions"]
    if _emit(ctx, intros):
        return
    if not intros:
        click.echo("No introductions.")
        return
    click.echo(f"[{len(intros)} introduction(s)]")
    for i in intros:
        click.echo(f"  {i['from_alias']} introduced '{i['suggested_alias']}' "
                   f"(Tox ID: {i['introduced_tox_id'][:16]}...)")
        if i["note"]:
            click.echo(f"    note: {i['note']}")
    click.echo("\nAccept with: toxi accept-intro <from> <whom> [--alias=...]")


@cli.command(name="accept-intro")
@click.argument("introducer", metavar="FROM")
@click.argument("whom")
@click.option("--alias", default=None, help="Local alias to give them (default: their suggested name).")
def accept_intro(introducer, whom, alias):
    """Accept an introduction: toxi accept-intro <from> <whom>."""
    res = _call("accept_introduction",
                {"from_alias": introducer, "whom": whom, "alias": alias})
    click.echo(f"Sending a friend request to '{res['alias']}'. They'll need to accept it.")


@cli.group()
def daemon():
    """Manage the background daemon."""


def _spawn_daemon(timeout: float = 10.0) -> int:
    """Spawn the daemon detached and poll until it answers. Returns the PID."""
    paths.ensure_config_dir()
    deadline = time.time() + 5  # a daemon that was just told to stop may still be exiting
    while bootstrap.daemon_locked() and time.time() < deadline:
        time.sleep(0.1)
    if bootstrap.daemon_locked():
        raise click.ClickException(
            "a daemon for this identity is running but does not answer (busy?). "
            "Not starting a second one. Wait, or run `toxi service restart`.")
    proc = subprocess.Popen(
        [sys.executable, "-m", "toxi.daemon"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )
    deadline = time.time() + timeout
    while time.time() < deadline:
        if bootstrap.daemon_running():
            return proc.pid
        time.sleep(0.05)
    raise click.ClickException(f"daemon failed to start; check {paths.log_path()}")


@daemon.command("start")
def daemon_start():
    """Start the background daemon."""
    if bootstrap.daemon_running():
        click.echo("daemon already running")
        return
    pid = _spawn_daemon()
    click.echo(f"daemon started (pid {pid})")


@daemon.command("stop")
def daemon_stop():
    if service.installed():
        click.echo("⚠ a background service manages this daemon and will restart it; "
                   "use `toxi service restart` or `toxi service uninstall`.")
    """Stop the background daemon."""
    try:
        client.request("shutdown")
        click.echo("daemon stopped")
    except client.DaemonNotRunning:
        click.echo("daemon is not running")


@cli.group()
def mcp():
    """MCP server for Claude Code and other MCP clients."""


@mcp.command("serve")
def mcp_serve():
    """Run the MCP server over stdio (used by Claude Code's .mcp.json)."""
    from . import mcp_server
    try:
        mcp_server.serve()
    except ImportError:
        raise click.ClickException(
            "MCP support needs the extra: pipx install 'toxi[mcp]'"
        )


def _ensure_mcp_extra() -> str:
    """If the `mcp` package isn't in our venv, pipx-inject it.

    Without this, `toxi mcp serve` crashes on import, Claude Code's MCP
    spawn fails silently, and the natural-language tool path is dead.

    Returns: "present" | "installed" | "failed".
    """
    try:
        import mcp  # noqa: F401
        return "present"
    except ImportError:
        pass
    try:
        r = subprocess.run(["pipx", "inject", "toxi", "mcp"],
                           capture_output=True, text=True)
    except FileNotFoundError:
        return "failed"
    if r.returncode != 0:
        click.echo(r.stderr.rstrip(), err=True)
        return "failed"
    return "installed"


def _run_codex(args: list[str]) -> subprocess.CompletedProcess:
    return subprocess.run(["codex", *args], capture_output=True, text=True)


def _completed_output(result: subprocess.CompletedProcess) -> str:
    return (result.stderr or result.stdout or "").rstrip()


def _codex_stdout(args: list[str]) -> tuple[bool, str]:
    try:
        result = _run_codex(args)
    except FileNotFoundError:
        return False, "Codex CLI not found"
    return result.returncode == 0, result.stdout.rstrip()


def _mcp_extra_present() -> bool:
    try:
        import mcp  # noqa: F401
        return True
    except ImportError:
        return False


def _line_starts_with(text: str, prefix: str) -> bool:
    return any(line.split(None, 1)[0] == prefix for line in text.splitlines() if line.split())


def _plugin_installed(text: str, selector: str) -> bool:
    for line in text.splitlines():
        parts = line.split()
        if parts and parts[0] == selector and "not installed" not in line:
            return True
    return False


def _codex_step(args: list[str], ok: str, warning: str, manual: str, kept: str) -> None:
    try:
        result = _run_codex(args)
    except FileNotFoundError:
        click.echo(f"⚠ Codex CLI not found. Run manually after installing Codex:\n    {manual}")
        return
    if result.returncode == 0:
        click.echo(ok)
        return
    details = _completed_output(result)
    lowered = details.lower()
    if "already" in lowered or "exists" in lowered or "installed" in lowered:
        click.echo(kept)
        return
    click.echo(f"⚠ {warning}")
    if details:
        click.echo(f"  {details}")
    click.echo(f"  Run manually:\n    {manual}")


def _codex_remove_step(args: list[str], ok: str, warning: str, manual: str, absent: str) -> None:
    try:
        result = _run_codex(args)
    except FileNotFoundError:
        click.echo(f"⚠ Codex CLI not found. Run manually after installing Codex:\n    {manual}")
        return
    if result.returncode == 0:
        click.echo(ok)
        return
    details = _completed_output(result)
    lowered = details.lower()
    if "not found" in lowered or "no " in lowered or "not installed" in lowered:
        click.echo(absent)
        return
    click.echo(f"⚠ {warning}")
    if details:
        click.echo(f"  {details}")
    click.echo(f"  Run manually:\n    {manual}")


@cli.command(name="setup-codex", hidden=True)
def setup_codex():
    """Wire toxi into Codex: MCP server plus the local Codex plugin."""
    codex_root = bootstrap.codex_repo_root()
    marketplace = bootstrap.codex_marketplace_path()
    mcp_status = _ensure_mcp_extra()
    if mcp_status == "present":
        click.echo("✓ MCP extra already installed.")
    elif mcp_status == "installed":
        click.echo("✓ MCP extra installed via `pipx inject toxi mcp`.")
    else:
        click.echo(
            "⚠ Could not install the MCP extra. Run manually:\n"
            "    pipx inject toxi mcp\n"
            "  (without it, Codex's MCP tools won't start.)"
        )

    prompt = bootstrap.ensure_codex_prompt()
    click.echo(f"✓ Codex slash command /prompts:{bootstrap.CODEX_PROMPT_NAME} "
               + {"added": "installed.", "updated": "updated.", "kept": "already installed."}[prompt])

    if shutil.which("codex") is None:
        click.echo("\n⚠ Codex CLI not found on PATH. After installing Codex, run:")
        click.echo("  codex mcp add toxi -- toxi mcp serve")
        if marketplace.exists():
            click.echo(f"  codex plugin marketplace add {codex_root}")
            click.echo("  codex plugin add toxi@toxi")
        else:
            click.echo(
                f"\n⚠ Codex plugin files were not found at {marketplace}.\n"
                "  Plugin install currently requires running from a source checkout:\n"
                "    git clone https://github.com/JefferyLee/toxi\n"
                "    cd toxi\n"
                "    toxi setup-codex"
            )
        return

    _codex_step(
        ["mcp", "add", "toxi", "--", "toxi", "mcp", "serve"],
        "✓ Registered Codex MCP server `toxi`.",
        "Could not register Codex MCP server `toxi`.",
        "codex mcp add toxi -- toxi mcp serve",
        "✓ Codex MCP server `toxi` already registered.",
    )

    if marketplace.exists():
        _codex_step(
            ["plugin", "marketplace", "add", str(codex_root)],
            "✓ Registered this checkout as a Codex plugin marketplace.",
            "Could not register this checkout as a Codex plugin marketplace.",
            f"codex plugin marketplace add {codex_root}",
            "✓ Codex plugin marketplace already registered.",
        )
        _codex_step(
            ["plugin", "add", "toxi@toxi"],
            "✓ Installed Codex plugin `toxi`.",
            "Could not install Codex plugin `toxi`.",
            "codex plugin add toxi@toxi",
            "✓ Codex plugin `toxi` already installed.",
        )
    else:
        click.echo(
            f"⚠ Codex marketplace file not found at {marketplace}.\n"
            "  Codex MCP was registered, but plugin install currently requires a source checkout:\n"
            "    git clone https://github.com/JefferyLee/toxi\n"
            "    cd toxi\n"
            "    toxi setup-codex"
        )

    click.echo("\nNext:")
    click.echo("  toxi doctor-codex  # verify Codex MCP/plugin wiring without changing config")
    click.echo("\nThen in Codex:")
    click.echo("  /mcp      # confirm the toxi MCP server is enabled")
    click.echo("  /hooks    # review and trust the toxi hooks if prompted")
    click.echo("  /plugins  # confirm the toxi plugin is installed and enabled")
    click.echo(f"  /prompts:{bootstrap.CODEX_PROMPT_NAME}  # read unread toxi messages")


@cli.command(name="doctor-codex", hidden=True)
def doctor_codex():
    """Check the Codex MCP/plugin wiring without changing Codex config."""
    failures: list[str] = []
    codex_root = bootstrap.codex_repo_root()
    marketplace = bootstrap.codex_marketplace_path()
    if marketplace.exists():
        click.echo(f"✓ Local Codex marketplace file found: {marketplace}")
    else:
        failures.append(f"Local Codex marketplace file missing: {marketplace}")
        click.echo(f"⚠ Local Codex marketplace file missing: {marketplace}")

    if _mcp_extra_present():
        click.echo("✓ MCP extra importable by this toxi environment.")
    else:
        failures.append("MCP extra is not importable")
        click.echo("⚠ MCP extra is not importable. Run: pipx inject toxi mcp")

    if shutil.which("codex") is None:
        failures.append("Codex CLI not found")
        click.echo("⚠ Codex CLI not found on PATH.")
    else:
        click.echo("✓ Codex CLI found on PATH.")

        ok, out = _codex_stdout(["mcp", "list"])
        if ok and _line_starts_with(out, "toxi"):
            click.echo("✓ Codex MCP server `toxi` is registered.")
        else:
            failures.append("Codex MCP server `toxi` is not registered")
            click.echo("⚠ Codex MCP server `toxi` is not registered. Run: codex mcp add toxi -- toxi mcp serve")

        ok, out = _codex_stdout(["plugin", "marketplace", "list"])
        if ok and _line_starts_with(out, "toxi"):
            click.echo("✓ Codex plugin marketplace `toxi` is registered.")
        else:
            failures.append("Codex plugin marketplace `toxi` is not registered")
            click.echo(f"⚠ Codex plugin marketplace `toxi` is not registered. Run: codex plugin marketplace add {codex_root}")

        ok, out = _codex_stdout(["plugin", "list"])
        if ok and _plugin_installed(out, "toxi@toxi"):
            click.echo("✓ Codex plugin `toxi` is installed.")
        else:
            failures.append("Codex plugin `toxi` is not installed")
            click.echo("⚠ Codex plugin `toxi` is not installed. Run: codex plugin add toxi@toxi")

    if failures:
        raise click.ClickException(
            "Codex integration incomplete. Run `toxi setup-codex`, then `toxi doctor-codex` again."
        )
    click.echo("\nCodex integration looks ready. In Codex, `/mcp`, `/hooks`, and `/plugins` should show toxi.")


@cli.command(name="teardown-codex")
def teardown_codex():
    """Remove toxi from Codex without touching your identity or daemon."""
    if bootstrap.remove_codex_prompt() == "removed":
        click.echo(f"✓ Removed Codex slash command /prompts:{bootstrap.CODEX_PROMPT_NAME}.")
    if shutil.which("codex") is None:
        click.echo(
            "⚠ Codex CLI not found on PATH. If Codex is installed elsewhere, run:\n"
            "  codex plugin remove toxi@toxi\n"
            "  codex mcp remove toxi\n"
            "  codex plugin marketplace remove toxi"
        )
        return

    _codex_remove_step(
        ["plugin", "remove", "toxi@toxi"],
        "✓ Removed Codex plugin `toxi`.",
        "Could not remove Codex plugin `toxi`.",
        "codex plugin remove toxi@toxi",
        "✓ Codex plugin `toxi` already absent.",
    )
    _codex_remove_step(
        ["mcp", "remove", "toxi"],
        "✓ Removed Codex MCP server `toxi`.",
        "Could not remove Codex MCP server `toxi`.",
        "codex mcp remove toxi",
        "✓ Codex MCP server `toxi` already absent.",
    )
    _codex_remove_step(
        ["plugin", "marketplace", "remove", "toxi"],
        "✓ Removed Codex plugin marketplace `toxi`.",
        "Could not remove Codex plugin marketplace `toxi`.",
        "codex plugin marketplace remove toxi",
        "✓ Codex plugin marketplace `toxi` already absent.",
    )

    click.echo(f"\n(Identity + history preserved in {paths.config_dir()}.)")


def _setup_engine() -> None:
    """Initialize identity and start the daemon."""
    if bootstrap.identity_initialized():
        click.echo("✓ Identity already initialized.")
    else:
        paths.ensure_config_dir()
        t = Tox()
        addr = t.self_get_address_hex()
        paths.tox_state_path().write_bytes(t.get_savedata())
        t.kill()
        click.echo(f"✓ Generated identity (Tox ID: {addr[:16]}…). Run `toxi me` for the full ID.")

    if bootstrap.daemon_running():
        click.echo("✓ Daemon already running.")
    else:
        pid = _spawn_daemon()
        click.echo(f"✓ Daemon started (PID {pid}).")


def _setup_claude() -> None:
    """Wire Claude Code statusLine and print plugin install guidance."""
    mcp_status = _ensure_mcp_extra()
    if mcp_status == "present":
        click.echo("✓ MCP extra already installed.")
    elif mcp_status == "installed":
        click.echo("✓ MCP extra installed via `pipx inject toxi mcp`.")
    else:
        click.echo(
            "⚠ Could not install the MCP extra. Run manually:\n"
            "    pipx inject toxi mcp\n"
            "  (without it, Claude Code's natural-language tool calls won't work.)"
        )

    sp = bootstrap.claude_settings_path()
    result = bootstrap.ensure_statusline()
    if result == "added":
        click.echo(f"✓ Wired statusLine into {sp}.")
    elif result == "kept":
        click.echo(f"✓ statusLine already wired in {sp}.")
    elif result == "wrapped":
        click.echo(f"✓ Wrapped your existing statusLine in {sp} — toxi runs it and appends its own segment.")
    else:  # left-custom
        click.echo(
            f"⚠ {sp} has a non-command statusLine toxi can't run — not touching it.\n"
            f'  To enable toxi in the status bar, merge in: '
            f'{{"statusLine":{{"type":"command","command":"toxi statusline"}}}}'
        )

    # 5. Plugin install hint (Claude Code's plugin registry isn't safe to write from outside)
    click.echo("\nNext — install the Claude Code plugin (run inside Claude Code):")
    click.echo("  /plugin marketplace add JefferyLee/toxi")
    click.echo("  /plugin install toxi")


@cli.command(name="setup-engine", hidden=True)
def setup_engine():
    """Alias of `toxi setup engine`."""
    _setup_engine()


@cli.command(name="setup-claude", hidden=True)
def setup_claude():
    """Alias of `toxi setup claude`."""
    _setup_claude()


@cli.group(invoke_without_command=True)
@click.pass_context
def setup(ctx):
    """Wire toxi into this machine: `toxi setup engine|claude|codex|headless`
    (no target = engine + claude, the original one-shot)."""
    if ctx.invoked_subcommand is None:
        _setup_engine()
        _setup_claude()


@setup.command("engine")
def setup_engine_cmd():
    """Initialize your identity and start the daemon."""
    _setup_engine()


@setup.command("claude")
def setup_claude_cmd():
    """Wire Claude Code (statusLine + plugin hints)."""
    _setup_claude()


@setup.command("codex")
@click.pass_context
def setup_codex_cmd(ctx):
    """Register the MCP server and plugin with Codex."""
    ctx.invoke(setup_codex)


@setup.command("headless")
@click.option("--name", help="Name on this identity's card (default: hostname).")
@click.option("--kind", type=click.Choice(list(envelope.PROFILE_KINDS)), default="agent", show_default=True)
@click.option("--runtime", default="other", show_default=True, help="e.g. grok, script, cron.")
@click.option("--operator", help="Who runs this identity.")
@click.option("--role", help="One line on what it does.")
@click.option("--no-service", is_flag=True, help="Do not install a launchd/systemd service.")
def setup_headless(name, kind, runtime, operator, role, no_service):
    """A bot or script with no Claude/Codex: identity, service, card, pairing hint."""
    _setup_engine()
    fields = {"kind": kind, "runtime": runtime, "name": name or socket.gethostname(),
              "host": socket.gethostname()}
    if operator:
        fields["operator"] = operator
    if role:
        fields["role"] = role
    _call("set_profile", {"profile": fields})
    click.echo(f"✓ profile card: {kind}/{runtime} {fields['name']}")
    if not no_service:
        try:
            service.install()
            click.echo("✓ service installed (survives reboots)")
        except service.ServiceError as e:
            click.echo(f"ℹ {e}\n  keep-alive instead: add to crontab:  */10 * * * * "
                       f"{shutil.which('toxi') or 'toxi'} daemon start >/dev/null 2>&1")
    me = _call("get_me")
    click.echo(f"\nTox ID: {me['tox_id']}\n"
               "Pair with a peer:  toxi pair   (or join theirs: toxi join <invite>)\n"
               "Run your bot:      toxi agent run --exec 'python3 my_bot.py'\n"
               "Or be woken by HTTP: [hooks] on_message_url = \"https://...\" in config.toml")


@cli.command()
@click.option("--purge", is_flag=True,
              help="Also delete your identity and chat history (DESTRUCTIVE).")
def teardown(purge):
    """Reverse of setup: stop daemon, unwire statusLine, optionally wipe identity."""
    # 1. Daemon
    if bootstrap.daemon_running():
        try:
            client.request("shutdown")
            click.echo("✓ Daemon stopped.")
        except client.DaemonNotRunning:
            click.echo("✓ Daemon stopped.")
    else:
        click.echo("✓ Daemon already stopped.")

    # 2. statusLine
    sp = bootstrap.claude_settings_path()
    result = bootstrap.remove_statusline()
    if result == "removed":
        click.echo(f"✓ Removed statusLine entry from {sp}.")
    elif result == "restored":
        click.echo(f"✓ Restored your original statusLine in {sp}.")
    elif result == "absent":
        click.echo("✓ statusLine entry already absent.")
    else:  # left-custom
        click.echo(f"⚠ statusLine in {sp} isn't ours — leaving it alone.")

    # 3. Optional purge
    if purge:
        cd = paths.config_dir()
        if cd.exists():
            shutil.rmtree(cd)
            click.echo(f"✓ Removed config dir ({cd}). Your identity is gone.")
        else:
            click.echo(f"✓ Config dir already absent ({cd}).")
    else:
        click.echo(f"\n(Identity + history preserved in {paths.config_dir()}. Pass --purge to wipe.)")

    # 4. Next steps
    click.echo("\nFinish removal:")
    click.echo("  In Claude Code:  /plugin uninstall toxi@toxi")
    click.echo("  In terminal:     pipx uninstall toxi")


def _pipx_lifecycle(pipx_args: list[str], label: str) -> None:
    """Shared body for `upgrade` and `reinstall`: stop daemon → run pipx → restart."""
    was_running = bootstrap.daemon_running()
    if was_running:
        try:
            client.request("shutdown")
            click.echo("→ Stopped daemon.")
        except client.DaemonNotRunning:
            pass

    click.echo(f"→ Running `pipx {' '.join(pipx_args)}`...")
    try:
        r = subprocess.run(["pipx", *pipx_args], capture_output=True, text=True)
    except FileNotFoundError:
        raise click.ClickException("pipx not found on PATH.")
    click.echo(r.stdout.rstrip() or "(no output)")
    if r.returncode != 0:
        click.echo(r.stderr.rstrip(), err=True)
        raise click.ClickException(f"pipx {label} failed.")

    if was_running:
        pid = _spawn_daemon()
        click.echo(f"✓ Daemon restarted (PID {pid}).")

    click.echo("\nIf the plugin shipped changes too, in Claude Code:")
    click.echo("  /plugin uninstall toxi@toxi")
    click.echo("  /plugin install toxi")


@cli.command()
def upgrade():
    """Upgrade the engine via pipx — only fires when pyproject.toml version bumped."""
    _pipx_lifecycle(["upgrade", "toxi"], "upgrade")


@cli.command()
def reinstall():
    """Force-reinstall the engine via pipx — ignores version, always re-fetches."""
    _pipx_lifecycle(["reinstall", "toxi"], "reinstall")


def _git(cwd, *args) -> subprocess.CompletedProcess:
    return subprocess.run(["git", "-C", str(cwd), *args], capture_output=True, text=True)


@cli.command(name="reinstall-plugin")
def reinstall_plugin():
    """Refresh the Claude Code plugin from GitHub HEAD (then /reload-plugins in Claude Code)."""
    marketplace = bootstrap.toxi_marketplace_clone()
    if not marketplace.exists():
        raise click.ClickException(
            f"Marketplace clone not found at {marketplace}.\n"
            "Run `/plugin marketplace add JefferyLee/toxi` in Claude Code first."
        )

    click.echo(f"→ Fetching latest plugin from GitHub into {marketplace}...")
    r = _git(marketplace, "fetch", "origin")
    if r.returncode != 0:
        raise click.ClickException(f"git fetch failed:\n{r.stderr.rstrip()}")
    r = _git(marketplace, "reset", "--hard", "origin/HEAD")
    if r.returncode != 0:
        raise click.ClickException(f"git reset failed:\n{r.stderr.rstrip()}")

    plugin_src = marketplace / "claude-code-plugin"
    new_meta = json.loads((plugin_src / ".claude-plugin" / "plugin.json").read_text())
    new_version = new_meta["version"]
    new_sha = _git(marketplace, "rev-parse", "HEAD").stdout.strip()

    installed_p = bootstrap.installed_plugins_path()
    if not installed_p.exists():
        raise click.ClickException(
            f"{installed_p} not found. Run `/plugin install toxi@toxi` in Claude Code first."
        )
    installed = json.loads(installed_p.read_text())
    key = f"{bootstrap.PLUGIN_NAME}@{bootstrap.MARKETPLACE_NAME}"
    entries = installed.get("plugins", {}).get(key, [])
    if not entries:
        raise click.ClickException(
            f"Plugin '{key}' not installed. Run `/plugin install {key}` in Claude Code first."
        )

    old_entry = entries[0]
    old_version, old_sha = old_entry["version"], old_entry["gitCommitSha"]
    if old_sha == new_sha:
        click.echo(f"✓ Plugin already at latest commit ({new_sha[:7]}). Nothing to do.")
        return

    # Place fresh files in the (possibly new) versioned cache dir.
    new_cache_dir = bootstrap.toxi_plugin_cache_root() / new_version
    if new_cache_dir.exists():
        shutil.rmtree(new_cache_dir)
    shutil.copytree(plugin_src, new_cache_dir)
    click.echo(f"✓ Wrote fresh plugin files to {new_cache_dir}")

    # Update the installed-plugins registry.
    old_entry["version"] = new_version
    old_entry["gitCommitSha"] = new_sha
    old_entry["installPath"] = str(new_cache_dir)
    old_entry["lastUpdated"] = time.strftime("%Y-%m-%dT%H:%M:%S.000Z", time.gmtime())
    installed_p.write_text(json.dumps(installed, indent=2))
    # The old version dir stays: running sessions call their hooks from it
    # until /reload-plugins, and a missing hook script fails every turn.

    click.echo(f"\n✓ Plugin: {old_version} ({old_sha[:7]}) → {new_version} ({new_sha[:7]})")
    click.echo("\nIn Claude Code, run:")
    click.echo("  /reload-plugins")


if __name__ == "__main__":
    cli()
