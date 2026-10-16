"""`toxi pair` / `toxi join` invite codec (design §4.2)."""
import time

import pytest

from toxi import invite


def test_invite_roundtrip():
    data = {"v": 1, "tox_id": "A" * 76, "name": "ada", "nonce": "n" * 32,
            "exp": int(time.time()) + 60}
    text = invite.encode(data)
    assert "=" not in text and text.isascii()
    assert invite.decode(" " + text + "\n") == data


def test_invite_rejects_garbage_and_expired():
    with pytest.raises(invite.InvalidInvite):
        invite.decode("not-an-invite")
    with pytest.raises(invite.InvalidInvite):
        invite.decode(invite.encode({"tox_id": "short", "nonce": "x", "exp": 9e9}))
    with pytest.raises(invite.InvalidInvite, match="expired"):
        invite.decode(invite.encode({"tox_id": "A" * 76, "nonce": "x", "exp": 1}))


def test_request_message_roundtrip_and_foreign_requests():
    msg = invite.request_message("abc", "codex@Ada").encode()
    assert invite.parse_request(msg) == ("abc", "codex@Ada")
    assert invite.parse_request(b"Let's chat on toxi") is None
    assert invite.parse_request(b'{"nonce": "abc"}') is None  # no toxi tag
    assert invite.parse_request(b"\xff\xfe") is None


def test_safe_alias():
    assert invite.safe_alias("codex@Ada") == "codex@Ada"
    assert invite.safe_alias("  Powerupcastle OP!! ") == "Powerupcastle-OP"
    assert invite.safe_alias("///") == "peer"
    assert len(invite.safe_alias("x" * 100)) == 32
