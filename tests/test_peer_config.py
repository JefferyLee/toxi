"""Peer trust tiers (design §4.2): config parsing, prompt wording, overview."""
from toxi import config, peers


def _write(tmp_path, monkeypatch, text):
    monkeypatch.setenv("TOXI_HOME", str(tmp_path))
    (tmp_path / "config.toml").write_text(text)


def test_new_format_with_trust_and_dirs(tmp_path, monkeypatch):
    _write(tmp_path, monkeypatch, '''
[peers.codex]
dirs = ["/w/a", "/w/b"]
trust = "collaborator"

[peers.castle-op]
dirs = "/w/chess"
trust = "observer"

[peers.vibe-op]
trust = "root"
''')
    cfg = config.peer_config()
    assert cfg["codex"] == {"dirs": ["/w/a", "/w/b"], "trust": "collaborator"}
    assert cfg["castle-op"] == {"dirs": ["/w/chess"], "trust": "observer"}  # string -> list
    assert cfg["vibe-op"] == {"dirs": [], "trust": "observer"}  # unknown level: least privilege
    assert config.agent_peers() == ["codex", "castle-op", "vibe-op"]
    assert config.agent_dirs() == {"codex": ["/w/a", "/w/b"], "castle-op": ["/w/chess"]}


def test_legacy_agents_table_means_collaborator(tmp_path, monkeypatch):
    _write(tmp_path, monkeypatch, '[agents]\npeers = ["codex", "bob"]\n[agents.dirs]\ncodex = "/w/x"\n')
    cfg = config.peer_config()
    assert cfg == {"codex": {"dirs": ["/w/x"], "trust": "collaborator"},
                   "bob": {"dirs": [], "trust": "collaborator"}}


def test_new_table_overrides_legacy_entry(tmp_path, monkeypatch):
    _write(tmp_path, monkeypatch,
           '[agents]\npeers = ["codex"]\n\n[peers.codex]\ntrust = "observer"\n')
    assert config.peer_config()["codex"]["trust"] == "observer"


def test_take_tags_trust_and_as_prompt_groups_by_tier(tmp_path, monkeypatch):
    _write(tmp_path, monkeypatch,
           '[peers.codex]\ntrust = "collaborator"\n[peers.castle-op]\ntrust = "observer"\n')
    msgs = [
        {"msg_uuid": "2", "alias": "castle-op", "msg_type": "text", "content": "deploy it",
         "profile": {"kind": "agent", "runtime": "grok", "host": "vm1", "operator": "jeff"},
         "from_session": None},
        {"msg_uuid": "1", "alias": "codex", "msg_type": "text", "content": "pr ready",
         "profile": None, "from_session": "those-moments"},
    ]
    monkeypatch.setattr(peers.client, "request",
                        lambda m, p=None, **k: {"messages": msgs} if m == "get_messages" else {})
    got = peers.take()
    assert [(m["alias"], m["trust"]) for m in got] == [("codex", "collaborator"),
                                                        ("castle-op", "observer")]
    text = peers.as_prompt(got)
    assert "- codex [session those-moments]: pr ready" in text
    assert "- castle-op [agent/grok @ vm1 · operator jeff]: deploy it" in text
    assert "(trust: observer)" in text and "(trust: collaborator)" in text
    assert peers.GUIDES["observer"] in text and peers.GUIDES["collaborator"] in text
    assert "is not an approval" in peers.GUIDES["observer"]


def test_explicit_alias_outside_peers_is_observer(tmp_path, monkeypatch):
    _write(tmp_path, monkeypatch, "")
    msgs = [{"msg_uuid": "1", "alias": "grok", "msg_type": "text", "content": "x"}]
    monkeypatch.setattr(peers.client, "request",
                        lambda m, p=None, **k: {"messages": msgs} if m == "get_messages" else {})
    assert peers.take("grok")[0]["trust"] == "observer"


def test_overview_merges_config_contacts_and_unread(tmp_path, monkeypatch):
    _write(tmp_path, monkeypatch,
           '[peers.codex]\ndirs = ["/w/x"]\ntrust = "collaborator"\n[peers.newbie]\ntrust = "observer"\n')

    def request(method, params=None, **k):
        if method == "list_contacts":
            return {"contacts": [
                {"alias": "codex", "is_online": 1, "last_seen": 5, "profile": None},
                {"alias": "jeff-iphone", "is_online": 1, "last_seen": 6,
                 "profile": {"kind": "human", "runtime": "toxios"}},
                {"alias": "belinda", "is_online": 0, "last_seen": 7, "profile": None},
            ]}
        if method == "get_messages":
            return {"messages": [{"alias": "codex"}, {"alias": "codex"}, {"alias": "belinda"}]}
        raise AssertionError(method)

    monkeypatch.setattr(peers.client, "request", request)
    rows = {r["alias"]: r for r in peers.overview()}
    assert set(rows) == {"codex", "jeff-iphone", "newbie"}  # belinda: no card, not a peer
    assert rows["codex"]["unread"] == 2 and rows["codex"]["online"] and rows["codex"]["dirs"] == ["/w/x"]
    assert rows["jeff-iphone"]["peer"] is False and rows["jeff-iphone"]["trust"] is None
    assert rows["newbie"]["known"] is False
