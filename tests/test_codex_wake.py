"""The Codex waker (codex_wake.check): which threads it wakes, and how."""
from toxi import codex_wake, sessions


class FakeDaemon:
    def __init__(self, threads):
        self.threads = threads  # thread id -> status type
        self.calls = []

    def call(self, method, params):
        self.calls.append((method, params))
        if method == "thread/loaded/list":
            return {"data": list(self.threads)}
        if method == "thread/read":
            return {"thread": {"cwd": "/w/p", "status": {"type": self.threads[params["threadId"]]}}}
        return {}

    def methods(self):
        return [m for m, _ in self.calls]


def _mail(monkeypatch, peek):
    marked = []
    monkeypatch.setattr(codex_wake.peers, "peek", lambda alias, cwd, sid=None: peek)
    monkeypatch.setattr(codex_wake.client, "request", lambda m, p: marked.append(p["msg_uuids"]))
    return marked


def _ours(sid, pid=4242):
    sessions.register("/w/p", pid=pid, sid=sid)


def test_an_idle_thread_with_waking_mail_gets_a_developer_message_and_a_turn(monkeypatch):
    monkeypatch.setattr(sessions, "_alive", lambda pid: True)
    _ours("t1")
    marked = _mail(monkeypatch, {"text": "«toxi» bob: 6*7?", "msg_uuids": ["m1"], "wake": True})
    d = FakeDaemon({"t1": "idle"})
    assert codex_wake.check(d, 4242, False, {}) == 1
    inject = dict(d.calls)["thread/inject_items"]
    assert inject["items"][0]["role"] == "developer"  # never the user's role
    assert dict(d.calls)["turn/start"] == {"threadId": "t1", "input": []}
    assert marked == [["m1"]]


def test_no_wake_for_a_busy_thread_or_mail_that_does_not_wake(monkeypatch):
    monkeypatch.setattr(sessions, "_alive", lambda pid: True)
    _ours("t1")
    _ours("t2")
    marked = _mail(monkeypatch, {"text": "FYI", "msg_uuids": ["m1"], "wake": False})
    d = FakeDaemon({"t1": "active", "t2": "idle"})
    codex_wake.check(d, 4242, False, {})
    assert "turn/start" not in d.methods() and marked == []


def test_a_thread_the_daemon_let_go_ends_its_session_and_others_are_not_touched(monkeypatch):
    monkeypatch.setattr(sessions, "_alive", lambda pid: True)
    _ours("gone")
    _ours("theirs", pid=999)  # another process: not this waker's
    _mail(monkeypatch, {"text": "", "msg_uuids": [], "wake": False})
    d = FakeDaemon({"theirs": "idle"})
    assert codex_wake.check(d, 4242, False, {}) == 1
    assert [s.get("sid") for s in sessions.live()] == ["theirs"]
    assert ("thread/read", {"threadId": "theirs"}) not in d.calls


def test_wakes_stop_at_the_limit(monkeypatch):
    monkeypatch.setattr(sessions, "_alive", lambda pid: True)
    _ours("t1")
    _mail(monkeypatch, {"text": "ping", "msg_uuids": ["m"], "wake": True})
    woken = {}
    for _ in range(codex_wake.WAKE_LIMIT + 3):
        codex_wake.check(d := FakeDaemon({"t1": "idle"}), 4242, False, woken)
    assert len(woken["t1"]) == codex_wake.WAKE_LIMIT
    assert "turn/start" not in d.methods()
