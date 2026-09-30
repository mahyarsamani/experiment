import pytest


@pytest.fixture(autouse=True)
def isolated_dirs(tmp_path, monkeypatch):
    """Keep locks, sockets, state and certificates out of the real ones."""
    monkeypatch.setenv("EXPERIMENT_RUNTIME_DIR", str(tmp_path / "run"))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    monkeypatch.setenv("EXPERIMENT_PKI_DIR", str(tmp_path / "pki"))
    return tmp_path
