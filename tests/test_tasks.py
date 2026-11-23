"""Design §13.11: the task state of a request, with A2A 1.0 names. A text that
answers a request may set it (`state`); receipts give submitted / working /
completed; only the side that asked may cancel; a task that ended stays ended."""
import pytest

from toxi import envelope, peers
from toxi.daemon import RpcError, _task_state

from test_threads import _daemon  # noqa: E402  (shared fixture: contact `codex`, friend 5)


def _incoming(d, body, **meta):
    env = envelope.make_text(body, meta=meta)
    d._on_friend_message(5, envelope.encode(env), 0)
    return env["uuid"]


def _row(d, uuid):
    return d.db.execute("SELECT * FROM messages WHERE msg_uuid=?", (uuid,)).fetchone()


def _sent(d, type_):
    return [e for _, e in d.tox.sent if e["type"] == type_]


def test_state_is_kept_only_with_a_known_name_and_a_reply_to():
    assert envelope.clean_meta({"state": "input-required", "reply_to": "r1"})["state"] == "input-required"
    assert "state" not in envelope.clean_meta({"state": "input-required"})  # answers nothing
    assert "state" not in envelope.clean_meta({"state": "paused", "reply_to": "r1"})
    assert "task" in envelope.CAPS


def test_the_worker_asks_back_and_the_request_stays_open(tmp_path, monkeypatch):
    d = _daemon(tmp_path, monkeypatch)
    req = _incoming(d, "build it", kind="request", thread="t")
    d._send_message({"alias": "codex", "body": "which branch?", "reply_to": req,
                     "state": "input-required"})
    assert _sent(d, "text")[-1]["data"]["state"] == "input-required"  # on the wire
    assert _row(d, req)["task_state"] == "input-required" and _row(d, req)["handled_at"] is None
    assert not [r for r in _sent(d, "receipt") if r["data"]["kind"] == "done"]  # a question is not done
    d._send_message({"alias": "codex", "body": "built", "reply_to": req})  # no state: completed
    assert _row(d, req)["handled_at"] is not None
    assert [r for r in _sent(d, "receipt") if r["data"]["kind"] == "done"]


def test_a_failed_request_ends_without_a_done_receipt(tmp_path, monkeypatch):
    d = _daemon(tmp_path, monkeypatch)
    req = _incoming(d, "deploy", kind="request")
    d._send_message({"alias": "codex", "body": "the build is red", "reply_to": req, "state": "failed"})
    assert _row(d, req)["task_state"] == "failed" and _row(d, req)["handled_at"] is not None
    # a `done` that arrived before the text would make the sender record `completed`
    assert not [r for r in _sent(d, "receipt") if r["data"]["kind"] == "done"]


def test_the_requester_sees_each_state_and_only_it_may_cancel(tmp_path, monkeypatch):
    d = _daemon(tmp_path, monkeypatch)
    req = d._send_message({"alias": "codex", "body": "review pr 9", "kind": "request"})["msg_uuid"]
    _incoming(d, "on it, 2 of 5 files", reply_to=req, state="working")
    assert _row(d, req)["task_state"] == "working"
    with pytest.raises(RpcError):  # the side that asked cannot set the worker's states
        d._send_message({"alias": "codex", "body": "done", "reply_to": req, "state": "completed"})
    d._send_message({"alias": "codex", "body": "never mind", "reply_to": req, "state": "canceled"})
    assert _row(d, req)["task_state"] == "canceled"
    _incoming(d, "finished anyway", reply_to=req, state="completed")
    assert _row(d, req)["task_state"] == "canceled"  # ended stays ended
    got = d._get_request({"msg_uuid": req})["request"]
    assert got["state"] == "canceled"
    assert [m.get("state") for m in got["replies"]] == ["working", "canceled", None]
    assert req not in [r["msg_uuid"] for r in d._get_request({})["requests"]]


def test_the_worker_cannot_cancel_and_a_done_receipt_completes(tmp_path, monkeypatch):
    d = _daemon(tmp_path, monkeypatch)
    req = d._send_message({"alias": "codex", "body": "lint it", "kind": "request"})["msg_uuid"]
    _incoming(d, "nah", reply_to=req, state="canceled")  # only we may cancel our request
    assert _row(d, req)["task_state"] is None
    d._on_friend_message(5, envelope.encode(envelope.make_receipt(req, "done")), 0)
    assert _row(d, req)["task_state"] == "completed"
    assert d._get_request({"msg_uuid": req})["request"]["state"] == "completed"


def test_states_follow_from_receipts_when_no_text_set_one():
    assert _task_state({"direction": "out", "status": "queued"}) == "submitted"
    assert _task_state({"direction": "out", "status": "delivered"}) == "submitted"
    assert _task_state({"direction": "out", "status": "read"}) == "working"
    assert _task_state({"direction": "out", "status": "bounced"}) == "rejected"
    assert _task_state({"direction": "out", "status": "handled", "handled_at": 1}) == "completed"
    assert _task_state({"direction": "in", "read_at": 1}) == "working"
    assert _task_state({"direction": "in", "read_at": 1, "task_state": "failed"}) == "failed"


def test_a_reply_to_a_message_we_took_goes_back_to_the_session_that_took_it(tmp_path, monkeypatch):
    d = _daemon(tmp_path, monkeypatch)
    req = _incoming(d, "build it", kind="request")
    d._mark_read({"msg_uuids": [req], "session": "worker-2", "session_dir": "/w/p"})
    cancel = _incoming(d, "stop", reply_to=req, state="canceled")
    m = d._get_message({"msg_uuid": cancel})["message"]
    assert (m["route_session"], m["route_dir"], m["state"]) == ("worker-2", "/w/p", "canceled")
    assert _row(d, req)["task_state"] == "canceled"
    assert peers.wakes(dict(m, trust="collaborator"), ["msg"])  # the worker should stop now


def test_an_agent_sees_the_request_id_to_answer_and_the_state(tmp_path, monkeypatch):
    d = _daemon(tmp_path, monkeypatch)
    req = _incoming(d, "build it", kind="request")
    m = d._get_message({"msg_uuid": req})["message"]
    text = peers.as_prompt([dict(m, trust="collaborator")])
    assert f"request id {req}" in text and "input-required" in text  # the id, and how to ask back
    assert "task input-required" in peers.meta_tag({"state": "input-required", "reply_to": req})
