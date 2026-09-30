import argparse
import os
import signal
import ssl
import subprocess
import sys
import threading
import time

import pytest
import rpyc

from prompt_toolkit.completion import CompleteEvent
from prompt_toolkit.document import Document
from rpyc.utils.server import ThreadedServer

from experiment.api.scheduler.console import (
    ConsoleCompleter,
    ConsoleError,
    _HelpShown,
    build_parser,
    short_ids,
)
from experiment.api.scheduler.state import InstanceLock, StateStore
from experiment.api.worker import PROTOCOL_CONFIG, Worker
from experiment.common import pki
from experiment.common.gem5_work import calculate_hash


def test_hash_keeps_item_boundaries():
    assert calculate_hash(["r", 1, 23]) != calculate_hash(["r", 12, 3])


@pytest.mark.parametrize(
    "line", ["kill", "kill a b", "signal", "signal hup x", "bogus", "kill -h"]
)
def test_console_parser_never_exits(line):
    parser, _ = build_parser()
    with pytest.raises((ConsoleError, argparse.ArgumentError, _HelpShown)):
        parser.parse_args(line.split())


def complete(text, names=None):
    _, commands = build_parser()
    completer = ConsoleCompleter(commands, names or (lambda kind: []))
    return [c.text for c in completer.get_completions(Document(text), CompleteEvent())]


def test_completion():
    names = {
        "experiment": [("sweep", ""), ("smoke", "")],
        "host": [("node1", "")],
        "job": [("abcdef01", ""), ("abcdff02", "")],
    }
    lookup = lambda kind: names.get(kind, [])
    assert "reload" in complete("re", lookup)
    assert complete("kill s", lookup) == ["sweep", "smoke"]
    assert complete("capacity ", lookup) == ["node1"]
    assert complete("signal ", lookup) == ["term", "int", "quit", "kill"]
    assert complete("signal kill abcdef01 abcdf", lookup) == ["abcdff02"]
    assert complete("jobs --status fa", lookup) == ["failed"]
    assert complete("jobs --host ", lookup) == ["node1"]
    assert complete("reset --f", lookup) == ["--force"]


def test_path_completion_offers_python_files_and_dirs(tmp_path, monkeypatch):
    (tmp_path / "scripts").mkdir()
    (tmp_path / "run.py").touch()
    (tmp_path / "notes.txt").touch()
    monkeypatch.chdir(tmp_path)
    assert sorted(complete("load ")) == ["run.py", "scripts"]


def test_short_ids_are_unique():
    ids = ["abcdef0123", "abcdef0199", "ffffffff00"]
    short = short_ids(ids)
    assert short["ffffffff00"] == "ffffffff"
    assert short["abcdef0123"] != short["abcdef0199"]
    assert all(full.startswith(s) for full, s in short.items())


def test_instance_lock_is_exclusive_and_released_on_kill(tmp_path):
    holder = subprocess.Popen(
        [
            sys.executable,
            "-c",
            "import time; from experiment.api.scheduler.state import "
            "InstanceLock; assert InstanceLock().acquire({'pid': 1}); "
            "print('locked', flush=True); time.sleep(60)",
        ],
        stdout=subprocess.PIPE,
        env=os.environ.copy(),
    )
    assert holder.stdout.readline().strip() == b"locked"
    lock = InstanceLock()
    assert not lock.acquire({})
    assert lock.holder() == {"pid": 1}
    holder.send_signal(signal.SIGKILL)
    holder.wait()
    assert lock.is_free()
    assert lock.acquire({"pid": os.getpid()})
    lock.release()


def test_state_store_round_trip():
    store = StateStore("t", min_interval=60)
    assert store.load() is None
    assert store.save({"jobs": {"a": 1}}, force=True)
    assert not store.save({"jobs": {}})  # throttled
    assert store.load()["jobs"] == {"a": 1}
    assert store.archive() is not None and store.load() is None


@pytest.fixture
def tls_worker(tmp_path):
    pki.init_ca()
    pki.issue(pki.WORKER, "localhost")
    pki.issue(pki.SCHEDULER, pki.SCHEDULER)
    cert, key = pki.paths("localhost")
    server = ThreadedServer(
        Worker(),
        hostname="127.0.0.1",
        port=0,
        authenticator=pki.RoleAuthenticator(
            cert, key, pki.ca_path(), pki.SCHEDULER
        ),
        protocol_config=PROTOCOL_CONFIG,
    )
    thread = threading.Thread(target=server.start, daemon=True)
    thread.start()
    time.sleep(0.2)
    yield server.port
    server.close()


def tls_connect(port, name, host="localhost"):
    cert, key = pki.paths(name)
    conn = rpyc.ssl_connect(
        host,
        port,
        keyfile=str(key),
        certfile=str(cert),
        ca_certs=str(pki.ca_path()),
        cert_reqs=ssl.CERT_REQUIRED,
        config=PROTOCOL_CONFIG,
    )
    return conn.root.ping()


def test_mtls_accepts_only_scheduler_role(tls_worker):
    assert tls_connect(tls_worker, pki.SCHEDULER)
    with pytest.raises(Exception):
        tls_connect(tls_worker, "localhost")
    with pytest.raises(Exception):
        rpyc.connect("127.0.0.1", tls_worker, config=PROTOCOL_CONFIG).root.ping()


def test_mtls_checks_worker_hostname(tls_worker):
    with pytest.raises(ssl.SSLCertVerificationError):
        # NOTE: The worker's certificate only names "localhost".
        tls_connect(tls_worker, pki.SCHEDULER, host="127.0.0.1")


def test_key_files_are_private():
    pki.init_ca()
    cert, key = pki.issue(pki.SCHEDULER, pki.SCHEDULER)
    assert key.stat().st_mode & 0o777 == 0o600
    with pytest.raises(FileExistsError):
        pki.init_ca()
