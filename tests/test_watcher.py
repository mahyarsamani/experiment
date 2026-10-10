"""Job watchers, run by the runner with real subprocesses (no rpyc)."""

import time

from pathlib import Path

import psutil

from experiment.api.work import Job
from experiment.api.worker import Worker, resolve_status


class WatchedJob(Job):
    watch_interval = 0.2
    watch_timeout = 0.5
    script = None

    def watcher(self):
        return self.script


def launch(tmp_path: Path, command: str, script: str, **settings) -> Path:
    outdir = tmp_path / "job"
    job_class = type(
        "Watched", (WatchedJob,), dict(script=script, **settings)
    )
    job = job_class("e", tmp_path, command, command, outdir, 1, "job-id")
    Worker().exposed_launch_job(
        job.id(),
        str(tmp_path),
        command,
        str(outdir),
        tuple((label, str(path)) for label, path in job.files()),
        tuple((content, str(path)) for _, content, path in job.dumps()),
    )
    return outdir


def wait_ended(outdir: Path, timeout: float = 15) -> tuple:
    deadline = time.time() + timeout
    while time.time() < deadline:
        status = resolve_status(outdir)
        if status[0] not in ("running", "missing"):
            return status
        time.sleep(0.05)
    raise AssertionError(f"{outdir} still {resolve_status(outdir)}")


def log(outdir: Path) -> str:
    path = outdir / ".job" / "watcher.log"
    return path.read_text() if path.exists() else ""


def test_fail_kills_the_job(tmp_path):
    outdir = launch(tmp_path, "sleep 30", "echo fail stuck")
    status, _, message, start, end = wait_ended(outdir)
    assert (status, message) == ("failed", "watcher: stuck")
    assert end - start < 10
    assert "fail: stuck" in log(outdir)


def test_retry_is_reported(tmp_path):
    outdir = launch(tmp_path, "sleep 30", "echo retry port_proxy fatal")
    status, _, message, _, _ = wait_ended(outdir)
    assert (status, message) == ("retry", "watcher: port_proxy fatal")


def test_ok_watcher_lets_the_job_finish(tmp_path):
    outdir = launch(tmp_path, "sleep 1", 'echo ok; echo "checked" >&2')
    assert wait_ended(outdir)[0] == "exited"
    assert "checked" in log(outdir)


def test_watcher_judges_a_finished_job(tmp_path):
    script = (
        '[ "$JOB_RUNNING" = 0 ] && grep -q fatal "$JOB_OUTDIR/stderr" '
        '&& echo "fail fatal in stderr (exit $JOB_EXIT_CODE)"'
    )
    outdir = launch(tmp_path, "echo fatal >&2; exit 0", script)
    status, returncode, message, _, _ = wait_ended(outdir)
    assert status == "failed" and returncode == 0
    assert message == "watcher: fatal in stderr (exit 0)"


def test_hanging_watcher_is_timed_out_and_ignored(tmp_path):
    outdir = launch(tmp_path, "sleep 1", "sleep 30; echo fail never")
    assert wait_ended(outdir)[0] == "exited"
    assert "timed out" in log(outdir)


def test_unknown_output_is_ignored(tmp_path):
    outdir = launch(tmp_path, "sleep 0.5", "echo maybe later")
    assert wait_ended(outdir)[0] == "exited"
    assert "not ok/fail/retry" in log(outdir)


def test_shebang_watcher_uses_its_interpreter(tmp_path):
    script = (
        "#!/usr/bin/env python3\n"
        "import os\n"
        "print('fail from python', os.environ['JOB_ID'])\n"
    )
    outdir = launch(tmp_path, "sleep 30", script)
    status, _, message, _, _ = wait_ended(outdir)
    assert (status, message) == ("failed", "watcher: from python job-id")


def test_fail_kills_everything_the_job_started(tmp_path):
    outdir = launch(
        tmp_path,
        "sleep 31 & sleep 32 & wait",
        '[ -n "$(pgrep -f "sleep 3[12]")" ] && echo fail cleanup',
    )
    assert wait_ended(outdir)[0] == "failed"
    leftovers = [
        p
        for p in psutil.process_iter(["cmdline"])
        if p.info["cmdline"] in (["sleep", "31"], ["sleep", "32"])
    ]
    assert leftovers == []


def test_job_without_watcher_has_no_watcher_files(tmp_path):
    job = Job("e", tmp_path, "true", "true", tmp_path / "j", 1, "id")
    assert job.dumps() == ()
    assert "watcher.log" not in dict(job.files())
