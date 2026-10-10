"""The worker and runner, with real subprocesses (no rpyc)."""

import signal
import time

from pathlib import Path

import pytest

from experiment.api.worker import Worker, resolve_status


def launch(worker: Worker, tmp_path: Path, name: str, command: str) -> Path:
    outdir = tmp_path / name
    worker.exposed_launch_job(
        name,
        str(tmp_path),
        command,
        str(outdir),
        (("stdout", str(outdir / "stdout")),),
        (),
    )
    return outdir


def wait_for(outdir: Path, wanted: str, timeout: float = 10) -> tuple:
    deadline = time.time() + timeout
    while time.time() < deadline:
        status = resolve_status(outdir)
        if status[0] == wanted:
            return status
        time.sleep(0.05)
    raise AssertionError(f"{outdir} is {resolve_status(outdir)}, not {wanted}")


def test_exit_codes(tmp_path):
    worker = Worker()
    assert wait_for(launch(worker, tmp_path, "ok", "true"), "exited")[1] == 0
    assert wait_for(launch(worker, tmp_path, "bad", "exit 3"), "failed")[1] == 3


def test_missing_cwd_fails_the_job(tmp_path):
    worker = Worker()
    outdir = tmp_path / "job"
    worker.exposed_launch_job(
        "job", str(tmp_path / "nope"), "true", str(outdir), (), ()
    )
    status, returncode, message, start, end = wait_for(outdir, "failed")
    assert start is not None and end >= start
    assert returncode == 127 and "could not start" in message


@pytest.mark.parametrize("signum", [signal.SIGTERM, signal.SIGKILL])
def test_signal_marks_job_killed(tmp_path, signum):
    worker = Worker()
    outdir = launch(worker, tmp_path, "sleep", "sleep 30")
    wait_for(outdir, "running")
    assert worker.exposed_signal_job(str(outdir), int(signum))
    wait_for(outdir, "killed")
    assert not worker.exposed_signal_job(str(outdir), int(signum))


def test_crash_is_failed_not_killed(tmp_path):
    worker = Worker()
    outdir = launch(worker, tmp_path, "segv", "kill -SEGV $$")
    status, _, message, _, _ = wait_for(outdir, "failed")
    assert "signal 11" in message


def test_status_survives_worker_restart(tmp_path):
    outdir = launch(Worker(), tmp_path, "sleep", "sleep 1")
    fresh = Worker()
    assert fresh.exposed_job_statuses((str(outdir),))[0][0] == "running"
    wait_for(outdir, "exited")


def test_never_launched_is_missing(tmp_path):
    assert resolve_status(tmp_path / "nothing")[0] == "missing"


def test_refuses_double_launch(tmp_path):
    worker = Worker()
    outdir = launch(worker, tmp_path, "sleep", "sleep 30")
    with pytest.raises(RuntimeError):
        launch(worker, tmp_path, "sleep", "sleep 30")
    worker.exposed_signal_job(str(outdir), int(signal.SIGKILL))


def test_read_file_only_serves_job_files(tmp_path):
    worker = Worker()
    outdir = launch(worker, tmp_path, "echo", "echo hello")
    wait_for(outdir, "exited")
    assert worker.exposed_read_file(str(outdir), "stdout", 0, 100) == b"hello\n"
    assert worker.exposed_read_file(str(outdir), "stdout", 2, 2) == b"ll"
    with pytest.raises(PermissionError):
        worker.exposed_read_file(str(outdir), "../../etc/passwd", 0, 100)


def test_files_outside_outdir_are_rejected(tmp_path):
    with pytest.raises(ValueError):
        Worker().exposed_launch_job(
            "job",
            str(tmp_path),
            "true",
            str(tmp_path / "job"),
            (("secret", "/etc/passwd"),),
            (),
        )
