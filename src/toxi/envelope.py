"""Application-layer message envelope carried inside a Tox message (PRD §4.2.3).

Tox only moves opaque bytes between friends; all structure lives here as JSON:
    {"v": 2, "uuid": "...", "type": "text", "ts": 1716700000, "data": {...}}

v2 (design doc §5) adds the `profile` type and optional text fields such as
`from_session`; v1 peers ignore both, so the two versions interoperate.
"""
import hashlib
import json
import re
import time
import uuid as _uuid

VERSION = 2
# What this daemon implements; advertised in its profile so peers can feature-gate:
# profile cards, from_session labels, read receipts, multi-part reassembly.
CAPS = ["profile", "session", "receipt", "part", "task"]

PROFILE_KEYS = ("kind", "name", "runtime", "model", "host", "operator", "role", "proto", "caps")
PROFILE_KINDS = ("agent", "human", "service")

# A message id as toxi stores it: it goes into commands an agent runs (`toxi show`,
# `toxi mark-read`), so it never starts with "-" and holds no space or shell char.
ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,63}")


def safe_id(raw) -> str:
    """A sender-chosen id as stored: as is when it is a plain id, else a stable
    digest of it (so a resend still dedupes)."""
    s = str(raw or "")[:64]
    return s if ID.fullmatch(s) else "h-" + hashlib.sha256(s.encode("utf-8", "replace")).hexdigest()[:32]


# Structured fields a text may carry besides its body (design §4.5).
KINDS = ("msg", "request", "report", "ack", "relay", "bounce")
META_KEYS = ("thread", "reply_to", "kind", "needs_human", "tags", "room", "room_msg", "relayed", "state")
# Task states, as A2A 1.0 names them (design §13.11). A text that sets one
# answers a request (`reply_to`); `submitted`, plain `working` and `completed`
# also follow from receipts, so they need no text.
STATES = ("submitted", "working", "input-required", "auth-required",
          "completed", "failed", "canceled", "rejected")
WORKER_STATES = ("working", "input-required", "auth-required", "completed", "failed", "rejected")
TERMINAL = ("completed", "failed", "canceled", "rejected")


def clean_meta(raw) -> dict:
    """Validated, bounded thread/reply_to/kind/needs_human/tags; {} when none set."""
    if not isinstance(raw, dict):
        return {}
    out: dict = {}
    thread = str(raw.get("thread") or "").strip()[:64]
    if thread:
        out["thread"] = thread
    reply_to = str(raw.get("reply_to") or "").strip()[:64]
    if reply_to:
        out["reply_to"] = reply_to
    kind = str(raw.get("kind") or "").strip().lower()
    if kind in KINDS and kind != "msg":
        out["kind"] = kind
    if raw.get("needs_human"):
        out["needs_human"] = True
    state = str(raw.get("state") or "").strip().lower()
    if state in STATES and reply_to:
        out["state"] = state
    tags = raw.get("tags")
    if isinstance(tags, list):
        tags = [str(t).strip()[:24] for t in tags[:8] if str(t).strip()]
        if tags:
            out["tags"] = tags
    for k in ("room", "room_msg"):  # app-layer rooms (design §4.5): room id, shared msg id
        v = str(raw.get(k) or "").strip()[:64]
        if v:
            out[k] = v
    rel = raw.get("relayed")  # room history: who originally wrote this, and when
    if isinstance(rel, dict) and rel.get("name"):
        try:
            out["relayed"] = {"name": str(rel["name"])[:64], "ts": int(rel.get("ts") or 0)}
        except (TypeError, ValueError):
            pass
    return out


def make_file_meta(data: dict) -> dict:
    """What is about to arrive as a Tox file transfer: name, size, sha256, the
    sender's msg_uuid, plus thread/kind/... (design §4.6)."""
    return {"v": VERSION, "uuid": str(_uuid.uuid4()), "type": "file", "ts": int(time.time()),
            "data": data}


def make_room(room_id: str, name: str, members: list) -> dict:
    """Room card: who is in a room. `members` = [{"pk": <64 hex>, "name": str}, ...]."""
    return {"v": VERSION, "uuid": str(_uuid.uuid4()), "type": "room", "ts": int(time.time()),
            "data": {"op": "create", "id": room_id, "name": name, "members": members}}


def clean_room(raw) -> dict | None:
    """Validated room card data, or None."""
    if not isinstance(raw, dict):
        return None
    rid = str(raw.get("id") or "").strip()[:64]
    name = str(raw.get("name") or "").strip()[:64]
    members = []
    for m in (raw.get("members") or [])[:64]:
        if not isinstance(m, dict):
            continue
        pk = str(m.get("pk") or "").strip().upper()
        if len(pk) == 64 and all(c in "0123456789ABCDEF" for c in pk):
            members.append({"pk": pk, "name": str(m.get("name") or "").strip()[:64]})
    if not rid or not name or not members:
        return None
    return {"id": rid, "name": name, "members": members}


def text_envelope(uuid: str, ts: int, body: str, session: str | None = None,
                  part: dict | None = None, meta: dict | None = None) -> dict:
    data = {"body": body}
    if session:
        data["from_session"] = session
    if part:
        data["part"] = {"id": part["id"], "i": part["i"], "n": part["n"]}
    if meta:
        data.update(clean_meta(meta))
    return {"v": VERSION, "uuid": uuid, "type": "text", "ts": ts, "data": data}


def make_text(body: str, session: str | None = None, part: dict | None = None,
              meta: dict | None = None) -> dict:
    return text_envelope(str(_uuid.uuid4()), int(time.time()), body, session, part, meta)


def make_receipt(ref_uuid: str, kind: str = "read") -> dict:
    return {"v": VERSION, "uuid": str(_uuid.uuid4()), "type": "receipt", "ts": int(time.time()),
            "data": {"ref_uuid": ref_uuid, "kind": kind}}


def make_profile(profile: dict) -> dict:
    return {"v": VERSION, "uuid": str(_uuid.uuid4()), "type": "profile",
            "ts": int(time.time()), "data": profile}


def clean_profile(raw) -> dict:
    """Keep only the known profile fields, each bounded in size; {} if unusable."""
    if not isinstance(raw, dict):
        return {}
    out: dict = {}
    for k in PROFILE_KEYS:
        v = raw.get(k)
        if v is None:
            continue
        if k == "proto":
            try:
                out[k] = int(v)
            except (TypeError, ValueError):
                pass
        elif k == "caps":
            if isinstance(v, list):
                out[k] = [str(c)[:32] for c in v[:16]]
        else:
            s = str(v).strip()[:120]
            if s:
                out[k] = s
    if out.get("kind") not in PROFILE_KINDS:
        out.pop("kind", None)
    sessions = clean_sessions(raw.get("sessions"))
    if sessions:
        out["sessions"] = sessions
    return out


def clean_sessions(raw) -> list:
    """The session directory on a card (design §13.6, C8): the sessions running
    on that identity, so a peer can address one with `@name`.
    [{name ≤64, about ≤120}], at most 16."""
    out = []
    for s in raw[:16] if isinstance(raw, list) else []:
        name = str(s.get("name") or "").strip()[:64] if isinstance(s, dict) else ""
        if name:
            about = str(s.get("about") or "").strip()[:120]
            out.append({"name": name, **({"about": about} if about else {})})
    return out


def make_ack(ref_uuid: str) -> dict:
    return {
        "v": VERSION,
        "uuid": str(_uuid.uuid4()),
        "type": "ack",
        "ts": int(time.time()),
        "data": {"ref_uuid": ref_uuid},
    }


def make_contact_share(tox_id: str, suggested_alias: str, note: str = "") -> dict:
    return {
        "v": VERSION,
        "uuid": str(_uuid.uuid4()),
        "type": "contact_share",
        "ts": int(time.time()),
        "data": {"tox_id": tox_id, "suggested_alias": suggested_alias, "note": note},
    }


def encode(envelope: dict) -> bytes:
    # Keep non-ASCII as raw UTF-8 (3 bytes for a CJK char) instead of \uXXXX (6).
    return json.dumps(envelope, ensure_ascii=False).encode("utf-8")


_PART_STUB = {"id": "x" * 36, "i": 99999, "n": 99999}  # worst-case size of a `part` field


def split_text(body: str, limit: int, session: str | None = None, parts: bool = False,
               meta: dict | None = None) -> list[str]:
    """Split `body` into chunks whose text envelopes each encode to <= `limit` bytes.

    A body that fits is returned as is. Otherwise, with `parts=False` (v1 peers)
    each chunk gets an "[i/n] " prefix; with `parts=True` chunks are returned
    bare and the caller sends them with a `part` field for reassembly. Splits
    fall on character boundaries. `meta` (thread etc.) rides on every chunk and
    is budgeted for.
    """
    if len(encode(make_text(body, session, None, meta))) <= limit:
        return [body]
    if parts:
        budget = limit - len(encode(make_text("", session, _PART_STUB, meta)))
    else:
        budget = limit - len(encode(make_text("", session, None, meta))) - len("[999/999] ")
    chunks, cur, size = [], "", 0
    for ch in body:
        cost = len(json.dumps(ch, ensure_ascii=False).encode("utf-8")) - 2
        if cur and size + cost > budget:
            chunks.append(cur)
            cur, size = "", 0
        cur += ch
        size += cost
    chunks.append(cur)
    if parts:
        return chunks
    return [f"[{i}/{len(chunks)}] {p}" for i, p in enumerate(chunks, 1)]


def parse(raw: bytes) -> dict:
    """Decode a received Tox message. Anything that isn't our JSON envelope
    (e.g. a plain message from another Tox client) is wrapped as a text message."""
    try:
        env = json.loads(raw.decode("utf-8"))
        if isinstance(env, dict) and "type" in env:
            env.setdefault("uuid", str(_uuid.uuid4()))
            # ids are capped at 64 chars everywhere (dedup, reply_to, receipts), so a
            # peer with longer ids still gets consistent matching. The ACK names the
            # id the sender used; toxi keeps a safe form of it.
            raw_id = str(env["uuid"])[:64]
            env["uuid"] = safe_id(raw_id)
            if env["uuid"] != raw_id:
                env["ack_uuid"] = raw_id
            env.setdefault("ts", int(time.time()))
            return env
    except (json.JSONDecodeError, UnicodeDecodeError):
        pass
    return {
        "v": VERSION,
        "uuid": str(_uuid.uuid4()),
        "type": "text",
        "ts": int(time.time()),
        "data": {"body": raw.decode("utf-8", "replace")},
    }
