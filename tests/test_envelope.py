from toxi import envelope


def test_text_roundtrip():
    body = "héllo 🌍 bob"  # intentionally multibyte UTF-8
    env = envelope.make_text(body)
    parsed = envelope.parse(envelope.encode(env))
    assert parsed["type"] == "text"
    assert parsed["data"]["body"] == body
    assert parsed["uuid"] == env["uuid"]


def test_parse_non_json_is_treated_as_text():
    parsed = envelope.parse(b"just a plain message")
    assert parsed["type"] == "text"
    assert parsed["data"]["body"] == "just a plain message"
    assert parsed["uuid"]  # a uuid is synthesized


def test_parse_caps_uuid_at_64_chars():
    long_id = "A" * 64 + ":12345"
    parsed = envelope.parse(('{"type":"text","uuid":"%s","data":{"body":"x"}}' % long_id).encode())
    assert parsed["uuid"] == "A" * 64 and envelope.clean_meta({"reply_to": long_id})["reply_to"] == "A" * 64


def test_parse_fills_missing_uuid_and_ts():
    parsed = envelope.parse(b'{"type": "text", "data": {"body": "hi"}}')
    assert parsed["uuid"]
    assert parsed["ts"]
    assert parsed["data"]["body"] == "hi"


def test_v2_text_carries_optional_session():
    assert envelope.VERSION == 2
    assert "from_session" not in envelope.make_text("hi")["data"]
    env = envelope.make_text("hi", "powerup-chess")
    assert env["data"] == {"body": "hi", "from_session": "powerup-chess"}
    assert envelope.parse(envelope.encode(env))["data"]["from_session"] == "powerup-chess"


def test_split_text_budget_accounts_for_session():
    body = "x" * 3000
    parts = envelope.split_text(body, 1372, session="a-long-session-label")
    assert len(parts) > 1
    for part in parts:
        assert len(envelope.encode(envelope.make_text(part, "a-long-session-label"))) <= 1372
    assert "".join(p.split("] ", 1)[1] for p in parts) == body


def test_profile_envelope_and_cleaning():
    env = envelope.make_profile({"kind": "agent", "runtime": "grok"})
    assert env["type"] == "profile" and env["v"] == 2
    assert envelope.parse(envelope.encode(env))["data"]["runtime"] == "grok"

    raw = {"kind": "robot", "name": "n" * 500, "runtime": "codex", "proto": "2",
           "caps": list(range(40)), "secret": "x", "host": ""}
    prof = envelope.clean_profile(raw)
    assert "kind" not in prof            # unknown kind dropped
    assert len(prof["name"]) == 120      # strings bounded
    assert prof["proto"] == 2 and len(prof["caps"]) == 16
    assert "secret" not in prof and "host" not in prof
    assert envelope.clean_profile("junk") == {} and envelope.clean_profile(None) == {}
