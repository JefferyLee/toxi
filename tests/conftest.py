"""Pytest controls for tests that need a real toxcore runtime."""
import os

import pytest


def pytest_addoption(parser):
    parser.addoption(
        "--run-toxcore",
        action="store_true",
        default=False,
        help="run tests that construct libtoxcore handles or start the daemon",
    )
    parser.addoption(
        "--run-dht",
        action="store_true",
        default=False,
        help="run slow tests that connect to the public Tox DHT",
    )


def pytest_collection_modifyitems(config, items):
    run_dht = config.getoption("--run-dht") or os.environ.get("TOXI_RUN_DHT") == "1"
    run_toxcore = (
        run_dht
        or config.getoption("--run-toxcore")
        or os.environ.get("TOXI_RUN_TOXCORE") == "1"
    )
    skip_dht = pytest.mark.skip(reason="needs public Tox DHT; run with --run-dht")
    skip_toxcore = pytest.mark.skip(reason="needs real toxcore runtime; run with --run-toxcore")

    for item in items:
        if "dht" in item.keywords and not run_dht:
            item.add_marker(skip_dht)
        elif "toxcore" in item.keywords and not run_toxcore:
            item.add_marker(skip_toxcore)


@pytest.fixture(autouse=True)
def _own_sessions_dir(tmp_path_factory, monkeypatch):
    """Keep the session registry of every test out of the real TOXI_HOME."""
    from toxi import sessions
    d = tmp_path_factory.mktemp("sessions")
    monkeypatch.setattr(sessions, "_dir", lambda: d)
