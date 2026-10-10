"""The dashboard's state and action API, against a fake worker."""

import socket
import threading

import pytest

from experiment.api.scheduler.dashboard import COOKIE, Dashboard, summarize

from test_scheduler import FakeRoot, make_experiment, make_scheduler


@pytest.fixture
def client(tmp_path):
    experiment = make_experiment("e", 3, tmp_path)
    scheduler, _ = make_scheduler(tmp_path, FakeRoot(), [experiment])
    thread = threading.Thread(target=scheduler.run)
    thread.start()
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    dashboard = Dashboard(scheduler, port, "test")
    app = dashboard._app
    app.testing = True
    test_client = app.test_client()
    test_client.set_cookie(COOKIE, dashboard._token, domain="localhost")
    # NOTE: The dashboard only answers requests addressed to localhost:<port>.
    base = f"http://localhost:{port}"
    for method in ("get", "post"):
        call = getattr(test_client, method)
        setattr(
            test_client,
            method,
            lambda *a, call=call, **kw: call(*a, base_url=base, **kw),
        )
    yield test_client
    scheduler.stop()
    thread.join(5)


def post(client, payload):
    return client.post("/api/action", json=payload)


def test_state_has_everything_the_page_shows(client):
    state = client.get("/api/state").get_json()
    assert [e["name"] for e in state["experiments"]] == ["e"]
    assert state["hosts"][0]["isa"] == "x86_64"
    job = state["jobs"][0]
    assert {"id", "start_time", "end_time", "retries", "files"} <= set(job)
    assert state["scripts"] == [] and state["deleted_jobs"] == 0


def test_page_embeds_the_state(client):
    page = client.get("/").get_data(as_text=True)
    assert 'id="initial-state"' in page and '"e-0000"' in page


def test_bulk_actions(client):
    ids = ["e-0000", "e-0001", "e-0002"]
    killed = post(client, {"action": "kill", "job_ids": ids}).get_json()
    assert killed["ok"] and len(killed["lines"]) == 3
    deleted = post(client, {"action": "delete", "job_ids": ids}).get_json()
    assert deleted["lines"][-1].startswith("deleted")
    assert post(client, {"action": "delete", "experiment": "nope"}).status_code == 409


def test_bad_requests_are_refused(client):
    assert post(client, {"action": "bogus", "job_ids": ["e-0000"]}).status_code == 400
    assert post(client, {"action": "kill", "job_ids": "e-0000"}).status_code == 400
    assert post(client, {"action": "kill"}).status_code == 400
    form = client.post("/api/action", data={"action": "kill"})
    assert form.status_code == 415


def test_requests_without_the_token_are_refused(client):
    client.delete_cookie(COOKIE)
    assert client.get("/api/state").status_code == 401


def test_summarize_keeps_toasts_short():
    lines = [f"{i}: skipped" for i in range(20)] + ["deleted 3 job(s)"]
    assert summarize(lines) == (
        "0: skipped; 1: skipped; 2: skipped; … 17 more; deleted 3 job(s)"
    )
