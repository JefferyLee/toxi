"""Application-layer message envelope carried inside a Tox message (PRD §4.2.3).

Tox only moves opaque bytes between friends; all structure lives here as JSON:
    {"v": 2, "uuid": "...", "type": "text", "ts": 1716700000, "data": {...}}

v2 (design doc §5) adds the `profile` type and optional text fields such as
`from_session`; v1 peers ignore both, so the two versions interoperate.
"""
import json
import time
import uuid as _uuid

VERSION = 2
# What this daemon implements; advertised in its profile so peers can feature-gate.
CAPS = ["profile", "session"]

PROFILE_KEYS = ("kind", "name", "runtime", "model", "host", "operator", "role", "proto", "caps")
PROFILE_KINDS = ("agent", "human", "service")


def text_envelope(uuid: str, ts: int, body: str, session: str | None = None) -> dict:
    data = {"body": body}
    if session:
        data["from_session"] = session
    return {"v": VERSION, "uuid": uuid, "type": "text", "ts": ts, "data": data}


def make_text(body: str, session: str | None = None) -> dict:
    return text_envelope(str(_uuid.uuid4()), int(time.time()), body, session)


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


def split_text(body: str, limit: int, session: str | None = None) -> list[str]:
    """Split `body` into parts whose text envelopes each encode to <= `limit` bytes.

    A body that fits is returned as is. Otherwise each part gets an "[i/n] "
    prefix; splits fall on character boundaries.
    """
    if len(encode(make_text(body, session))) <= limit:
        return [body]
    budget = limit - len(encode(make_text("", session))) - len("[999/999] ")
    parts, cur, size = [], "", 0
    for ch in body:
        cost = len(json.dumps(ch, ensure_ascii=False).encode("utf-8")) - 2
        if cur and size + cost > budget:
            parts.append(cur)
            cur, size = "", 0
        cur += ch
        size += cost
    parts.append(cur)
    return [f"[{i}/{len(parts)}] {p}" for i, p in enumerate(parts, 1)]


def parse(raw: bytes) -> dict:
    """Decode a received Tox message. Anything that isn't our JSON envelope
    (e.g. a plain message from another Tox client) is wrapped as a text message."""
    try:
        env = json.loads(raw.decode("utf-8"))
        if isinstance(env, dict) and "type" in env:
            env.setdefault("uuid", str(_uuid.uuid4()))
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
