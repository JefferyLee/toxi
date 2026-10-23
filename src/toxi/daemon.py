"""The resident daemon (PRD §3.2): one Tox instance + one IPC server.

Single-threaded by design. The toxcore handle is not thread-safe, so the event
loop both drives `tox_iterate` and serves IPC requests from one thread: we wait
on `accept()` with a timeout derived from `tox_iteration_interval`, so we never
block the protocol for long. This sidesteps all locking.
"""
import hashlib
import hmac
import json
import logging
import os
import secrets
import selectors
import socket
import subprocess
import threading
import time
import urllib.request
import uuid as _uuid
from collections import deque
from logging.handlers import RotatingFileHandler
from pathlib import Path

from . import __version__, client, config, db, envelope, invite, ipc, paths, sessions
from .tox import (
    FILE_CONTROL_CANCEL,
    FILE_CONTROL_RESUME,
    FILE_KIND_DATA,
    MAX_MESSAGE_LENGTH,
    NO_FILE,
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
PART_TIMEOUT = 60  # seconds to wait for the rest of a multi-part text before storing it partial
MAX_BODY_BYTES = 64 * 1024  # largest text body (after reassembly); bigger bodies go as a file
FILE_META_TTL = 300  # seconds a `file` meta waits for its transfer (or the other way round)

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


def _post_json(url: str, body: bytes, headers: dict) -> None:
    """Fire-and-forget HTTP POST for the on_message webhook (runs in a thread)."""
    try:
        req = urllib.request.Request(url, data=body, headers=headers, method="POST")
        with urllib.request.urlopen(req, timeout=10) as resp:
            if resp.status >= 300:
                log.warning("on_message webhook answered %s", resp.status)
    except Exception as e:  # network errors must never reach the event loop
        log.warning("on_message webhook failed: %s", e)


def _sha256(path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


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


# Which of the user's sessions an incoming message belongs to (design: reply
# routing): the session that sent the message it answers, else the session that
# last wrote in the same thread with that contact. Local project dirs only.
def _route_sql(col: str) -> str:
    """`col` of the outgoing message an incoming one answers (by reply_to, else
    the latest in its thread to that contact)."""
    return (
        "CASE WHEN m.direction='in' THEN COALESCE("
        f"(SELECT o.{col} FROM messages o WHERE o.direction='out' AND o.from_dir IS NOT NULL "
        " AND m.reply_to IS NOT NULL AND (o.msg_uuid=m.reply_to OR o.part_id=m.reply_to) LIMIT 1),"
        f"(SELECT o.{col} FROM messages o WHERE o.direction='out' AND o.from_dir IS NOT NULL "
        " AND m.thread IS NOT NULL AND o.thread=m.thread AND o.contact_id=m.contact_id "
        " ORDER BY o.created_at DESC, o.id DESC LIMIT 1)) END"
    )


_ROUTE_SQL = (_route_sql("from_dir") + " AS route_dir, "
              + _route_sql("from_session") + " AS route_session ")


def _with_profile(row) -> dict:
    """Row -> dict: profile_json parsed into `profile`, meta columns normalised."""
    d = dict(row)
    pj = d.pop("profile_json", None)
    if "profile" not in d:
        try:
            d["profile"] = json.loads(pj) if pj else None
        except ValueError:
            d["profile"] = None
    if "kind" in d:
        d["kind"] = d["kind"] or "msg"
    if "needs_human" in d:
        d["needs_human"] = bool(d["needs_human"])
    if "tags" in d:
        try:
            d["tags"] = json.loads(d["tags"]) if d["tags"] else []
        except ValueError:
            d["tags"] = []
    if "relayed" in d:
        try:
            d["relayed"] = json.loads(d["relayed"]) if d["relayed"] else None
        except ValueError:
            d["relayed"] = None
    return d


def _row_meta(row) -> dict:
    """The structured fields stored on an outgoing row, for re-encoding."""
    keys = row.keys()
    raw = {k: row[k] for k in envelope.META_KEYS if k in keys}
    if "room_id" in keys:
        raw["room"], raw["room_msg"] = row["room_id"], row["room_msg_id"]
    if raw.get("relayed"):
        try:
            raw["relayed"] = json.loads(raw["relayed"])
        except ValueError:
            raw["relayed"] = None
    if raw.get("tags"):
        try:
            raw["tags"] = json.loads(raw["tags"])
        except ValueError:
            raw["tags"] = None
    return envelope.clean_meta(raw)


def _meta_values(meta: dict) -> tuple:
    """(thread, reply_to, kind, needs_human, tags_json, room_id, room_msg_id, relayed_json)."""
    return (meta.get("thread"), meta.get("reply_to"), meta.get("kind"),
            1 if meta.get("needs_human") else 0,
            json.dumps(meta["tags"], ensure_ascii=False) if meta.get("tags") else None,
            meta.get("room"), meta.get("room_msg"),
            json.dumps(meta["relayed"], ensure_ascii=False) if meta.get("relayed") else None)


ROOM_HISTORY = 20  # recent room messages a new member receives from the creator
ARCHIVE_INTERVAL = 86400  # the daemon checks [archive] after_days once a day


def _merge_room_sends(msgs: list) -> list:
    """Collapse the fan-out copies of one outgoing room message (same room_msg_id)
    into a single row carrying `recipients: [{alias, status}]`."""
    out, groups = [], {}
    rank = {"queued": 0, "sent": 1, "delivered": 2, "read": 3, "handled": 4, "failed": -1}
    for m in msgs:
        rid = m.get("room_msg_id")
        if not rid or m.get("direction") != "out":
            out.append(m)
            continue
        g = groups.get(rid)
        if g is None:
            g = groups[rid] = dict(m, recipients=[])
            out.append(g)
        g["recipients"].append({"alias": m["alias"], "status": m["status"]})
        if rank.get(m["status"], 0) < rank.get(g["status"], 0):
            g["status"] = m["status"]
    for g in groups.values():
        g["recipients"].sort(key=lambda r: r["alias"])
    return out


def _caps(row) -> list:
    """Capabilities a contact advertised in its profile card ([] if none)."""
    try:
        return list((json.loads(row["profile_json"]) or {}).get("caps") or []) if row["profile_json"] else []
    except (ValueError, KeyError, IndexError, TypeError):
        return []


def _row_payload(row) -> bytes:
    """Re-encode a stored outgoing text row exactly as it was (or will be) sent."""
    part = ({"id": row["part_id"], "i": row["part_i"], "n": row["part_n"]}
            if row["part_id"] else None)
    return envelope.encode(envelope.text_envelope(
        row["msg_uuid"], row["created_at"], row["content"], row["from_session"], part,
        _row_meta(row)))


def _merge_parts(msgs: list) -> list:
    """Collapse the sender-side rows of one multi-part text (same part_id) into a
    single message: content joined in order, msg_uuid = part_id, status = the
    least advanced part. Rows arrive newest first and leave newest first."""
    out, groups = [], {}
    rank = {"queued": 0, "sent": 1, "delivered": 2, "read": 3, "handled": 4, "failed": -1}
    for m in msgs:
        pid = m.get("part_id")
        if not pid:
            out.append(m)
            continue
        g = groups.get(pid)
        if g is None:
            g = groups[pid] = dict(m, msg_uuid=pid, _parts=[])
            out.append(g)
        g["_parts"].append(m)
        if rank.get(m["status"], 0) < rank.get(g["status"], 0):
            g["status"] = m["status"]
    for g in groups.values():
        parts = sorted(g.pop("_parts"), key=lambda x: x.get("part_i") or 0)
        g["content"] = "".join(x["content"] for x in parts)
        g["created_at"] = parts[0]["created_at"]
    for m in out:
        for k in ("part_id", "part_i", "part_n"):
            m.pop(k, None)
    return out

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
        self._subs: list[dict] = []  # live `subscribe` connections (event stream, design §4.3)
        self._parts: dict[tuple[int, str], dict] = {}  # multi-part texts being reassembled
        self._dropped_parts: deque = deque(maxlen=200)  # ids we gave up on (too big)
        self._sel = selectors.DefaultSelector()
        rc = config.retry_config()
        self.ack_timeout = rc["ack_timeout_minutes"] * 60
        self.fail_after = rc["fail_after_hours"] * 3600
        self._last_sweep = 0.0
        self._last_archive = 0.0
        self._started_at = time.time()
        self._transfers: dict[tuple[int, int], dict] = {}  # in-flight incoming files
        self._out: dict[tuple[int, int], dict] = {}  # in-flight outgoing files (design §4.6)
        self._file_meta: dict[tuple[int, str, int], dict] = {}  # `file` metas awaiting a transfer
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
            "list_threads": self._list_threads,
            "create_room": self._create_room,
            "list_rooms": self._list_rooms,
            "get_room": self._get_room,
            "send_room": self._send_room,
            "room_members": self._room_members_rpc,
            "send_file": self._send_file,
            "rename_contact": self._rename_contact,
            "mark_handled": self._mark_handled,
            "archive_messages": self._archive_messages,
            "archive_status": self._archive_status,
            "search_messages": self._search_messages,
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
        tox.on_file_chunk_request(self._on_file_chunk_request)
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
        row = self.db.execute(
            "SELECT id, alias FROM contacts WHERE friend_number=?", (friend_number,)
        ).fetchone()
        if row is None:
            return
        self._emit("contact.online" if online else "contact.offline",
                   {"alias": row["alias"]}, row["alias"])
        if not online:
            self._abort_outgoing(friend_number)
        if online:
            self._send_profile(friend_number)
            self._send_room_cards(friend_number)
            self._flush_queue(row["id"], friend_number)
            self._flush_receipts(row["id"], friend_number)

    def _flush_queue(self, contact_id: int, friend_number: int) -> None:
        """Resend this friend's queued messages in send order (PRD §4.3.4).
        Stop at the first failure so ordering is preserved for the next attempt."""
        rows = self.db.execute(
            "SELECT * FROM messages WHERE contact_id=? AND direction='out' AND status='queued' "
            "ORDER BY created_at ASC, id ASC",
            (contact_id,),
        ).fetchall()
        for row in rows:
            if row["msg_type"] != "text":
                if not self._start_file_transfer(row, friend_number):
                    break
                continue
            if not self.tox.friend_send_message(friend_number, _row_payload(row)):
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
        # File rows marked sent whose transfer is no longer running (daemon restart,
        # link drop toxcore did not report): queue them again so the flush restarts them.
        active = [t["msg_uuid"] for t in self._out.values()] or [""]
        self.db.execute(
            f"UPDATE messages SET status='queued' WHERE direction='out' AND msg_type!='text' "
            f"AND status='sent' AND msg_uuid NOT IN ({','.join('?' * len(active))})", active)
        # Queued rows and owed receipts for friends who are online.
        for c in self.db.execute(
            "SELECT id, friend_number FROM contacts WHERE is_online=1 AND friend_number IS NOT NULL"
        ).fetchall():
            self._flush_queue(c["id"], c["friend_number"])
            self._flush_receipts(c["id"], c["friend_number"])
        rows = self.db.execute(
            "SELECT m.msg_uuid, m.content, m.created_at, m.last_attempt_at, m.from_session, "
            "m.part_id, m.part_i, m.part_n, m.thread, m.reply_to, m.kind, m.needs_human, m.tags, "
            "m.room_id, m.room_msg_id, c.friend_number, c.is_online FROM messages m "
            "JOIN contacts c ON c.id=m.contact_id "
            "WHERE m.direction='out' AND m.msg_type='text' AND m.status='sent' "
            "AND m.delivered_at IS NULL"
        ).fetchall()
        for r in rows:
            if now - r["created_at"] > self.fail_after:
                self.db.execute(
                    "UPDATE messages SET status='failed' WHERE msg_uuid=?", (r["msg_uuid"],)
                )
            elif r["is_online"] and now - (r["last_attempt_at"] or r["created_at"]) > self.ack_timeout:
                if self.tox.friend_send_message(r["friend_number"], _row_payload(r)):
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
                cur = self.db.execute(
                    "UPDATE messages SET status='delivered', delivered_at=? "
                    "WHERE msg_uuid=? AND direction='out' AND status='sent'",
                    (int(time.time()), ref),
                )
                self.db.commit()
                if cur.rowcount:
                    alias = self._alias_of(friend_number)
                    self._emit("delivery", {"msg_uuid": ref, "alias": alias}, alias)
            return

        if etype == "receipt":
            self._receive_receipt(friend_number, env)
            return

        if etype == "contact_share":
            self._receive_contact_share(friend_number, env)
            return

        if etype == "profile":
            self._receive_profile(friend_number, env)
            return

        if etype == "room":
            self._receive_room(friend_number, env)
            return

        if etype == "file":
            self._receive_file_meta(friend_number, env)
            return

        if etype != "text":
            return

        row = self.db.execute(
            "SELECT id, alias FROM contacts WHERE friend_number=?", (friend_number,)
        ).fetchone()
        if row is None:
            log.warning("message from unknown friend_number %d", friend_number)
            return
        data = env.get("data", {})
        if isinstance(data.get("part"), dict):
            self._receive_part(friend_number, row, env, data)
            return
        times = self._text_times.setdefault(friend_number, deque())
        if not _within_rate(times, TEXT_RATE_LIMIT, time.time()):
            log.warning("rate limit: dropped a text from friend %d", friend_number)
            return
        body = data.get("body", "")
        session = str(data.get("from_session") or "")[:64] or None
        meta = envelope.clean_meta(data)
        # INSERT OR IGNORE: msg_uuid is UNIQUE, so a re-delivered message is dropped.
        cur = self.db.execute(
            "INSERT OR IGNORE INTO messages (msg_uuid, contact_id, direction, msg_type, "
            "content, created_at, received_at, status, from_session, "
            "thread, reply_to, kind, needs_human, tags, room_id, room_msg_id, relayed, read_at) "
            "VALUES (?, ?, 'in', 'text', ?, ?, ?, 'received', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (env["uuid"], row["id"], body, env.get("ts", int(time.time())), int(time.time()),
             session, *_meta_values(meta), int(time.time()) if meta.get("relayed") else None),
        )
        self.db.commit()
        if cur.rowcount and body.strip() == "@?" and self._is_owner(row["alias"]):
            self._list_sessions(row["alias"], env["uuid"])
        elif cur.rowcount:
            self._stored(env["uuid"], row["alias"], meta)
        # ACK even for a duplicate, so the sender can clear it (PRD §4.4.1).
        self.tox.friend_send_message(friend_number, envelope.encode(envelope.make_ack(env["uuid"])))

    @staticmethod
    def _is_owner(alias: str) -> bool:
        return config.peer_config().get(alias, {}).get("trust") == "owner"

    def _list_sessions(self, alias: str, msg_uuid: str) -> None:
        """`@?` from the user's own device: answer with the live agent sessions
        it can address with `@name`. The question wakes no one."""
        self.db.execute("UPDATE messages SET read_at=? WHERE msg_uuid=?",
                        (int(time.time()), msg_uuid))
        self.db.commit()
        lines = [f"@{s['name']}  {s['cwd']}" for s in sessions.live()]
        body = "\n".join(lines) if lines else "No agent session is running."
        try:
            self._send_message({"alias": alias, "body": body, "reply_to": msg_uuid})
        except RpcError as e:
            log.warning("could not answer @?: %s", e)

    def _alias_of(self, friend_number: int):
        row = self.db.execute(
            "SELECT alias FROM contacts WHERE friend_number=?", (friend_number,)
        ).fetchone()
        return row["alias"] if row else None

    # --- multi-part texts (design §4.3): reassemble before storing or waking anyone ---
    def _receive_part(self, friend_number: int, row, env: dict, data: dict) -> None:
        part = data["part"]
        try:
            pid = str(part.get("id") or "")[:64]
            i, n = int(part.get("i") or 0), int(part.get("n") or 0)
        except (TypeError, ValueError):
            return
        if not pid or not 1 <= i <= n:
            return
        ack = envelope.encode(envelope.make_ack(env["uuid"]))
        if (pid in self._dropped_parts
                or self.db.execute("SELECT 1 FROM messages WHERE msg_uuid=?", (pid,)).fetchone()):
            self.tox.friend_send_message(friend_number, ack)  # finished or given up: just ACK
            return
        key = (friend_number, pid)
        buf = self._parts.get(key)
        if buf is None:
            # One logical message counts once against the rate limit, not per part.
            times = self._text_times.setdefault(friend_number, deque())
            if not _within_rate(times, TEXT_RATE_LIMIT, time.time()):
                log.warning("rate limit: dropped a multi-part text from friend %d", friend_number)
                return
            buf = self._parts[key] = {
                "n": n, "got": {}, "first": time.time(), "contact_id": row["id"],
                "alias": row["alias"], "ts": env.get("ts", int(time.time())),
                "session": str(data.get("from_session") or "")[:64] or None, "size": 0,
                "meta": envelope.clean_meta(data),
            }
        if i not in buf["got"]:
            body = str(data.get("body", ""))
            buf["got"][i] = body
            buf["size"] += len(body.encode("utf-8"))
        self.tox.friend_send_message(friend_number, ack)
        if buf["size"] > MAX_BODY_BYTES:
            log.warning("multi-part text from friend %d exceeds %d bytes; dropped",
                        friend_number, MAX_BODY_BYTES)
            del self._parts[key]
            self._dropped_parts.append(pid)
            return
        if len(buf["got"]) >= buf["n"]:
            self._finish_parts(key, complete=True)

    def _finish_parts(self, key: tuple[int, str], complete: bool) -> None:
        buf = self._parts.pop(key)
        body = "".join(buf["got"][i] for i in sorted(buf["got"]))
        if not complete:
            body = f"[toxi: only {len(buf['got'])} of {buf['n']} parts arrived] " + body
        now = int(time.time())
        cur = self.db.execute(
            "INSERT OR IGNORE INTO messages (msg_uuid, contact_id, direction, msg_type, "
            "content, created_at, received_at, status, from_session, "
            "thread, reply_to, kind, needs_human, tags, room_id, room_msg_id, relayed, read_at) "
            "VALUES (?, ?, 'in', 'text', ?, ?, ?, 'received', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (key[1], buf["contact_id"], body, buf["ts"], now, buf["session"],
             *_meta_values(buf["meta"]), now if buf["meta"].get("relayed") else None),
        )
        self.db.commit()
        if cur.rowcount:
            self._stored(key[1], buf["alias"], buf["meta"])

    def _stored(self, msg_uuid: str, alias: str, meta: dict) -> None:
        """A text was stored: wake hooks and flag unless it is a mere `ack`
        (design §4.5: acknowledgements never wake anyone) or relayed room
        history (old news, stored as already read); always tell subscribers."""
        wake = meta.get("kind") != "ack" and not meta.get("relayed")
        if wake:
            self._queue_hook(alias, msg_uuid, "text")
        self._announce(msg_uuid, alias, wake=wake)

    def _tick_parts(self) -> None:
        now = time.time()
        for key in [k for k, b in self._parts.items() if now - b["first"] > PART_TIMEOUT]:
            self._finish_parts(key, complete=False)
        for key in [k for k, m in self._file_meta.items() if now - m["ts"] > FILE_META_TTL]:
            del self._file_meta[key]

    # --- read receipts (design §4.3) ---
    def _receive_receipt(self, friend_number: int, env: dict) -> None:
        """`read`: the peer saw it. `done`: the peer handled it (answered, or marked
        it handled) — implies read. Never downgrades a status."""
        data = env.get("data", {})
        ref = str(data.get("ref_uuid") or "")[:64]
        kind = "done" if data.get("kind") == "done" else "read"
        if not ref:
            return
        now = int(time.time())
        if kind == "done":
            cur = self.db.execute(
                "UPDATE messages SET status='handled', handled_at=?, "
                "read_receipt_at=COALESCE(read_receipt_at, ?), delivered_at=COALESCE(delivered_at, ?) "
                "WHERE direction='out' AND handled_at IS NULL AND (msg_uuid=? OR part_id=?)",
                (now, now, now, ref, ref),
            )
        else:
            cur = self.db.execute(
                "UPDATE messages SET status='read', read_receipt_at=?, "
                "delivered_at=COALESCE(delivered_at, ?) "
                "WHERE direction='out' AND read_receipt_at IS NULL AND status!='handled' "
                "AND (msg_uuid=? OR part_id=?)",
                (now, now, ref, ref),
            )
        self.db.commit()
        if cur.rowcount:
            alias = self._alias_of(friend_number)
            self._emit("receipt", {"msg_uuid": ref, "alias": alias, "kind": kind}, alias)

    def _send_receipts(self, uuids: list, kind: str = "read") -> None:
        """Owe the senders a receipt for these messages and try to send it now.

        The receipt is recorded as pending first and only cleared once toxcore
        accepted it, so a sender that is offline (or a daemon restart in
        between) gets it later from _flush_receipts. `done` supersedes `read`.
        """
        if not uuids:
            return
        qs = ",".join("?" * len(uuids))
        self.db.execute(
            f"UPDATE messages SET receipt_pending=? WHERE direction='in' AND msg_uuid IN ({qs}) "
            f"AND (receipt_pending IS NULL OR receipt_pending='read' OR ?='done')",
            (kind, *uuids, kind))
        self.db.commit()
        rows = self.db.execute(
            f"SELECT DISTINCT c.id, c.friend_number FROM messages m JOIN contacts c "
            f"ON c.id=m.contact_id WHERE m.msg_uuid IN ({qs}) AND c.is_online=1", uuids,
        ).fetchall()
        for r in rows:
            self._flush_receipts(r["id"], r["friend_number"])

    def _flush_receipts(self, contact_id: int, friend_number) -> int:
        """Send every receipt still owed to this contact (if it wants receipts)."""
        if friend_number is None:
            return 0
        c = self.db.execute("SELECT profile_json FROM contacts WHERE id=?", (contact_id,)).fetchone()
        if c is None or "receipt" not in _caps(c):
            return 0
        rows = self.db.execute(
            "SELECT msg_uuid, receipt_pending FROM messages WHERE contact_id=? AND direction='in' "
            "AND receipt_pending IS NOT NULL ORDER BY id", (contact_id,)).fetchall()
        sent = 0
        for r in rows:
            payload = envelope.encode(envelope.make_receipt(r["msg_uuid"], r["receipt_pending"]))
            if not self.tox.friend_send_message(friend_number, payload):
                break  # not connected right now: keep the rest pending
            self.db.execute("UPDATE messages SET receipt_pending=NULL WHERE msg_uuid=? "
                            "AND receipt_pending=?", (r["msg_uuid"], r["receipt_pending"]))
            sent += 1
        self.db.commit()
        return sent

    def _mark_handled(self, params: dict) -> dict:
        """We (or our agent) dealt with these incoming messages: mark them read and
        handled locally and send `done` receipts (design: processing receipt)."""
        now = int(time.time())
        uuids = [str(u)[:64] for u in params.get("msg_uuids", [])]
        done = []
        for u in uuids:
            cur = self.db.execute(
                "UPDATE messages SET handled_at=?, read_at=COALESCE(read_at, ?), "
                "status=CASE WHEN status='received' THEN 'read' ELSE status END "
                "WHERE msg_uuid=? AND direction='in' AND handled_at IS NULL", (now, now, u))
            if cur.rowcount:
                done.append(u)
        self.db.commit()
        self._send_receipts(done, "done")
        return {"handled": len(done)}

    def _auto_done(self, contact_id: int, reply_to) -> None:
        """A reply that references one of this contact's messages handles it."""
        if not reply_to:
            return
        row = self.db.execute(
            "SELECT msg_uuid FROM messages WHERE msg_uuid=? AND contact_id=? AND direction='in' "
            "AND handled_at IS NULL", (str(reply_to)[:64], contact_id)).fetchone()
        if row:
            self._mark_handled({"msg_uuids": [row["msg_uuid"]]})

    # --- event stream for subscribers (design §4.3) ---
    def _announce(self, msg_uuid: str, alias: str, wake: bool = True) -> None:
        """A new incoming message was stored: bump inbox.flag (unless `wake` is
        off) and tell subscribers."""
        if wake:
            try:
                paths.inbox_flag_path().touch()
            except OSError:
                log.exception("could not touch inbox.flag")
        msg = self._get_message({"msg_uuid": msg_uuid}).get("message")
        if msg:
            self._emit("message.new", msg, alias)

    def _emit(self, event: str, data: dict, alias=None) -> None:
        if not self._subs:
            return
        dead = []
        for sub in list(self._subs):
            if sub["aliases"] and alias not in sub["aliases"]:
                continue
            payload = data
            if sub["context"] and alias and event == "message.new":
                payload = dict(data, context=self._recent(alias, sub["context"]))
            try:
                ipc.send_message(sub["sock"], {"event": event, "data": payload,
                                               "ts": int(time.time())})
            except (OSError, ipc.IPCError):
                dead.append(sub)  # closed, or too slow to keep up
        for sub in dead:
            self._drop_subscriber(sub)

    def _recent(self, alias: str, n: int) -> list:
        return list(reversed(self._get_messages({"alias": alias, "limit": n})["messages"]))

    def _subscribe(self, conn: socket.socket, req: dict) -> None:
        params = req.get("params") or {}
        aliases = set(params.get("aliases") or []) or None
        sub = {"sock": conn, "aliases": aliases, "context": max(0, int(params.get("context") or 0))}
        ipc.send_message(conn, {"id": req.get("id"), "result": {"subscribed": True}})
        conn.setblocking(False)
        self._sel.register(conn, selectors.EVENT_READ, data=sub)
        self._subs.append(sub)

    def _drain_subscriber(self, sub: dict) -> None:
        """The subscriber socket became readable: either it closed, or it sent
        something we ignore (the stream is one-way)."""
        try:
            data = sub["sock"].recv(4096)
        except BlockingIOError:
            return
        except OSError:
            data = b""
        if not data:
            self._drop_subscriber(sub)

    def _drop_subscriber(self, sub: dict) -> None:
        try:
            self._sel.unregister(sub["sock"])
        except (KeyError, ValueError, OSError):
            pass
        try:
            sub["sock"].close()
        except OSError:
            pass
        if sub in self._subs:
            self._subs.remove(sub)

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
        if "receipt" in (prof.get("caps") or []):
            c = self.db.execute("SELECT id FROM contacts WHERE friend_number=?",
                                (friend_number,)).fetchone()
            if c is not None:
                self._flush_receipts(c["id"], friend_number)

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
            prof = envelope.clean_profile(json.loads(p.read_text()))
        except (OSError, ValueError):
            log.warning("profile.json is unreadable; ignoring it")
            return {}
        if prof:
            # proto/caps describe this build, not the one that wrote the file:
            # refresh them so peers learn new capabilities after an upgrade.
            prof["proto"], prof["caps"] = envelope.VERSION, list(envelope.CAPS)
        return prof

    # --- rooms (design §4.5): app-layer fan-out, members identified by public key ---
    def _my_pk(self) -> str:
        return self.tox.self_get_address_hex()[:64]

    def _resolve_room(self, ref: str):
        ref = (ref or "").strip()
        row = self.db.execute(
            "SELECT * FROM rooms WHERE room_id=? OR name=? OR room_id LIKE ? "
            "ORDER BY created_at DESC", (ref, ref, ref + "%"),
        ).fetchall()
        if not row:
            raise RpcError("ROOM_NOT_FOUND", f"no room named or starting with {ref!r}")
        if len(row) > 1 and not any(r["room_id"] == ref or r["name"] == ref for r in row):
            raise RpcError("AMBIGUOUS", f"{ref!r} matches several rooms")
        return next((r for r in row if r["room_id"] == ref or r["name"] == ref), row[0])

    def _room_card(self, room_id: str) -> bytes:
        room = self.db.execute("SELECT name FROM rooms WHERE room_id=?", (room_id,)).fetchone()
        members = [{"pk": r["public_key"], "name": r["name"] or ""} for r in self.db.execute(
            "SELECT public_key, name FROM room_members WHERE room_id=?", (room_id,)).fetchall()]
        return envelope.encode(envelope.make_room(room_id, room["name"], members))

    def _create_room(self, params: dict) -> dict:
        name = str(params.get("name") or "").strip()[:64]
        aliases = [str(a).strip() for a in params.get("aliases") or [] if str(a).strip()]
        if not name or not aliases:
            raise RpcError("INVALID_ROOM", "a room needs a name and at least one member alias")
        qs = ",".join("?" * len(aliases))
        rows = self.db.execute(
            f"SELECT alias, public_key, friend_number, is_online, profile_json FROM contacts "
            f"WHERE alias IN ({qs})", aliases).fetchall()
        found = {r["alias"]: r for r in rows}
        missing = [a for a in aliases if a not in found]
        if missing:
            raise RpcError("CONTACT_NOT_FOUND", f"not a contact: {', '.join(missing)}")
        room_id, now = str(_uuid.uuid4()), int(time.time())
        me = self.profile.get("name") or self.tox.self_get_name().decode("utf-8", "replace")
        members = [(self._my_pk(), me)]
        for r in rows:
            prof = {}
            try:
                prof = json.loads(r["profile_json"]) if r["profile_json"] else {}
            except ValueError:
                pass
            members.append((r["public_key"], prof.get("name") or r["alias"]))
        self.db.execute("INSERT INTO rooms (room_id, name, created_by, created_at) VALUES (?,?,?,?)",
                        (room_id, name, self._my_pk(), now))
        self.db.executemany("INSERT OR REPLACE INTO room_members (room_id, public_key, name, "
                            "history_sent) VALUES (?, ?, ?, ?)",
                            [(room_id, pk, n, now) for pk, n in members])  # nothing to catch up on
        self.db.commit()
        card = self._room_card(room_id)
        told = [r["alias"] for r in rows
                if r["friend_number"] is not None and r["is_online"]
                and self.tox.friend_send_message(r["friend_number"], card)]
        return {"room_id": room_id, "name": name, "members": len(members), "card_sent_to": told}

    def _room_members_rpc(self, params: dict) -> dict:
        """Add or remove members of a room I created; every member gets the new card."""
        room = self._resolve_room(params.get("room", ""))
        if room["created_by"] != self._my_pk():
            raise RpcError("NOT_ROOM_OWNER", "only the room's creator can change its members")
        add = [str(a).strip() for a in params.get("add") or [] if str(a).strip()]
        remove = [str(a).strip() for a in params.get("remove") or [] if str(a).strip()]
        if not add and not remove:
            raise RpcError("INVALID_ROOM", "nothing to add or remove")
        for alias in add + remove:
            if not self.db.execute("SELECT 1 FROM contacts WHERE alias=?", (alias,)).fetchone():
                raise RpcError("CONTACT_NOT_FOUND", f"not a contact: {alias}")
        for alias in add:
            c = self.db.execute("SELECT public_key, profile_json FROM contacts WHERE alias=?",
                                (alias,)).fetchone()
            try:
                name = (json.loads(c["profile_json"]) if c["profile_json"] else {}).get("name")
            except ValueError:
                name = None
            self.db.execute("INSERT INTO room_members (room_id, public_key, name) VALUES (?, ?, ?) "
                            "ON CONFLICT(room_id, public_key) DO UPDATE SET name=excluded.name",
                            (room["room_id"], c["public_key"], name or alias))  # keeps history_sent
        for alias in remove:
            self.db.execute("DELETE FROM room_members WHERE room_id=? AND public_key="
                            "(SELECT public_key FROM contacts WHERE alias=?)", (room["room_id"], alias))
        self.db.commit()
        card = self._room_card(room["room_id"])
        told = []
        for m in self._room_members(room["room_id"]):
            if m["me"] or not m["alias"] or not m["online"]:
                continue
            fn = self.db.execute("SELECT friend_number FROM contacts WHERE alias=?",
                                 (m["alias"],)).fetchone()["friend_number"]
            if fn is not None and self.tox.friend_send_message(fn, card):
                told.append(m["alias"])
                self._send_room_history(room["room_id"], m["public_key"], fn)
        # Removed members that are online hear about it too (the card no longer lists them).
        for alias in remove:
            c = self.db.execute("SELECT friend_number, is_online FROM contacts WHERE alias=?",
                                (alias,)).fetchone()
            if c["friend_number"] is not None and c["is_online"]:
                self.tox.friend_send_message(c["friend_number"], card)
        return {"room_id": room["room_id"], "name": room["name"],
                "members": self._room_members(room["room_id"]), "card_sent_to": told}

    def _send_room_cards(self, friend_number: int) -> None:
        """A member came online: (re)send the cards of rooms I created that include them."""
        c = self.db.execute("SELECT public_key FROM contacts WHERE friend_number=?",
                            (friend_number,)).fetchone()
        if c is None:
            return
        rows = self.db.execute(
            "SELECT r.room_id FROM rooms r JOIN room_members m ON m.room_id=r.room_id "
            "WHERE r.created_by=? AND m.public_key=?", (self._my_pk(), c["public_key"])).fetchall()
        for r in rows:
            if self.tox.friend_send_message(friend_number, self._room_card(r["room_id"])):
                self._send_room_history(r["room_id"], c["public_key"], friend_number)

    def _send_room_history(self, room_id: str, member_pk: str, friend_number: int) -> int:
        """Once per member: relay the room's last ROOM_HISTORY texts so a newcomer
        is not blind (design backlog 7). Copies carry the original writer and time
        in `relayed`, keep their ids (so re-sends dedupe), create no rows here and
        arrive on the other side already read: history, not news."""
        done = self.db.execute("SELECT history_sent FROM room_members WHERE room_id=? AND public_key=?",
                               (room_id, member_pk)).fetchone()
        if done is None or done["history_sent"]:
            return 0
        member = self.db.execute("SELECT alias FROM contacts WHERE public_key=?", (member_pk,)).fetchone()
        member_alias = member["alias"] if member else None
        # Logical messages (parts and fan-out copies already merged), newest first.
        picked = []
        for m in self._get_messages({"room": room_id, "limit": ROOM_HISTORY + 40})["messages"]:
            if m["msg_type"] != "text" or m.get("relayed"):
                continue
            if m["direction"] == "out" and any(r["alias"] == member_alias for r in m.get("recipients", [])):
                continue  # they already have their own copy of this one
            picked.append(m)
            if len(picked) >= ROOM_HISTORY:
                break
        me = self.profile.get("name") or self.tox.self_get_name().decode("utf-8", "replace") or "me"
        sent = 0
        for m in reversed(picked):  # oldest first
            if m["direction"] == "out":
                who, uid = me, m.get("room_msg_id") or m["msg_uuid"]
            else:
                who = (m.get("profile") or {}).get("name") or m["alias"]
                uid = m["msg_uuid"]
            meta = {"thread": m.get("thread"), "kind": m.get("kind"), "room": room_id, "room_msg": uid,
                    "relayed": {"name": who, "ts": m["created_at"]}}
            if m.get("tags"):
                meta["tags"] = m["tags"]
            r = {"content": m["content"], "created_at": m["created_at"]}
            chunks = envelope.split_text(r["content"], MAX_MESSAGE_LENGTH, None, parts=True, meta=meta)
            for i, chunk in enumerate(chunks, 1):
                part = {"id": uid, "i": i, "n": len(chunks)} if len(chunks) > 1 else None
                env = envelope.text_envelope(uid if not part else f"{uid}:{i}", r["created_at"],
                                             chunk, None, part, meta)
                if not self.tox.friend_send_message(friend_number, envelope.encode(env)):
                    break
            sent += 1
        self.db.execute("UPDATE room_members SET history_sent=? WHERE room_id=? AND public_key=?",
                        (int(time.time()), room_id, member_pk))
        self.db.commit()
        if sent:
            log.info("sent %d history message(s) of room %s to %s", sent, room_id[:8], member_pk[:8])
        return sent

    def _receive_room(self, friend_number: int, env: dict) -> None:
        card = envelope.clean_room(env.get("data"))
        sender = self.db.execute("SELECT public_key FROM contacts WHERE friend_number=?",
                                 (friend_number,)).fetchone()
        if card is None or sender is None:
            return
        existing = self.db.execute("SELECT created_by FROM rooms WHERE room_id=?",
                                   (card["id"],)).fetchone()
        if existing is not None and existing["created_by"] != sender["public_key"]:
            log.warning("ignored a room card for %s from a non-creator", card["id"][:8])
            return
        now = int(time.time())
        if self._my_pk() not in {m["pk"] for m in card["members"]}:
            # The creator took me out: forget the room (history stays).
            self.db.execute("DELETE FROM room_members WHERE room_id=?", (card["id"],))
            self.db.execute("DELETE FROM rooms WHERE room_id=?", (card["id"],))
            self.db.commit()
            log.info("removed from room %s (%s)", card["name"], card["id"][:8])
            return
        self.db.execute("INSERT OR REPLACE INTO rooms (room_id, name, created_by, created_at) "
                        "VALUES (?, ?, ?, COALESCE((SELECT created_at FROM rooms WHERE room_id=?), ?))",
                        (card["id"], card["name"], sender["public_key"], card["id"], now))
        self.db.execute("DELETE FROM room_members WHERE room_id=?", (card["id"],))
        self.db.executemany("INSERT INTO room_members (room_id, public_key, name) VALUES (?, ?, ?)",
                            [(card["id"], m["pk"], m["name"]) for m in card["members"]])
        self.db.commit()
        log.info("room %s (%s) card from friend %d", card["name"], card["id"][:8], friend_number)

    def _room_members(self, room_id: str) -> list:
        """Members with their local view: alias (if a contact), presence, is it me."""
        me = self._my_pk()
        rows = self.db.execute(
            "SELECT m.public_key, m.name, c.alias, c.is_online FROM room_members m "
            "LEFT JOIN contacts c ON c.public_key=m.public_key WHERE m.room_id=?", (room_id,),
        ).fetchall()
        return [{"public_key": r["public_key"], "name": r["name"], "alias": r["alias"],
                 "online": bool(r["is_online"]), "me": r["public_key"] == me} for r in rows]

    def _list_rooms(self, params: dict) -> dict:
        rows = self.db.execute(
            "SELECT r.room_id, r.name, r.created_by, r.created_at, "
            "(SELECT COUNT(*) FROM room_members m WHERE m.room_id=r.room_id) AS members, "
            "(SELECT COUNT(*) FROM messages x WHERE x.room_id=r.room_id AND x.direction='in' "
            " AND x.read_at IS NULL) AS unread, "
            "(SELECT MAX(created_at) FROM messages x WHERE x.room_id=r.room_id) AS last_at "
            "FROM rooms r ORDER BY COALESCE(last_at, r.created_at) DESC").fetchall()
        me = self._my_pk()
        return {"rooms": [dict(r, mine=r["created_by"] == me) for r in rows]}

    def _get_room(self, params: dict) -> dict:
        room = self._resolve_room(params.get("room", ""))
        return {"room": dict(room, mine=room["created_by"] == self._my_pk(),
                             members=self._room_members(room["room_id"]))}

    def _send_room(self, params: dict) -> dict:
        """Fan a text out to every member of a room that is one of my contacts."""
        room = self._resolve_room(params.get("room", ""))
        room_msg = str(_uuid.uuid4())
        results, unreachable = [], []
        for m in self._room_members(room["room_id"]):
            if m["me"]:
                continue
            if not m["alias"]:
                unreachable.append(m["name"] or m["public_key"][:8])
                continue
            p = dict(params, alias=m["alias"], room=room["room_id"], room_msg=room_msg)
            r = self._send_message(p)
            results.append({"alias": m["alias"], "status": r["status"]})
        return {"room_msg_id": room_msg, "room_id": room["room_id"], "name": room["name"],
                "sent": results, "unreachable": unreachable}

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
        key = (friend_number, file_number)
        if control == FILE_CONTROL_CANCEL:
            if key in self._out:
                self._finish_outgoing(key, "failed")  # the receiver declined it
            else:
                self._discard_transfer(key)

    def _finish_transfer(self, key: tuple[int, int]) -> None:
        tr = self._transfers.pop(key, None)
        if tr is None:
            return
        try:
            tr["fh"].close()
            os.chmod(tr["part_path"], 0o600)  # received files are private, like the rest
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
            uid = tr["uuid"]
            name = tr["final_path"].name.split("__", 1)[-1]
            meta = self._file_meta.pop((key[0], name, tr["received"]), None)
            if meta:
                uid = self._attach_file_meta(uid, tr["final_path"], meta)
            alias = self.db.execute(
                "SELECT alias FROM contacts WHERE id=?", (tr["contact_id"],)
            ).fetchone()["alias"]
            self._queue_hook(alias, uid, tr["msg_type"])
            self._announce(uid, alias)
        log.info("received %s message from contact %d", tr["msg_type"], tr["contact_id"])

    # --- file metadata (design §4.6): rides as a `file` envelope next to the transfer ---
    def _receive_file_meta(self, friend_number: int, env: dict) -> None:
        d = env.get("data") or {}
        name = _safe_filename(str(d.get("name") or "").encode("utf-8", "replace"))
        try:
            size = int(d.get("size") or 0)
        except (TypeError, ValueError):
            return
        meta = {"msg_uuid": str(d.get("msg_uuid") or "")[:64],
                "sha256": str(d.get("sha256") or "")[:64].lower(),
                "session": str(d.get("from_session") or "")[:64] or None,
                "meta": envelope.clean_meta(d), "ts": time.time()}
        if not meta["msg_uuid"]:
            return
        # The (small) meta can arrive after the transfer finished: attach it then.
        row = self.db.execute(
            "SELECT m.msg_uuid, m.content FROM messages m JOIN contacts c ON c.id=m.contact_id "
            "WHERE c.friend_number=? AND m.direction='in' AND m.msg_type!='text' "
            "AND m.content LIKE ? AND m.received_at>=? AND m.sha256 IS NULL "
            "ORDER BY m.id DESC LIMIT 1",
            (friend_number, f"%__{name}", int(time.time()) - FILE_META_TTL),
        ).fetchone()
        if row is not None:
            try:
                if Path(row["content"]).stat().st_size == size:
                    self._attach_file_meta(row["msg_uuid"], Path(row["content"]), meta)
                    return
            except OSError:
                pass
        self._file_meta[(friend_number, name, size)] = meta

    def _attach_file_meta(self, row_uuid: str, path: Path, meta: dict) -> str:
        """Give a received file the sender's msg_uuid (so replies and receipts line
        up), its thread/kind/room fields, and a verified digest. Returns the uuid."""
        m = dict(meta["meta"])
        tags = list(m.get("tags") or [])
        digest = meta["sha256"] or None
        if digest:
            try:
                if _sha256(path) != digest:
                    tags.append("sha256-mismatch")
                    log.warning("sha256 mismatch on received file %s", path.name)
            except OSError:
                pass
        if tags:
            m["tags"] = tags
        new_uuid = row_uuid
        taken = self.db.execute("SELECT 1 FROM messages WHERE msg_uuid=?",
                                (meta["msg_uuid"],)).fetchone()
        if not taken:
            new_uuid = meta["msg_uuid"]
        self.db.execute(
            "UPDATE messages SET msg_uuid=?, sha256=?, from_session=?, thread=?, reply_to=?, "
            "kind=?, needs_human=?, tags=?, room_id=?, room_msg_id=?, relayed=? WHERE msg_uuid=?",
            (new_uuid, digest, meta.get("session"), *_meta_values(m), row_uuid),
        )
        self.db.commit()
        return new_uuid

    # --- outgoing files (design §4.6) ---
    def _send_file(self, params: dict) -> dict:
        alias = params.get("alias", "").strip()
        path = Path(str(params.get("path") or "")).expanduser()
        contact = self.db.execute(
            "SELECT id, friend_number FROM contacts WHERE alias=?", (alias,)).fetchone()
        if contact is None:
            raise RpcError("CONTACT_NOT_FOUND", f"no contact named {alias!r}")
        if not path.is_file():
            raise RpcError("FILE_NOT_FOUND", f"no such file: {path}")
        size = path.stat().st_size
        if size > MAX_FILE_BYTES:
            raise RpcError("FILE_TOO_LARGE",
                           f"{size} bytes; the limit is {MAX_FILE_BYTES // (1024 * 1024)} MB")
        meta = envelope.clean_meta(params)
        session = str(params.get("session") or "").strip()[:64] or None
        from_dir = str(params.get("session_dir") or "").strip()[:512] or None
        uid, now = str(_uuid.uuid4()), int(time.time())
        digest = _sha256(path)
        kind = _classify_media(path.name)
        self.db.execute(
            "INSERT INTO messages (msg_uuid, contact_id, direction, msg_type, content, created_at, "
            "status, from_session, sha256, thread, reply_to, kind, needs_human, tags, room_id, "
            "room_msg_id, relayed, from_dir) "
            "VALUES (?, ?, 'out', ?, ?, ?, 'queued', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (uid, contact["id"], kind, str(path.resolve()), now, session, digest,
             *_meta_values(meta), from_dir),
        )
        self.db.commit()
        self._auto_done(contact["id"], meta.get("reply_to"))
        fn = contact["friend_number"]
        started = False
        if fn is not None and self.tox.friend_get_connection_status(fn) != 0:
            row = self.db.execute("SELECT * FROM messages WHERE msg_uuid=?", (uid,)).fetchone()
            started = self._start_file_transfer(row, fn)
        return {"msg_uuid": uid, "status": "sent" if started else "queued", "msg_type": kind,
                "name": path.name, "size": size, "sha256": digest}

    def _start_file_transfer(self, row, friend_number: int) -> bool:
        """Announce the file's metadata, then offer the transfer. False if it could
        not start (the row stays queued, or is failed when the file is gone)."""
        path = Path(row["content"])
        if not path.is_file():
            self.db.execute("UPDATE messages SET status='failed' WHERE msg_uuid=?", (row["msg_uuid"],))
            self.db.commit()
            log.warning("file to send is gone: %s", path)
            return False
        size = path.stat().st_size
        data = {"msg_uuid": row["msg_uuid"], "name": path.name, "size": size,
                "sha256": row["sha256"], "from_session": row["from_session"], **_row_meta(row)}
        self.tox.friend_send_message(friend_number, envelope.encode(envelope.make_file_meta(data)))
        number = self.tox.file_send(friend_number, FILE_KIND_DATA, size, path.name.encode("utf-8"))
        if number == NO_FILE:
            return False
        try:
            fh = open(path, "rb")
        except OSError:
            log.exception("could not open %s for sending", path)
            self.tox.file_control(friend_number, number, FILE_CONTROL_CANCEL)
            return False
        self._out[(friend_number, number)] = {"fh": fh, "msg_uuid": row["msg_uuid"],
                                              "size": size, "path": str(path)}
        self.db.execute("UPDATE messages SET status='sent', last_attempt_at=? WHERE msg_uuid=?",
                        (int(time.time()), row["msg_uuid"]))
        self.db.commit()
        log.info("sending %s (%d bytes) to friend %d", path.name, size, friend_number)
        return True

    def _on_file_chunk_request(self, friend_number: int, file_number: int, position: int,
                               length: int) -> None:
        key = (friend_number, file_number)
        tr = self._out.get(key)
        if tr is None:
            return
        if length == 0:  # the receiver has the whole file
            self._finish_outgoing(key, "delivered")
            return
        try:
            tr["fh"].seek(position)
            data = tr["fh"].read(length)
        except OSError:
            log.exception("could not read %s while sending", tr["path"])
            self.tox.file_control(friend_number, file_number, FILE_CONTROL_CANCEL)
            self._finish_outgoing(key, "failed")
            return
        self.tox.file_send_chunk(friend_number, file_number, position, data)

    def _finish_outgoing(self, key: tuple[int, int], status: str) -> None:
        tr = self._out.pop(key, None)
        if tr is None:
            return
        try:
            tr["fh"].close()
        except OSError:
            pass
        now = int(time.time())
        if status == "delivered":
            self.db.execute("UPDATE messages SET status='delivered', delivered_at=? "
                            "WHERE msg_uuid=? AND status='sent'", (now, tr["msg_uuid"]))
        elif status == "queued":  # link dropped: start over when they are back
            self.db.execute("UPDATE messages SET status='queued' WHERE msg_uuid=? AND status='sent'",
                            (tr["msg_uuid"],))
        else:
            self.db.execute("UPDATE messages SET status='failed' WHERE msg_uuid=?", (tr["msg_uuid"],))
        self.db.commit()
        alias = self._alias_of(key[0])
        if status == "delivered":
            self._emit("delivery", {"msg_uuid": tr["msg_uuid"], "alias": alias}, alias)
        elif status == "failed":
            self._emit("send.failed", {"msg_uuid": tr["msg_uuid"], "alias": alias,
                                       "reason": "file transfer cancelled by the receiver"}, alias)

    def _abort_outgoing(self, friend_number: int) -> None:
        for key in [k for k in self._out if k[0] == friend_number]:
            self._finish_outgoing(key, "queued")

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
        self._post_webhook(msgs)
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

    def _post_webhook(self, msgs: list) -> None:
        """`[hooks] on_message_url`: POST {from, count, messages, ts}. Bodies and
        thread fields ride along only with `include_body = true` (decision 8);
        with `on_message_secret` the request carries
        X-Toxi-Signature: sha256=HMAC(secret, "<ts>.<body>")."""
        cfg = config.webhook_config()
        if not cfg:
            return
        items = []
        for m in msgs:
            item = dict(m)
            if cfg["include_body"]:
                full = self._get_message({"msg_uuid": m["msg_uuid"]}).get("message") or {}
                item.update({k: full.get(k) for k in (
                    "content", "from_session", "thread", "reply_to", "kind", "needs_human",
                    "tags", "room_id", "created_at") if k in full})
            items.append(item)
        ts = str(int(time.time()))
        body = json.dumps({"from": list(dict.fromkeys(m["alias"] for m in msgs)),
                           "count": len(msgs), "messages": items, "ts": int(ts)},
                          ensure_ascii=False).encode("utf-8")
        headers = {"Content-Type": "application/json", "User-Agent": "toxi-daemon",
                   "X-Toxi-Timestamp": ts}
        if cfg["secret"]:
            digest = hmac.new(cfg["secret"].encode("utf-8"), ts.encode() + b"." + body,
                              hashlib.sha256).hexdigest()
            headers["X-Toxi-Signature"] = f"sha256={digest}"
        threading.Thread(target=_post_json, args=(cfg["url"], body, headers), daemon=True).start()

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
        cfg = config.tox_config()
        nodes = (DEFAULT_BOOTSTRAP if cfg["public_dht"] else []) + cfg["bootstrap"]
        for host, port, key in nodes:
            try:
                self.tox.bootstrap(host, port, key)
            except ValueError:
                log.warning("bad bootstrap node %s:%s", host, port)
        if not cfg["public_dht"]:
            log.info("public DHT disabled; %d private bootstrap node(s)", len(cfg["bootstrap"]))

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
        self._sel.register(s, selectors.EVENT_READ, data=None)
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
                self._tick_parts()
                if time.time() - self._last_sweep > SWEEP_INTERVAL:
                    self._retry_sweep()
                    self._archive_tick()
                    self._last_sweep = time.time()
                timeout = min(self.tox.iteration_interval() / 1000, 0.1)
                for key, _ in self._sel.select(timeout):
                    if key.data is None:  # the listening socket
                        try:
                            conn, _ = self.sock.accept()
                        except OSError:
                            self.running = False
                            break
                        self._handle(conn)
                    else:  # a subscriber socket
                        self._drain_subscriber(key.data)
        finally:
            self._cleanup()

    def _handle(self, conn: socket.socket) -> None:
        conn.settimeout(5.0)
        keep = False
        try:
            req = ipc.recv_message(conn)
            if req.get("method") == "subscribe":
                self._subscribe(conn, req)
                keep = True
            else:
                ipc.send_message(conn, self._dispatch(req))
        except Exception:
            log.exception("error handling request")
        finally:
            if not keep:
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
            for sub in list(self._subs):
                self._drop_subscriber(sub)
            if self.sock:
                self.sock.close()
            self._sel.close()
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
            "dht_id": self.tox.self_get_dht_id_hex(),
            "udp_port": self.tox.self_get_udp_port(),
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
            "version": __version__,
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

    def _rename_contact(self, params: dict) -> dict:
        old = (params.get("alias") or "").strip()
        new = (params.get("new_alias") or "").strip()
        if not old or not new:
            raise RpcError("INVALID_ALIAS", "both the current and the new alias are required")
        if not self.db.execute("SELECT 1 FROM contacts WHERE alias=?", (old,)).fetchone():
            raise RpcError("CONTACT_NOT_FOUND", f"no contact named {old!r}")
        if self.db.execute("SELECT 1 FROM contacts WHERE alias=?", (new,)).fetchone():
            raise RpcError("CONTACT_EXISTS", f"a contact named {new!r} already exists")
        self.db.execute("UPDATE contacts SET alias=? WHERE alias=?", (new, old))
        self.db.commit()
        return {"alias": new, "was": old}

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
            "SELECT id, friend_number, is_online, profile_json FROM contacts WHERE alias=?",
            (alias,),
        ).fetchone()
        if contact is None:
            raise RpcError("CONTACT_NOT_FOUND", f"no contact named {alias!r}")
        size = len(body.encode("utf-8"))
        if size > MAX_BODY_BYTES:
            # Too big for a text: ship it as a file (one transfer, one ack) instead.
            out = paths.ensure_media_dir() / "outgoing"
            out.mkdir(exist_ok=True)
            os.chmod(out, 0o700)
            p = out / f"{_uuid.uuid4()}.txt"
            p.write_text(body, encoding="utf-8")
            os.chmod(p, 0o600)
            res = self._send_file(dict(params, path=str(p)))
            res["as_file"] = str(p)
            return res

        session = str(params.get("session") or "").strip()[:64] or None
        from_dir = str(params.get("session_dir") or "").strip()[:512] or None
        meta = envelope.clean_meta(params)
        fn = contact["friend_number"]
        # Presence straight from toxcore: the DB flag can lag behind a dropped link.
        online = fn is not None and self.tox.friend_get_connection_status(fn) != 0
        if bool(contact["is_online"]) != online:
            self.db.execute("UPDATE contacts SET is_online=? WHERE id=?",
                            (1 if online else 0, contact["id"]))
        # A body too long for one Tox message goes out in parts: reassembled by
        # peers that advertise the `part` cap, "[i/n] "-prefixed for older ones.
        use_parts = "part" in _caps(contact)
        chunks = envelope.split_text(body, MAX_MESSAGE_LENGTH, session, parts=use_parts,
                                     meta=meta)
        part_id = str(_uuid.uuid4()) if use_parts and len(chunks) > 1 else None
        uuids = []
        for i, chunk in enumerate(chunks, 1):
            part = {"id": part_id, "i": i, "n": len(chunks)} if part_id else None
            env = envelope.make_text(chunk, session, part, meta)
            # Persist first (status 'queued'), then try to send (PRD §4.3.3).
            self.db.execute(
                "INSERT INTO messages (msg_uuid, contact_id, direction, msg_type, content, "
                "created_at, status, from_session, part_id, part_i, part_n, "
                "thread, reply_to, kind, needs_human, tags, room_id, room_msg_id, relayed, from_dir) "
                "VALUES (?, ?, 'out', 'text', ?, ?, 'queued', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (env["uuid"], contact["id"], chunk, env["ts"], session, part_id,
                 i if part_id else None, len(chunks) if part_id else None,
                 *_meta_values(meta), from_dir),
            )
            self.db.commit()
            uuids.append(env["uuid"])
            # Once one part is queued, queue the rest too so they stay in order.
            online = online and self.tox.friend_send_message(fn, envelope.encode(env))
            if online:
                self.db.execute(
                    "UPDATE messages SET status='sent', last_attempt_at=? WHERE msg_uuid=?",
                    (env["ts"], env["uuid"]),
                )
                self.db.commit()
        if not online and fn is not None and self.tox.friend_get_connection_status(fn) != 0:
            self._emit("send.failed", {"msg_uuid": part_id or uuids[0], "alias": alias,
                                       "reason": "toxcore refused the message; queued for retry"},
                       alias)
        self._auto_done(contact["id"], meta.get("reply_to"))
        res = {"msg_uuid": part_id or uuids[0], "status": "sent" if online else "queued"}
        if len(uuids) > 1:
            res.update(parts=len(uuids), msg_uuids=uuids)
        return res

    def _get_messages(self, params: dict) -> dict:
        conds, args = [], []
        if params.get("alias"):
            conds.append("c.alias=?")
            args.append(params["alias"])
        if params.get("thread"):
            conds.append("m.thread=?")
            args.append(params["thread"])
        if params.get("room"):
            conds.append("m.room_id=?")
            args.append(self._resolve_room(params["room"])["room_id"])
        if params.get("unread_only"):
            conds.append("m.direction='in' AND m.read_at IS NULL")
        base = ("FROM messages m JOIN contacts c ON c.id=m.contact_id "
                "LEFT JOIN rooms r ON r.room_id=m.room_id")
        where = (" WHERE " + " AND ".join(conds)) if conds else ""
        # `limit` counts logical messages: the parts of one long text and the
        # fan-out copies of one room message share a key and come back together.
        key = ("CASE WHEN m.direction='out' AND m.room_msg_id IS NOT NULL THEN 'r:'||m.room_msg_id "
               "WHEN m.direction='out' AND m.part_id IS NOT NULL THEN 'p:'||m.part_id "
               "ELSE 'u:'||m.msg_uuid END")
        keys = [k[0] for k in self.db.execute(
            f"SELECT {key} AS lk {base}{where} GROUP BY lk "
            f"ORDER BY MAX(m.created_at) DESC, MAX(m.id) DESC LIMIT ?",
            (*args, int(params.get("limit", 20)))).fetchall()]
        if not keys:
            return {"messages": []}
        qs = ",".join("?" * len(keys))
        query = ("SELECT m.msg_uuid, c.alias, m.direction, m.msg_type, m.content, m.created_at, "
                 "m.status, m.read_at, m.handled_at, m.from_session, m.part_id, m.part_i, m.part_n, "
                 "m.thread, m.reply_to, m.kind, m.needs_human, m.tags, m.room_id, m.room_msg_id, "
                 "m.relayed, r.name AS room_name, c.profile_json, " + _ROUTE_SQL +
                 f"{base}{where}{' AND' if conds else ' WHERE'} {key} IN ({qs}) "
                 "ORDER BY m.created_at DESC, m.id DESC")
        rows = [_with_profile(r) for r in self.db.execute(query, (*args, *keys)).fetchall()]
        return {"messages": _merge_room_sends(_merge_parts(rows))}

    def _get_message(self, params: dict) -> dict:
        cols = ("SELECT m.msg_uuid, c.alias, m.direction, m.msg_type, m.content, m.created_at, "
                "m.status, m.read_at, m.handled_at, m.from_session, m.part_id, m.part_i, m.part_n, "
                "m.thread, m.reply_to, m.kind, m.needs_human, m.tags, m.room_id, m.room_msg_id, "
                "m.relayed, r.name AS room_name, c.profile_json, " + _ROUTE_SQL +
                "FROM messages m "
                "JOIN contacts c ON c.id=m.contact_id LEFT JOIN rooms r ON r.room_id=m.room_id ")
        rows = self.db.execute(cols + "WHERE m.msg_uuid=?", (params.get("msg_uuid"),)).fetchall()
        if not rows:  # the id of a multi-part text we sent: its parts share it
            rows = self.db.execute(cols + "WHERE m.part_id=? ORDER BY m.part_i DESC",
                                   (params.get("msg_uuid"),)).fetchall()
        merged = _merge_parts([_with_profile(r) for r in rows])
        return {"message": merged[0] if merged else None}

    def _mark_read(self, params: dict) -> dict:
        now = int(time.time())
        uuids = params.get("msg_uuids", [])
        marked = []
        for u in uuids:
            cur = self.db.execute(
                "UPDATE messages SET read_at=?, status='read' "
                "WHERE msg_uuid=? AND direction='in' AND read_at IS NULL",
                (now, u),
            )
            if cur.rowcount:
                marked.append(u)
        self.db.commit()
        self._send_receipts(marked)
        return {"marked": len(marked)}

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

    def _list_threads(self, params: dict) -> dict:
        """Threads seen with a contact (or all): message count, unread, last activity."""
        conds, args = ["m.thread IS NOT NULL"], []
        if params.get("alias"):
            conds.append("c.alias=?")
            args.append(params["alias"])
        rows = self.db.execute(
            "SELECT c.alias, m.thread, COUNT(*) AS count, MAX(m.created_at) AS last_at, "
            "SUM(CASE WHEN m.direction='in' AND m.read_at IS NULL THEN 1 ELSE 0 END) AS unread, "
            "SUM(CASE WHEN m.needs_human=1 AND m.direction='in' AND m.read_at IS NULL "
            "THEN 1 ELSE 0 END) AS needs_human, "
            "SUM(CASE WHEN m.direction='out' AND m.kind='request' AND m.status NOT IN "
            "('handled','failed') THEN 1 ELSE 0 END) AS open_requests "
            "FROM messages m JOIN contacts c ON c.id=m.contact_id WHERE " + " AND ".join(conds) +
            " GROUP BY c.alias, m.thread ORDER BY last_at DESC LIMIT ?",
            (*args, int(params.get("limit", 50))),
        ).fetchall()
        return {"threads": [dict(r) for r in rows]}

    # --- archive & search (design backlog 9) ---
    def _archive_path(self):
        return paths.config_dir() / "archive.db"

    def _archive_messages(self, params: dict) -> dict:
        """Move (or delete) delivered/received messages older than N days into
        archive.db (same columns plus the contact alias). Undelivered rows stay."""
        days = int(params.get("older_than_days") or 0)
        if days <= 0:
            raise RpcError("INVALID_DAYS", "older_than_days must be a positive number")
        cutoff = int(time.time()) - days * 86400
        conds, args = ["m.created_at<?", "m.status NOT IN ('queued','sent')"], [cutoff]
        if params.get("alias"):
            conds.append("c.alias=?")
            args.append(params["alias"])
        where = " AND ".join(conds)
        count = self.db.execute(
            f"SELECT COUNT(*) AS c FROM messages m JOIN contacts c ON c.id=m.contact_id WHERE {where}",
            args).fetchone()["c"]
        res = {"matched": count, "cutoff": cutoff, "archive": str(self._archive_path()),
               "dry_run": bool(params.get("dry_run")), "deleted": bool(params.get("delete"))}
        if params.get("dry_run") or not count:
            return res
        if not params.get("delete"):
            ap = str(self._archive_path())
            self.db.execute("ATTACH DATABASE ? AS arc", (ap,))
            try:
                self.db.execute("CREATE TABLE IF NOT EXISTS arc.messages AS "
                                "SELECT c.alias AS alias, m.* FROM messages m "
                                "JOIN contacts c ON c.id=m.contact_id WHERE 0")
                self.db.execute(f"INSERT OR IGNORE INTO arc.messages SELECT c.alias AS alias, m.* "
                                f"FROM messages m JOIN contacts c ON c.id=m.contact_id WHERE {where}", args)
                self.db.commit()
            finally:
                self.db.execute("DETACH DATABASE arc")
            os.chmod(ap, 0o600)
        self.db.execute(f"DELETE FROM messages WHERE id IN (SELECT m.id FROM messages m "
                        f"JOIN contacts c ON c.id=m.contact_id WHERE {where})", args)
        self.db.commit()
        return res

    def _archive_status(self, params: dict) -> dict:
        row = self.db.execute("SELECT COUNT(*) AS n, MIN(created_at) AS oldest FROM messages").fetchone()
        ap = self._archive_path()
        archived = 0
        if ap.exists():
            try:
                self.db.execute("ATTACH DATABASE ? AS arc", (str(ap),))
                try:
                    archived = self.db.execute("SELECT COUNT(*) AS n FROM arc.messages").fetchone()["n"]
                finally:
                    self.db.execute("DETACH DATABASE arc")
            except Exception:  # an archive from a foreign schema: report what we can
                archived = -1
        return {"messages": row["n"], "oldest": row["oldest"],
                "db_bytes": paths.db_path().stat().st_size if paths.db_path().exists() else 0,
                "archived": archived, "archive_bytes": ap.stat().st_size if ap.exists() else 0,
                "auto_after_days": config.archive_after_days()}

    def _archive_tick(self) -> None:
        days = config.archive_after_days()
        if not days or time.time() - self._last_archive < ARCHIVE_INTERVAL:
            return
        self._last_archive = time.time()
        try:
            res = self._archive_messages({"older_than_days": days})
            if res["matched"]:
                log.info("archived %d message(s) older than %d days", res["matched"], days)
        except Exception:
            log.exception("auto-archive failed")

    def _search_messages(self, params: dict) -> dict:
        q = str(params.get("q") or "").strip()
        if not q:
            raise RpcError("INVALID_QUERY", "a search string is required")
        limit = int(params.get("limit") or 20)
        like = f"%{q.lower()}%"
        conds, args = ["LOWER(m.content) LIKE ?"], [like]
        if params.get("alias"):
            conds.append("c.alias=?")
            args.append(params["alias"])
        if params.get("thread"):
            conds.append("m.thread=?")
            args.append(params["thread"])
        where = " AND ".join(conds)
        rows = self.db.execute(
            f"SELECT m.msg_uuid, c.alias, m.direction, m.msg_type, m.content, m.created_at, "
            f"m.status, m.thread, m.kind, m.room_id FROM messages m JOIN contacts c ON c.id=m.contact_id "
            f"WHERE {where} ORDER BY m.created_at DESC LIMIT ?", (*args, limit)).fetchall()
        out = [dict(r, archived=False) for r in rows]
        ap = self._archive_path()
        if params.get("archived") and ap.exists() and len(out) < limit:
            self.db.execute("ATTACH DATABASE ? AS arc", (str(ap),))
            try:
                aconds = ["LOWER(content) LIKE ?"] + (["alias=?"] if params.get("alias") else []) \
                    + (["thread=?"] if params.get("thread") else [])
                arows = self.db.execute(
                    f"SELECT msg_uuid, alias, direction, msg_type, content, created_at, status, thread, "
                    f"kind, room_id FROM arc.messages WHERE {' AND '.join(aconds)} "
                    f"ORDER BY created_at DESC LIMIT ?", (*args, limit - len(out))).fetchall()
                out += [dict(r, archived=True) for r in arows]
            finally:
                self.db.execute("DETACH DATABASE arc")
        for m in out:
            m["kind"] = m["kind"] or "msg"
        return {"count": len(out), "messages": out}

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


def hold_instance_lock(attempts: int = 30):
    """Take this identity's daemon lock and keep it for the life of the process.

    Two daemons on one identity run the same Tox key twice: they knock each
    other offline, and both write the same database. The socket probe alone
    cannot prevent that, because a busy daemon can miss its 5 s answer. The
    lock is held by the kernel, so a busy or hung daemon still owns it. A few
    retries cover a client that probes the lock at the same moment.
    """
    import fcntl
    paths.ensure_config_dir()
    f = open(paths.lock_path(), "a")
    for _ in range(attempts):
        try:
            fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
            return f
        except BlockingIOError:
            time.sleep(0.1)
    f.close()
    raise SystemExit(f"daemon already running for {paths.config_dir()}")


def main() -> None:
    lock = hold_instance_lock()  # noqa: F841 — held until the process exits
    try:
        Daemon().run()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
