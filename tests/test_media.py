"""Incoming media (file-transfer) receive path.

Exercises the daemon's file_recv handlers directly with a fake Tox handle, so
no real libtoxcore/DHT is needed.
"""
from pathlib import Path

from toxi import db
from toxi.daemon import Daemon
from toxi.tox import FILE_CONTROL_CANCEL, FILE_CONTROL_RESUME, FILE_KIND_AVATAR, FILE_KIND_DATA


class FakeTox:
    def __init__(self):
        self.controls = []

    def file_control(self, friend_number, file_number, control):
        self.controls.append((friend_number, file_number, control))
        return True


def _daemon(tmp_path, monkeypatch):
    monkeypatch.setenv("TOXI_HOME", str(tmp_path))
    conn = db.connect(tmp_path / "chat.db")
    conn.execute(
        "INSERT INTO contacts (public_key, alias, added_at, friend_number) VALUES (?, ?, ?, ?)",
        ("AA", "bob", 1, 5),
    )
    conn.commit()
    d = object.__new__(Daemon)
    d.db = conn
    d._transfers = {}
    d._hook_msgs, d._hook_due, d._hook_proc, d._hook_started = [], 0.0, None, 0.0
    d.tox = FakeTox()
    return d


def test_receive_image_writes_file_and_row(tmp_path, monkeypatch):
    d = _daemon(tmp_path, monkeypatch)
    d._on_file_recv(5, 0, FILE_KIND_DATA, 11, b"photo.png")
    assert (5, 0, FILE_CONTROL_RESUME) in d.tox.controls
    d._on_file_recv_chunk(5, 0, 0, b"hello ")
    d._on_file_recv_chunk(5, 0, 6, b"world")
    d._on_file_recv_chunk(5, 0, 0, b"")  # completion

    msgs = d._get_messages({"unread_only": True})["messages"]
    assert len(msgs) == 1
    assert msgs[0]["msg_type"] == "image"
    saved = Path(msgs[0]["content"])
    assert saved.read_bytes() == b"hello world"
    assert not saved.with_name(saved.name + ".part").exists()


def test_voice_and_video_classified_by_extension(tmp_path, monkeypatch):
    d = _daemon(tmp_path, monkeypatch)
    for fnum, name in ((0, b"clip.ogg"), (1, b"movie.mp4"), (2, b"notes.bin")):
        d._on_file_recv(5, fnum, FILE_KIND_DATA, 1, name)
        d._on_file_recv_chunk(5, fnum, 0, b"x")
        d._on_file_recv_chunk(5, fnum, 0, b"")
    types = sorted(m["msg_type"] for m in d._get_messages({})["messages"])
    assert types == ["file", "video", "voice"]


def test_avatar_is_rejected(tmp_path, monkeypatch):
    d = _daemon(tmp_path, monkeypatch)
    d._on_file_recv(5, 0, FILE_KIND_AVATAR, 10, b"avatar.png")
    assert (5, 0, FILE_CONTROL_CANCEL) in d.tox.controls
    assert (5, 0) not in d._transfers
    assert d._get_messages({})["messages"] == []


def test_unknown_friend_is_rejected(tmp_path, monkeypatch):
    d = _daemon(tmp_path, monkeypatch)
    d._on_file_recv(99, 0, FILE_KIND_DATA, 10, b"x.png")
    assert (99, 0, FILE_CONTROL_CANCEL) in d.tox.controls
    assert d._get_messages({})["messages"] == []


def test_sender_cancel_discards_partial(tmp_path, monkeypatch):
    d = _daemon(tmp_path, monkeypatch)
    d._on_file_recv(5, 0, FILE_KIND_DATA, 100, b"big.mp4")
    d._on_file_recv_chunk(5, 0, 0, b"partial")
    part = d._transfers[(5, 0)]["part_path"]
    assert part.exists()
    d._on_file_recv_control(5, 0, FILE_CONTROL_CANCEL)
    assert (5, 0) not in d._transfers
    assert not part.exists()
    assert d._get_messages({})["messages"] == []
