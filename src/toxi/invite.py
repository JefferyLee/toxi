"""`toxi pair` / `toxi join`: a one-time invite so two identities can befriend
each other without copying a 76-char Tox ID or accepting by hand (design §4.2).

The inviter's daemon mints a nonce; the invite string carries its Tox ID, name
and the nonce. The joiner adds the inviter with a friend request whose message
is a small JSON object carrying the nonce back; the inviter's daemon accepts a
request automatically only when it carries a valid, unused, unexpired nonce.
"""
import base64
import json
import re
import time

REQUEST_TAG = "toxi-invite"


class InvalidInvite(ValueError):
    pass


def encode(data: dict) -> str:
    raw = json.dumps(data, separators=(",", ":")).encode("utf-8")
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def decode(text: str) -> dict:
    text = text.strip()
    try:
        raw = base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))
        data = json.loads(raw.decode("utf-8"))
    except (ValueError, UnicodeDecodeError) as e:
        raise InvalidInvite("that is not a toxi invite") from e
    if (not isinstance(data, dict) or len(str(data.get("tox_id", ""))) != 76
            or not data.get("nonce")):
        raise InvalidInvite("that is not a toxi invite")
    if int(data.get("exp") or 0) < time.time():
        raise InvalidInvite("this invite has expired; ask for a fresh `toxi pair`")
    return data


def request_message(nonce: str, name: str) -> str:
    """Friend-request text the joiner sends: the nonce plus the name to file them under."""
    return json.dumps({"toxi": REQUEST_TAG, "nonce": nonce, "name": name}, ensure_ascii=False)


def parse_request(message: bytes) -> tuple[str, str] | None:
    """(nonce, name) if a friend-request message is an invite reply, else None."""
    try:
        data = json.loads(message.decode("utf-8"))
    except (ValueError, UnicodeDecodeError):
        return None
    if isinstance(data, dict) and data.get("toxi") == REQUEST_TAG and data.get("nonce"):
        return str(data["nonce"]), str(data.get("name") or "")
    return None


def safe_alias(name: str) -> str:
    """Turn a peer-supplied name into something usable as a local alias."""
    name = re.sub(r"[^A-Za-z0-9_.@-]+", "-", name.strip()).strip("-.")[:32]
    return name or "peer"
