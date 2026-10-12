"""The `toxi` CLI (PRD §4.7). Short-lived: each command talks to the daemon over
IPC and exits. Only `init` and `daemon start` touch the filesystem/process
directly; everything else goes through the daemon.
"""
import json
import shutil
import subprocess
import sys
import time

import click

from . import __version__, bootstrap, client, paths, peers
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
    """Render a message body for the terminal; media rows show type + saved path."""
    if m.get("msg_type", "text") == "text":
        return m["content"]
    label = _MEDIA_LABEL.get(m["msg_type"], "📎 file")
    return f"[{label}] {m['content']}"


@cli.command()
@click.argument("alias")
@click.argument("message")
@click.pass_context
def send(ctx, alias, message):
    """Send a message: toxi send <alias> <message> (use - to read from stdin)."""
    if message == "-":
        message = sys.stdin.read().rstrip("\n")
    res = _call("send_message", {"alias": alias, "body": message})
    if _emit(ctx, res):
        return
    if res["status"] == "sent":
        click.echo("✓ sent")
    else:
        click.echo(f"✓ {alias} is offline — queued (will send when they come online)")


_RESET = "\033[0m"
_BOLD_RED = "\033[1;31m"
_YELLOW = "\033[33m"
_DIM = "\033[2m"


def _toxi_segment() -> str:
    """The toxi part of the status line (colored, width-aware). Never raises."""
    try:
        st = client.request("get_status")
        msgs = client.request("get_messages", {"unread_only": True, "limit": 100})["messages"]
    except client.DaemonNotRunning:
        return f"{_YELLOW}toxi: offline{_RESET}"
    except Exception:  # a status line must never crash the prompt with a traceback
        return f"{_YELLOW}toxi: error{_RESET}"

    name = st.get("name", "").strip()
    prefix = f"toxi({name})" if name else "toxi"
    online = f"{st['contacts_online']}/{st['contacts_total']} online"
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
@click.pass_context
def unread(ctx, alias):
    """Show unread messages (optionally just from one contact)."""
    msgs = _call("get_messages", {"alias": alias, "unread_only": True, "limit": 100})["messages"]
    # --json is a programmatic peek (e.g. the Claude Code hook); it must not mark read.
    if _emit(ctx, msgs):
        return
    if not msgs:
        click.echo("No unread messages.")
        return
    click.echo(f"[{len(msgs)} unread]")
    for m in reversed(msgs):  # oldest first
        click.echo(f"  {m['alias']} ({_ago(m['created_at'])}): {_content(m)}")
    _call("mark_read", {"msg_uuids": [m["msg_uuid"] for m in msgs]})


@cli.command()
@click.argument("alias")
@click.option("--limit", default=20, help="How many recent messages to show.")
@click.pass_context
def read(ctx, alias, limit):
    """Show conversation history with a contact."""
    msgs = _call("get_messages", {"alias": alias, "limit": limit})["messages"]
    if _emit(ctx, msgs):  # programmatic peek: don't mark read
        return
    if not msgs:
        click.echo("No messages yet.")
        return
    for m in reversed(msgs):  # oldest first
        who = "you" if m["direction"] == "out" else m["alias"]
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


@cli.command(name="setup-codex")
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


@cli.command(name="doctor-codex")
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


@cli.command(name="setup-engine")
def setup_engine():
    """Initialize your toxi identity and start the daemon."""
    _setup_engine()


@cli.command(name="setup-claude")
def setup_claude():
    """Wire toxi into Claude Code without touching identity or daemon state."""
    _setup_claude()


@cli.command()
def setup():
    """One-shot setup: init identity, start daemon, wire Claude Code statusLine."""
    _setup_engine()
    _setup_claude()


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

    # Clean up the now-orphaned old version dir (if version actually bumped).
    if old_version != new_version:
        old_cache = bootstrap.toxi_plugin_cache_root() / old_version
        if old_cache.exists():
            shutil.rmtree(old_cache)
            click.echo(f"✓ Removed orphaned cache at {old_cache}")

    click.echo(f"\n✓ Plugin: {old_version} ({old_sha[:7]}) → {new_version} ({new_sha[:7]})")
    click.echo("\nIn Claude Code, run:")
    click.echo("  /reload-plugins")


if __name__ == "__main__":
    cli()
