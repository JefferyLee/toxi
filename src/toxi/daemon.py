"""The resident daemon (PRD §3.2): one Tox instance + one IPC server.

Single-threaded by design. The toxcore handle is not thread-safe, so the event
loop both drives `tox_iterate` and serves IPC requests from one thread: we wait
on `accept()` with a timeout derived from `tox_iteration_interval`, so we never
block the protocol for long. This sidesteps all locking.
"""
import json
import logging
import os
import secrets
import socket
import subprocess
import time
import uuid as _uuid
from collections import deque
from logging.handlers import RotatingFileHandler
from pathlib import Path

from . import client, config, db, envelope, invite, ipc, paths
from .tox import (
    FILE_CONTROL_CANCEL,
    FILE_CONTROL_RESUME,
    FILE_KIND_DATA,
    MAX_MESSAGE_LENGTH,
    NO_FRIEND,
    Tox,
)

SWEEP_INTERVAL = 30  # seconds between ACK-timeout retry sweeps
MAX_FILE_BYTES = 100 * 1024 * 1024  # cap on an accepted incoming file (100 MB)
HOOK_DEBOUNCE = 2.0  # quiet seconds before [hooks] on_message fires; coalesces bursts
HOOK_TIMEOUT = 30  # seconds before a still-running on_message hook is killed
TEXT_RATE_LIMIT = 60  # incoming texts accepted per contact per minute (PRD §4.9.4)
REQUEST_RATE_LIMIT = 5  # incoming friend requests accepted per minute
INVITE_TTL = 600  # seconds a `toxi pair` invite stays valid

# Map a filename extension to one of our media msg_types (PRD: image/voice/video).
_IMAGE_EXTS = {"jpg", "jpeg", "png", "gif", "webp", "bmp", "heic", "heif", "tiff", "tif", "svg"}
_VOICE_EXTS = {"ogg", "oga", "opus", "mp3", "m4a", "aac", "wav", "flac", "amr", "wma"}
_VIDEO_EXTS = {"mp4", "mov", "webm", "mkv", "avi", "m4v", "3gp", "flv", "wmv", "mpg", "mpeg"}


def _classify_media(filename: str) -> str:
    ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
    if ext in _IMAGE_EXTS:
        return "image"
    if ext in _VOICE_EXTS:
        return "voice"
    if ext in _VIDEO_EXTS:
        return "video"
    return "file"


def _safe_filename(raw: bytes) -> str:
    """Strip any path components and control chars from a sender-supplied name."""
    name = os.path.basename(raw.decode("utf-8", "replace"))
    name = name.replace("/", "_").replace("\\", "_").replace("\x00", "")
    name = "".join(c for c in name if c.isprintable()).strip()
    return name[:120] or "file"


def _is_hex(s: str) -> bool:
    try:
        bytes.fromhex(s)
        return True
    except ValueError:
        return False


def _within_rate(times: deque, limit: int, now: float, window: float = 60.0) -> bool:
    """Sliding-window counter: record `now` and return True if under `limit`."""
    while times and now - times[0] > window:
        times.popleft()
    if len(times) >= limit:
        return False
    times.append(now)
    return True


def _harden_permissions() -> None:
    """Keys, history and config are private: dirs 0700, files 0600 (PRD §4.9)."""
    for d in (paths.config_dir(), paths.media_dir()):
        if d.is_dir():
            os.chmod(d, 0o700)
    dbp = paths.db_path()
    for f in (paths.tox_state_path(), dbp, Path(f"{dbp}-wal"), Path(f"{dbp}-shm"),
              paths.config_path(), paths.profile_path()):
        if f.exists():
            os.chmod(f, 0o600)


def _with_profile(row) -> dict:
    """Row -> dict, with the contact's profile_json column parsed into `profile`."""
    d = dict(row)
    pj = d.pop("profile_json", None)
    if "profile" not in d:
        try:
            d["profile"] = json.loads(pj) if pj else None
        except ValueError:
            d["profile"] = None
    return d

log = logging.getLogger("toxi.daemon")

# A few long-lived public DHT nodes (PRD §4.8 will later move these to bootstrap.json).
DEFAULT_BOOTSTRAP = [
    ("144.217.167.73", 33445, "7E5668E0EE09E19F320AD47902419331FFEE147BB3606769CFBE921A2A2FD34C"),
    ("139.162.110.188", 33445, "F76A11284547163889DDC89A7738CF271797BF5E5E220643E97AD3C7E7903D55"),
    ("205.185.115.131", 53, "3091C6BEB2A993F1C6300C16549FABA67098FF3D62C6D253828B531470B53D68"),
]


class RpcError(Exception):
    def __init__(self, code: str, message: str):
        self.code = code
        super().__init__(message)


class Daemon:
    def __init__(self):
        paths.ensure_config_dir()
        _setup_logging()
        self.db = db.connect(paths.db_path())
        self.profile = self._load_profile()
        self._text_times: dict[int, deque] = {}  # per-friend incoming text timestamps
        self._request_times: deque = deque()  # incoming friend-request timestamps
        rc = config.retry_config()
        self.ack_timeout = rc["ack_timeout_minutes"] * 60
        self.fail_after = rc["fail_after_hours"] * 3600
        self._last_sweep = 0.0
        self._started_at = time.time()
        self._transfers: dict[tuple[int, int], dict] = {}  # in-flight incoming files
        self._hook_msgs: list[dict] = []  # new messages awaiting the on_message hook
        self._hook_due = 0.0
        self._hook_proc: subprocess.Popen | None = None
        self._hook_started = 0.0
        self.tox = self._init_tox()
        self._remap_friend_numbers()
        self._save_state()  # ensure tox_state.bin exists from first run
        _harden_permissions()
        self._bootstrap()
        self.sock: socket.socket | None = None
        self.running = False
        self._handlers = {
            "get_me": self._get_me,
            "set_name": self._set_name,
            "get_status": self._get_status,
            "add_contact": self._add_contact,
            "remove_contact": self._remove_contact,
            "accept_request": self._accept_request,
            "list_contacts": self._list_contacts,
            "list_requests": self._list_requests,
            "send_message": self._send_message,
            "get_messages": self._get_messages,
            "get_message": self._get_message,
            "mark_read": self._mark_read,
            "list_queue": self._list_queue,
            "introduce": self._introduce,
            "list_introductions": self._list_introductions,
            "accept_introduction": self._accept_introduction,
            "get_profile": self._get_profile,
            "set_profile": self._set_profile,
            "create_invite": self._create_invite,
            "shutdown": self._shutdown,
        }

    # --- setup ---
    def _init_tox(self) -> Tox:
        sd_path = paths.tox_state_path()
        savedata = sd_path.read_bytes() if sd_path.exists() else None
        tox = Tox(savedata=savedata)
        tox.on_self_connection_status(
            lambda status: log.info("DHT connection status -> %s", status)
        )
        tox.on_friend_request(self._on_friend_request)
        tox.on_friend_connection_status(self._on_friend_connection_status)
        tox.on_friend_message(self._on_friend_message)
        tox.on_file_recv(self._on_file_recv)
        tox.on_file_recv_chunk(self._on_file_recv_chunk)
        tox.on_file_recv_control(self._on_file_recv_control)
        log.info("Tox ID: %s", tox.self_get_address_hex())
        return tox

    def _remap_friend_numbers(self) -> None:
        """toxcore friend numbers can be reassigned across restarts; rebind them
        to our stable public keys and reset online state (callbacks refresh it)."""
        for row in self.db.execute("SELECT public_key FROM contacts").fetchall():
            fn = self.tox.friend_by_public_key(bytes.fromhex(row["public_key"]))
            if fn != NO_FRIEND:
                self.db.execute(
                    "UPDATE contacts SET friend_number=?, is_online=0 WHERE public_key=?",
                    (fn, row["public_key"]),
                )
        self.db.commit()

    # --- tox callbacks (fire during tox.iterate() on this thread) ---
    def _on_friend_request(self, public_key: bytes, message: bytes) -> None:
        pk = public_key.hex().upper()
        now = int(time.time())
        if not _within_rate(self._request_times, REQUEST_RATE_LIMIT, now):
            log.warning("rate limit: ignored a friend request from %s", pk[:8])
            return
        reply = invite.parse_request(message)
        if reply:
            nonce, name = reply
            valid = self.db.execute(
                "SELECT 1 FROM invites WHERE nonce=? AND used_at IS NULL AND expires_at>=?",
                (nonce, now),
            ).fetchone()
            if valid and self._accept_invited(pk, name, nonce):
                return
        self.db.execute(
            "INSERT OR IGNORE INTO friend_requests (public_key, message, received_at, status) "
            "VALUES (?, ?, ?, 'pending')",
            (pk, message.decode("utf-8", "replace"), int(time.time())),
        )
        self.db.commit()
        log.info("friend request from %s", pk[:8])

    def _on_friend_connection_status(self, friend_number: int, status: int) -> None:
        online = status != 0
        self.db.execute(
            "UPDATE contacts SET is_online=?, last_seen=? WHERE friend_number=?",
            (1 if online else 0, int(time.time()), friend_number),
        )
        self.db.commit()
        if online:
            row = self.db.execute(
                "SELECT id FROM contacts WHERE friend_number=?", (friend_number,)
            ).fetchone()
            if row is not None:
                self._send_profile(friend_number)
                self._flush_queue(row["id"], friend_number)

    def _flush_queue(self, contact_id: int, friend_number: int) -> None:
        """Resend this friend's queued messages in send order (PRD §4.3.4).
        Stop at the first failure so ordering is preserved for the next attempt."""
        rows = self.db.execute(
            "SELECT msg_uuid, content, created_at, from_session FROM messages "
            "WHERE contact_id=? AND direction='out' AND status='queued' "
            "ORDER BY created_at ASC, id ASC",
            (contact_id,),
        ).fetchall()
        for row in rows:
            payload = envelope.encode(envelope.text_envelope(
                row["msg_uuid"], row["created_at"], row["content"], row["from_session"]))
            if not self.tox.friend_send_message(friend_number, payload):
                break
            self.db.execute(
                "UPDATE messages SET status='sent', last_attempt_at=? WHERE msg_uuid=?",
                (int(time.time()), row["msg_uuid"]),
            )
        self.db.commit()
        if rows:
            log.info("flushed up to %d queued message(s) to friend %d", len(rows), friend_number)

    def _retry_sweep(self) -> None:
        """Resend 'sent' messages still unacked past ack_timeout (if the friend is
        online), and give up on ones older than fail_after (PRD §4.4.1, simplified)."""
        now = int(time.time())
        rows = self.db.execute(
            "SELECT m.msg_uuid, m.content, m.created_at, m.last_attempt_at, m.from_session, "
            "c.friend_number, c.is_online FROM messages m "
            "JOIN contacts c ON c.id=m.contact_id "
            "WHERE m.direction='out' AND m.status='sent' AND m.delivered_at IS NULL"
        ).fetchall()
        for r in rows:
            if now - r["created_at"] > self.fail_after:
                self.db.execute(
                    "UPDATE messages SET status='failed' WHERE msg_uuid=?", (r["msg_uuid"],)
                )
            elif r["is_online"] and now - (r["last_attempt_at"] or r["created_at"]) > self.ack_timeout:
                payload = envelope.encode(envelope.text_envelope(
                    r["msg_uuid"], r["created_at"], r["content"], r["from_session"]))
                if self.tox.friend_send_message(r["friend_number"], payload):
                    self.db.execute(
                        "UPDATE messages SET last_attempt_at=? WHERE msg_uuid=?",
                        (now, r["msg_uuid"]),
                    )
        self.db.commit()

    def _on_friend_message(self, friend_number: int, message: bytes, msg_type: int) -> None:
        env = envelope.parse(message)
        etype = env.get("type")

        if etype == "ack":
            ref = env.get("data", {}).get("ref_uuid")
            if ref:
                self.db.execute(
                    "UPDATE messages SET status='delivered', delivered_at=? "
                    "WHERE msg_uuid=? AND direction='out' AND status='sent'",
                    (int(time.time()), ref),
                )
                self.db.commit()
            return

        if etype == "contact_share":
            self._receive_contact_share(friend_number, env)
            return

        if etype == "profile":
            self._receive_profile(friend_number, env)
            return

        if etype != "text":
            return

        row = self.db.execute(
            "SELECT id, alias FROM contacts WHERE friend_number=?", (friend_number,)
        ).fetchone()
        if row is None:
            log.warning("message from unknown friend_number %d", friend_number)
            return
        times = self._text_times.setdefault(friend_number, deque())
        if not _within_rate(times, TEXT_RATE_LIMIT, time.time()):
            log.warning("rate limit: dropped a text from friend %d", friend_number)
            return
        data = env.get("data", {})
        body = data.get("body", "")
        session = str(data.get("from_session") or "")[:64] or None
        # INSERT OR IGNORE: msg_uuid is UNIQUE, so a re-delivered message is dropped.
        cur = self.db.execute(
            "INSERT OR IGNORE INTO messages (msg_uuid, contact_id, direction, msg_type, "
            "content, created_at, received_at, status, from_session) "
            "VALUES (?, ?, 'in', 'text', ?, ?, ?, 'received', ?)",
            (env["uuid"], row["id"], body, env.get("ts", int(time.time())), int(time.time()),
             session),
        )
        self.db.commit()
        if cur.rowcount:
            self._queue_hook(row["alias"], env["uuid"], "text")
        # ACK even for a duplicate, so the sender can clear it (PRD §4.4.1).
        self.tox.friend_send_message(friend_number, envelope.encode(envelope.make_ack(env["uuid"])))

    def _receive_contact_share(self, friend_number: int, env: dict) -> None:
        sender = self.db.execute(
            "SELECT id FROM contacts WHERE friend_number=?", (friend_number,)
        ).fetchone()
        if sender is None:
            return
        data = env.get("data", {})
        tox_id = data.get("tox_id")
        if not tox_id:
            return
        # Never auto-add (PRD §4.5.3); just queue for the user to confirm. Dedup on (from, who).
        exists = self.db.execute(
            "SELECT 1 FROM pending_introductions WHERE from_contact_id=? AND introduced_tox_id=? "
            "AND status='pending'",
            (sender["id"], tox_id),
        ).fetchone()
        if exists is None:
            self.db.execute(
                "INSERT INTO pending_introductions (from_contact_id, introduced_tox_id, "
                "suggested_alias, note, received_at, status) VALUES (?, ?, ?, ?, ?, 'pending')",
                (sender["id"], tox_id, data.get("suggested_alias"), data.get("note", ""),
                 int(time.time())),
            )
            self.db.commit()

    # --- profile cards (design §4.1) ---
    def _receive_profile(self, friend_number: int, env: dict) -> None:
        prof = envelope.clean_profile(env.get("data"))
        if not prof:
            return
        self.db.execute(
            "UPDATE contacts SET profile_json=?, profile_at=?, proto=? WHERE friend_number=?",
            (json.dumps(prof, ensure_ascii=False), int(time.time()),
             int(prof.get("proto") or 1), friend_number),
        )
        self.db.commit()

    def _send_profile(self, friend_number: int) -> bool:
        if not self.profile:
            return False
        return self.tox.friend_send_message(
            friend_number, envelope.encode(envelope.make_profile(self.profile)))

    def _broadcast_profile(self) -> int:
        rows = self.db.execute(
            "SELECT friend_number FROM contacts WHERE is_online=1 AND friend_number IS NOT NULL"
        ).fetchall()
        return sum(1 for r in rows if self._send_profile(r["friend_number"]))

    def _load_profile(self) -> dict:
        p = paths.profile_path()
        if not p.exists():
            return {}
        try:
            return envelope.clean_profile(json.loads(p.read_text()))
        except (OSError, ValueError):
            log.warning("profile.json is unreadable; ignoring it")
            return {}

    # --- invites (design §4.2) ---
    def _accept_invited(self, pk: str, name: str, nonce: str) -> bool:
        """Auto-accept a friend request that answers one of our live invites."""
        if self.db.execute("SELECT 1 FROM contacts WHERE public_key=?", (pk,)).fetchone():
            return False
        fn = self.tox.friend_add_norequest(bytes.fromhex(pk))
        if fn == NO_FRIEND:
            return False
        alias = self._free_alias(name or pk[:8].lower())
        now = int(time.time())
        self.db.execute(
            "INSERT INTO contacts (public_key, alias, added_at, added_by, friend_number) "
            "VALUES (?, ?, ?, 'invite', ?)",
            (pk, alias, now, fn),
        )
        self.db.execute("UPDATE invites SET used_at=? WHERE nonce=?", (now, nonce))
        self.db.commit()
        self._save_state()
        log.info("accepted invited friend %s as %r", pk[:8], alias)
        return True

    def _free_alias(self, name: str) -> str:
        base = invite.safe_alias(name)
        alias, n = base, 2
        while self.db.execute("SELECT 1 FROM contacts WHERE alias=?", (alias,)).fetchone():
            alias, n = f"{base}-{n}", n + 1
        return alias

    # --- incoming file transfers (image/voice/video, fired during tox.iterate()) ---
    def _on_file_recv(self, friend_number: int, file_number: int, kind: int,
                      file_size: int, filename: bytes) -> None:
        """A friend offers a file. Accept data files from known contacts, store
        them under media/, and reject everything else (avatars, oversized, unknown)."""
        key = (friend_number, file_number)
        contact = self.db.execute(
            "SELECT id FROM contacts WHERE friend_number=?", (friend_number,)
        ).fetchone()
        if contact is None or kind != FILE_KIND_DATA or (file_size and file_size > MAX_FILE_BYTES):
            self.tox.file_control(friend_number, file_number, FILE_CONTROL_CANCEL)
            return
        name = _safe_filename(filename)
        uid = str(_uuid.uuid4())
        final_path = paths.ensure_media_dir() / f"{uid}__{name}"
        part_path = final_path.with_name(final_path.name + ".part")
        try:
            fh = open(part_path, "wb")
        except OSError:
            log.exception("could not open media file for writing")
            self.tox.file_control(friend_number, file_number, FILE_CONTROL_CANCEL)
            return
        self._transfers[key] = {
            "uuid": uid, "contact_id": contact["id"], "msg_type": _classify_media(name),
            "fh": fh, "final_path": final_path, "part_path": part_path,
            "received": 0, "ts": int(time.time()),
        }
        self.tox.file_control(friend_number, file_number, FILE_CONTROL_RESUME)
        log.info("receiving %s (%s) from friend %d", name,
                 self._transfers[key]["msg_type"], friend_number)

    def _on_file_recv_chunk(self, friend_number: int, file_number: int,
                            position: int, data: bytes) -> None:
        key = (friend_number, file_number)
        tr = self._transfers.get(key)
        if tr is None:
            return
        if not data:  # zero-length chunk signals completion
            self._finish_transfer(key)
            return
        tr["received"] += len(data)
        if tr["received"] > MAX_FILE_BYTES:
            log.warning("incoming file exceeded size cap; cancelling")
            self.tox.file_control(friend_number, file_number, FILE_CONTROL_CANCEL)
            self._discard_transfer(key)
            return
        try:
            tr["fh"].seek(position)
            tr["fh"].write(data)
        except OSError:
            log.exception("error writing media chunk")
            self.tox.file_control(friend_number, file_number, FILE_CONTROL_CANCEL)
            self._discard_transfer(key)

    def _on_file_recv_control(self, friend_number: int, file_number: int, control: int) -> None:
        if control == FILE_CONTROL_CANCEL:
            self._discard_transfer((friend_number, file_number))

    def _finish_transfer(self, key: tuple[int, int]) -> None:
        tr = self._transfers.pop(key, None)
        if tr is None:
            return
        try:
            tr["fh"].close()
            os.replace(tr["part_path"], tr["final_path"])
        except OSError:
            log.exception("could not finalize received file")
            return
        cur = self.db.execute(
            "INSERT OR IGNORE INTO messages (msg_uuid, contact_id, direction, msg_type, "
            "content, created_at, received_at, status) VALUES (?, ?, 'in', ?, ?, ?, ?, 'received')",
            (tr["uuid"], tr["contact_id"], tr["msg_type"], str(tr["final_path"]),
             tr["ts"], int(time.time())),
        )
        self.db.commit()
        if cur.rowcount:
            alias = self.db.execute(
                "SELECT alias FROM contacts WHERE id=?", (tr["contact_id"],)
            ).fetchone()["alias"]
            self._queue_hook(alias, tr["uuid"], tr["msg_type"])
        log.info("received %s message from contact %d", tr["msg_type"], tr["contact_id"])

    # --- on_message hook ---
    def _queue_hook(self, alias: str, msg_uuid: str, msg_type: str) -> None:
        """Note a newly stored incoming message; the hook fires after HOOK_DEBOUNCE quiet."""
        self._hook_msgs.append({"alias": alias, "msg_uuid": msg_uuid, "msg_type": msg_type})
        self._hook_due = time.time() + HOOK_DEBOUNCE

    def _tick_hook(self) -> None:
        """Run `[hooks] on_message` for queued messages. Called from the event loop.

        One hook process at a time, never waited on: we reap it on later ticks,
        so the single-threaded loop never blocks. The command gets TOXI_FROM
        (comma-separated aliases), TOXI_COUNT, and a JSON list of
        {alias, msg_uuid, msg_type} on stdin. Message bodies are never passed,
        so a webhook only learns that something arrived, not what.
        """
        p = self._hook_proc
        if p is not None:
            if p.poll() is None:
                if time.time() - self._hook_started > HOOK_TIMEOUT:
                    log.warning("on_message hook timed out; killing it")
                    p.kill()
                return
            if p.returncode:
                log.warning("on_message hook exited with %d", p.returncode)
            self._hook_proc = None
        if not self._hook_msgs or time.time() < self._hook_due:
            return
        msgs, self._hook_msgs = self._hook_msgs, []
        cmd = config.on_message_hook()
        if not cmd:
            return
        env = dict(os.environ,
                   TOXI_FROM=",".join(dict.fromkeys(m["alias"] for m in msgs)),
                   TOXI_COUNT=str(len(msgs)))
        try:
            p = subprocess.Popen(cmd, shell=True, stdin=subprocess.PIPE,
                                 stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                 env=env, start_new_session=True)
        except OSError:
            log.exception("could not start on_message hook")
            return
        try:
            p.stdin.write(json.dumps(msgs).encode())
            p.stdin.close()
        except OSError:
            pass  # the command doesn't read stdin
        self._hook_proc, self._hook_started = p, time.time()

    def _discard_transfer(self, key: tuple[int, int]) -> None:
        tr = self._transfers.pop(key, None)
        if tr is None:
            return
        try:
            tr["fh"].close()
        except OSError:
            pass
        try:
            tr["part_path"].unlink(missing_ok=True)
        except OSError:
            pass

    def _save_state(self) -> None:
        data = self.tox.get_savedata()
        tmp = paths.tox_state_path().with_suffix(".tmp")
        tmp.write_bytes(data)
        os.chmod(tmp, 0o600)
        os.replace(tmp, paths.tox_state_path())

    def _bootstrap(self) -> None:
        for host, port, key in DEFAULT_BOOTSTRAP:
            self.tox.bootstrap(host, port, key)

    def _listen(self) -> None:
        sp = paths.socket_path()
        if sp.exists():
            if self._daemon_alive(sp):
                raise SystemExit("daemon already running")
            sp.unlink()  # stale socket from a crashed daemon
        s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        s.bind(str(sp))
        os.chmod(sp, 0o600)
        s.listen(8)
        self.sock = s
        paths.pid_path().write_text(str(os.getpid()))
        log.info("listening on %s (pid %d)", sp, os.getpid())

    @staticmethod
    def _daemon_alive(sp) -> bool:
        try:
            client.request("get_status", sock_path=sp)
            return True
        except Exception:
            return False

    # --- event loop ---
    def run(self) -> None:
        self._listen()
        self.running = True
        try:
            while self.running:
                self.tox.iterate()
                self._tick_hook()
                if time.time() - self._last_sweep > SWEEP_INTERVAL:
                    self._retry_sweep()
                    self._last_sweep = time.time()
                timeout = min(self.tox.iteration_interval() / 1000, 0.1)
                self.sock.settimeout(timeout)
                try:
                    conn, _ = self.sock.accept()
                except (TimeoutError, socket.timeout):
                    continue
                except OSError:
                    break
                self._handle(conn)
        finally:
            self._cleanup()

    def _handle(self, conn: socket.socket) -> None:
        conn.settimeout(5.0)
        try:
            req = ipc.recv_message(conn)
            ipc.send_message(conn, self._dispatch(req))
        except Exception:
            log.exception("error handling request")
        finally:
            conn.close()

    def _dispatch(self, req: dict) -> dict:
        rid = req.get("id")
        handler = self._handlers.get(req.get("method"))
        if handler is None:
            return {"id": rid, "error": {"code": "METHOD_NOT_FOUND",
                                         "message": f"unknown method: {req.get('method')}"}}
        try:
            return {"id": rid, "result": handler(req.get("params") or {})}
        except RpcError as e:
            return {"id": rid, "error": {"code": e.code, "message": str(e)}}
        except Exception as e:
            log.exception("handler failed")
            return {"id": rid, "error": {"code": "INTERNAL", "message": str(e)}}

    def _cleanup(self) -> None:
        log.info("shutting down")
        try:
            self._save_state()
        finally:
            if self.sock:
                self.sock.close()
            for p in (paths.socket_path(), paths.pid_path()):
                if p.exists():
                    p.unlink()
            self.tox.kill()
            self.db.close()

    # --- RPC handlers ---
    def _get_me(self, params: dict) -> dict:
        return {
            "tox_id": self.tox.self_get_address_hex(),
            "name": self.tox.self_get_name().decode("utf-8", "replace"),
            "connection": self.tox.self_get_connection_status(),
        }

    def _set_name(self, params: dict) -> dict:
        name = params.get("name", "")
        if not self.tox.self_set_name(name.encode("utf-8")):
            raise RpcError("SET_NAME_FAILED", "could not set name")
        self._save_state()
        return {"name": name}

    def _get_status(self, params: dict) -> dict:
        contacts = [
            dict(r) for r in self.db.execute(
                "SELECT alias, is_online, last_seen FROM contacts ORDER BY is_online DESC, alias"
            ).fetchall()
        ]
        queue_size = self.db.execute(
            "SELECT COUNT(*) AS c FROM messages WHERE direction='out' AND status='queued'"
        ).fetchone()["c"]
        since = int(time.time()) - 86400
        stat = lambda sql: self.db.execute(sql, (since,)).fetchone()["c"]
        return {
            "pid": os.getpid(),
            "uptime_seconds": int(time.time() - self._started_at),
            "dht_connected": self.tox.is_connected(),
            "connection": self.tox.self_get_connection_status(),
            "tox_id": self.tox.self_get_address_hex(),
            "name": self.tox.self_get_name().decode("utf-8", "replace"),
            "contacts": contacts,
            "contacts_total": len(contacts),
            "contacts_online": sum(1 for c in contacts if c["is_online"]),
            "queue_size": queue_size,
            "stats_24h": {
                "sent": stat("SELECT COUNT(*) AS c FROM messages WHERE direction='out' "
                             "AND status IN ('sent','delivered','read') AND created_at>=?"),
                "received": stat("SELECT COUNT(*) AS c FROM messages WHERE direction='in' "
                                 "AND received_at>=?"),
                "failed": stat("SELECT COUNT(*) AS c FROM messages WHERE direction='out' "
                               "AND status='failed' AND created_at>=?"),
            },
        }

    def _add_contact(self, params: dict) -> dict:
        tox_id = params.get("tox_id", "").strip().upper()
        alias = params.get("alias", "").strip()
        request_msg = params.get("request_msg") or "Let's chat on toxi"
        if len(tox_id) != 76 or not _is_hex(tox_id):
            raise RpcError("INVALID_TOX_ID", "Tox ID must be 76 hex characters")
        if not alias:
            raise RpcError("INVALID_ALIAS", "an alias is required")
        public_key = tox_id[:64]
        if self.db.execute(
            "SELECT 1 FROM contacts WHERE alias=? OR public_key=?", (alias, public_key)
        ).fetchone():
            raise RpcError("CONTACT_EXISTS", "a contact with that alias or key already exists")

        fn = self.tox.friend_add(bytes.fromhex(tox_id), request_msg.encode("utf-8"))
        if fn == NO_FRIEND:
            raise RpcError("FRIEND_ADD_FAILED", "tox_friend_add failed (bad ID or own key?)")
        self.db.execute(
            "INSERT INTO contacts (tox_id, public_key, alias, added_at, added_by, friend_number) "
            "VALUES (?, ?, ?, ?, 'manual', ?)",
            (tox_id, public_key, alias, int(time.time()), fn),
        )
        self.db.commit()
        self._save_state()
        return {"alias": alias}

    def _remove_contact(self, params: dict) -> dict:
        alias = params.get("alias", "").strip()
        if not alias:
            raise RpcError("INVALID_ALIAS", "an alias is required")
        row = self.db.execute(
            "SELECT id, public_key FROM contacts WHERE alias=?", (alias,)
        ).fetchone()
        if row is None:
            raise RpcError("CONTACT_NOT_FOUND", f"no contact named {alias!r}")
        # Resolve friend_number via public_key (DB column can be stale across restarts).
        fn = self.tox.friend_by_public_key(bytes.fromhex(row["public_key"]))
        if fn != NO_FRIEND:
            self.tox.friend_delete(fn)
        # FK requires clearing children before the contact row.
        self.db.execute("DELETE FROM messages WHERE contact_id=?", (row["id"],))
        self.db.execute("DELETE FROM pending_introductions WHERE from_contact_id=?", (row["id"],))
        self.db.execute("DELETE FROM contacts WHERE id=?", (row["id"],))
        self.db.commit()
        self._save_state()
        return {"alias": alias}

    def _accept_request(self, params: dict) -> dict:
        prefix = params.get("public_key", "").strip().upper()
        alias = params.get("alias", "").strip()
        if not alias:
            raise RpcError("INVALID_ALIAS", "an alias is required")
        pending = self.db.execute(
            "SELECT public_key FROM friend_requests WHERE status='pending'"
        ).fetchall()
        matches = [r["public_key"] for r in pending if r["public_key"].startswith(prefix)]
        if not matches:
            raise RpcError("REQUEST_NOT_FOUND", "no matching pending friend request")
        if len(matches) > 1:
            raise RpcError("AMBIGUOUS", "public key prefix matches multiple requests")
        pk = matches[0]
        if self.db.execute(
            "SELECT 1 FROM contacts WHERE alias=? OR public_key=?", (alias, pk)
        ).fetchone():
            raise RpcError("CONTACT_EXISTS", "a contact with that alias or key already exists")

        fn = self.tox.friend_add_norequest(bytes.fromhex(pk))
        if fn == NO_FRIEND:
            raise RpcError("ACCEPT_FAILED", "tox_friend_add_norequest failed")
        self.db.execute(
            "INSERT INTO contacts (public_key, alias, added_at, added_by, friend_number) "
            "VALUES (?, ?, ?, 'manual', ?)",
            (pk, alias, int(time.time()), fn),
        )
        self.db.execute("UPDATE friend_requests SET status='accepted' WHERE public_key=?", (pk,))
        self.db.commit()
        self._save_state()
        return {"alias": alias, "public_key": pk}

    def _list_contacts(self, params: dict) -> dict:
        query = ("SELECT alias, public_key, tox_id, is_online, last_seen, proto, profile_json "
                 "FROM contacts")
        if params.get("online_only"):
            query += " WHERE is_online=1"
        query += " ORDER BY alias"
        return {"contacts": [_with_profile(r) for r in self.db.execute(query).fetchall()]}

    def _list_requests(self, params: dict) -> dict:
        rows = self.db.execute(
            "SELECT public_key, message, received_at FROM friend_requests "
            "WHERE status='pending' ORDER BY received_at"
        ).fetchall()
        return {"requests": [dict(r) for r in rows]}

    def _send_message(self, params: dict) -> dict:
        alias = params.get("alias", "").strip()
        body = params.get("body", "")
        contact = self.db.execute(
            "SELECT id, friend_number, is_online FROM contacts WHERE alias=?", (alias,)
        ).fetchone()
        if contact is None:
            raise RpcError("CONTACT_NOT_FOUND", f"no contact named {alias!r}")

        session = str(params.get("session") or "").strip()[:64] or None
        # A body too long for one Tox message goes out as several "[i/n] " parts.
        online = bool(contact["is_online"])
        uuids = []
        for part in envelope.split_text(body, MAX_MESSAGE_LENGTH, session):
            env = envelope.make_text(part, session)
            # Persist first (status 'queued'), then try to send (PRD §4.3.3).
            self.db.execute(
                "INSERT INTO messages (msg_uuid, contact_id, direction, msg_type, content, "
                "created_at, status, from_session) "
                "VALUES (?, ?, 'out', 'text', ?, ?, 'queued', ?)",
                (env["uuid"], contact["id"], part, env["ts"], session),
            )
            self.db.commit()
            uuids.append(env["uuid"])
            # Once one part is queued, queue the rest too so they stay in order.
            online = online and self.tox.friend_send_message(
                contact["friend_number"], envelope.encode(env))
            if online:
                self.db.execute(
                    "UPDATE messages SET status='sent', last_attempt_at=? WHERE msg_uuid=?",
                    (env["ts"], env["uuid"]),
                )
                self.db.commit()
        res = {"msg_uuid": uuids[0], "status": "sent" if online else "queued"}
        if len(uuids) > 1:
            res.update(parts=len(uuids), msg_uuids=uuids)
        return res

    def _get_messages(self, params: dict) -> dict:
        conds, args = [], []
        if params.get("alias"):
            conds.append("c.alias=?")
            args.append(params["alias"])
        if params.get("unread_only"):
            conds.append("m.direction='in' AND m.read_at IS NULL")
        query = ("SELECT m.msg_uuid, c.alias, m.direction, m.msg_type, m.content, m.created_at, "
                 "m.status, m.read_at, m.from_session, c.profile_json "
                 "FROM messages m JOIN contacts c ON c.id=m.contact_id")
        if conds:
            query += " WHERE " + " AND ".join(conds)
        query += " ORDER BY m.created_at DESC, m.id DESC LIMIT ?"
        args.append(int(params.get("limit", 20)))
        return {"messages": [_with_profile(r) for r in self.db.execute(query, args).fetchall()]}

    def _get_message(self, params: dict) -> dict:
        row = self.db.execute(
            "SELECT m.msg_uuid, c.alias, m.direction, m.msg_type, m.content, m.created_at, "
            "m.read_at, m.from_session, c.profile_json "
            "FROM messages m JOIN contacts c ON c.id=m.contact_id WHERE m.msg_uuid=?",
            (params.get("msg_uuid"),),
        ).fetchone()
        return {"message": _with_profile(row) if row else None}

    def _mark_read(self, params: dict) -> dict:
        now = int(time.time())
        uuids = params.get("msg_uuids", [])
        marked = 0
        for u in uuids:
            cur = self.db.execute(
                "UPDATE messages SET read_at=?, status='read' "
                "WHERE msg_uuid=? AND direction='in' AND read_at IS NULL",
                (now, u),
            )
            marked += cur.rowcount
        self.db.commit()
        return {"marked": marked}

    def _list_queue(self, params: dict) -> dict:
        rows = self.db.execute(
            "SELECT c.alias, m.content, m.created_at FROM messages m "
            "JOIN contacts c ON c.id=m.contact_id "
            "WHERE m.direction='out' AND m.status='queued' ORDER BY m.created_at ASC, m.id ASC"
        ).fetchall()
        return {"queue": [dict(r) for r in rows]}

    def _introduce(self, params: dict) -> dict:
        to_alias = params.get("to_alias", "").strip()
        contact_alias = params.get("contact_alias", "").strip()
        note = params.get("note") or ""
        to = self.db.execute(
            "SELECT friend_number, is_online FROM contacts WHERE alias=?", (to_alias,)
        ).fetchone()
        if to is None:
            raise RpcError("CONTACT_NOT_FOUND", f"no contact named {to_alias!r}")
        whom = self.db.execute(
            "SELECT tox_id, alias FROM contacts WHERE alias=?", (contact_alias,)
        ).fetchone()
        if whom is None:
            raise RpcError("CONTACT_NOT_FOUND", f"no contact named {contact_alias!r}")
        if not whom["tox_id"]:
            raise RpcError("NO_TOX_ID",
                           f"can't introduce {contact_alias!r}: their full Tox ID is unknown")
        if not to["is_online"]:
            raise RpcError("RECIPIENT_OFFLINE",
                           f"{to_alias!r} is offline; introductions are sent live")
        payload = envelope.encode(
            envelope.make_contact_share(whom["tox_id"], whom["alias"], note)
        )
        if not self.tox.friend_send_message(to["friend_number"], payload):
            raise RpcError("SEND_FAILED", "could not send the introduction")
        return {"to": to_alias, "whom": contact_alias}

    def _list_introductions(self, params: dict) -> dict:
        rows = self.db.execute(
            "SELECT pi.introduced_tox_id, pi.suggested_alias, pi.note, pi.received_at, "
            "c.alias AS from_alias FROM pending_introductions pi "
            "JOIN contacts c ON c.id=pi.from_contact_id "
            "WHERE pi.status='pending' ORDER BY pi.received_at"
        ).fetchall()
        return {"introductions": [dict(r) for r in rows]}

    def _accept_introduction(self, params: dict) -> dict:
        from_alias = params.get("from_alias", "").strip()
        whom = params.get("whom", "").strip()
        alias = (params.get("alias") or whom).strip()
        from_contact = self.db.execute(
            "SELECT id FROM contacts WHERE alias=?", (from_alias,)
        ).fetchone()
        if from_contact is None:
            raise RpcError("CONTACT_NOT_FOUND", f"no contact named {from_alias!r}")
        intro = self.db.execute(
            "SELECT id, introduced_tox_id FROM pending_introductions "
            "WHERE from_contact_id=? AND suggested_alias=? AND status='pending'",
            (from_contact["id"], whom),
        ).fetchone()
        if intro is None:
            raise RpcError("INTRO_NOT_FOUND", f"no pending introduction for {whom!r}")
        tox_id = intro["introduced_tox_id"].upper()
        public_key = tox_id[:64]
        if self.db.execute(
            "SELECT 1 FROM contacts WHERE alias=? OR public_key=?", (alias, public_key)
        ).fetchone():
            raise RpcError("CONTACT_EXISTS", "a contact with that alias or key already exists")
        fn = self.tox.friend_add(bytes.fromhex(tox_id), b"added you via an introduction")
        if fn == NO_FRIEND:
            raise RpcError("FRIEND_ADD_FAILED", "tox_friend_add failed")
        self.db.execute(
            "INSERT INTO contacts (tox_id, public_key, alias, added_at, added_by, friend_number) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (tox_id, public_key, alias, int(time.time()), f"introduce:{from_alias}", fn),
        )
        self.db.execute(
            "UPDATE pending_introductions SET status='accepted' WHERE id=?", (intro["id"],)
        )
        self.db.commit()
        self._save_state()
        return {"alias": alias}

    def _get_profile(self, params: dict) -> dict:
        return {"profile": self.profile}

    def _set_profile(self, params: dict) -> dict:
        """Merge fields into this identity's card, persist it, and tell online friends."""
        prof = dict(self.profile)
        prof.update(envelope.clean_profile(params.get("profile")))
        prof = envelope.clean_profile(prof)
        prof["proto"] = envelope.VERSION
        prof["caps"] = list(envelope.CAPS)
        if not prof.get("name"):
            prof["name"] = self.tox.self_get_name().decode("utf-8", "replace") or None
            if not prof["name"]:
                del prof["name"]
        p = paths.profile_path()
        p.write_text(json.dumps(prof, ensure_ascii=False, indent=2) + "\n")
        os.chmod(p, 0o600)
        self.profile = prof
        return {"profile": prof, "sent_to": self._broadcast_profile()}

    def _create_invite(self, params: dict) -> dict:
        ttl = int(params.get("ttl") or INVITE_TTL)
        now = int(time.time())
        nonce = secrets.token_hex(16)
        self.db.execute(
            "INSERT INTO invites (nonce, created_at, expires_at) VALUES (?, ?, ?)",
            (nonce, now, now + ttl),
        )
        self.db.commit()
        name = self.profile.get("name") or self.tox.self_get_name().decode("utf-8", "replace")
        data = {"v": 1, "tox_id": self.tox.self_get_address_hex(), "name": name,
                "nonce": nonce, "exp": now + ttl}
        return {"invite": invite.encode(data), "expires_at": now + ttl, "name": name}

    def _shutdown(self, params: dict) -> dict:
        self.running = False
        return {"stopped": True}


def _setup_logging() -> None:
    if log.handlers:
        return
    # Rotate at 10 MB, keep 5 files (PRD §4.11). Bodies are never logged.
    handler = RotatingFileHandler(
        paths.log_path(), maxBytes=10 * 1024 * 1024, backupCount=5
    )
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
    log.addHandler(handler)
    log.setLevel(getattr(logging, config.daemon_log_level().upper(), logging.INFO))


def main() -> None:
    try:
        Daemon().run()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
