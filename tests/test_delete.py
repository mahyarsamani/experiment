"""Deleting jobs, experiments and scripts from the scheduler's state."""

from pathlib import Path

import pytest

from experiment.api.scheduler.scheduler import CommandError, Scheduler
from experiment.api.scheduler.state import StateStore
from experiment.api.work import JobStatus

from test_scheduler import FakeRoot, make_host, run

SCRIPT = """
from pathlib import Path
from experiment.api.work import Experiment, Job

{name} = Experiment("{name}", Path("{tmp}/{name}"))
for i in range({count}):
    {name}.register_job(Job(
        "{name}", Path("{tmp}"), f"c{{i}}", f"c{{i}}",
        Path("{tmp}/{name}/{{i}}"), 1, f"{name}-{{i:04d}}",
    ))
"""


def write_script(tmp_path: Path, name: str, count: int) -> Path:
    path = tmp_path / f"{name}.py"
    path.write_text(SCRIPT.format(name=name, tmp=tmp_path, count=count))
    return path


def new_scheduler(store=None, capacity=0):
    """A scheduler with one host; capacity 0 keeps every job queued."""
    root = FakeRoot()
    scheduler = Scheduler("test", polling_secs=0, store=store)
    host = make_host(root, capacity=capacity)
    scheduler._add_host(host)
    assert host.connect()
    return scheduler, root


def job_ids(scheduler: Scheduler) -> list[str]:
    return sorted(scheduler._jobs)


def test_deleted_jobs_stay_deleted_across_reload_and_resume(tmp_path):
    store = StateStore("test")
    scheduler, _ = new_scheduler(store)
    script = write_script(tmp_path, "e", 3)
    run(scheduler, "load", script=str(script))
    assert run(scheduler, "delete", jobs=["e-0001"])[-1] == "deleted 1 job(s)"
    assert job_ids(scheduler) == ["e-0000", "e-0002"]

    lines = run(scheduler, "reload", script=str(script))
    assert "1 deleted job(s) skipped" in lines
    assert job_ids(scheduler) == ["e-0000", "e-0002"]

    scheduler._persist(force=True)
    resumed, _ = new_scheduler(store)
    resumed.resume(store.load())
    assert job_ids(resumed) == ["e-0000", "e-0002"]

    run(resumed, "undelete", jobs=["e-0001"])
    assert job_ids(resumed) == ["e-0000", "e-0001", "e-0002"]


def test_running_jobs_are_skipped(tmp_path):
    scheduler, _ = new_scheduler(capacity=1)
    run(scheduler, "load", script=str(write_script(tmp_path, "e", 2)))
    scheduler._tick()
    running = [j for j in scheduler._jobs.values() if j.status().active()]
    assert len(running) == 1
    lines = run(scheduler, "delete", jobs=["e-0000", "e-0001"])
    assert any("skipped, it is running" in line for line in lines)
    assert lines[-1] == "deleted 1 job(s)"
    assert job_ids(scheduler) == [running[0].id()]


def test_experiment_delete_is_refused_while_running(tmp_path):
    scheduler, root = new_scheduler(capacity=1)
    script = write_script(tmp_path, "e", 2)
    run(scheduler, "load", script=str(script))
    scheduler._tick()
    with pytest.raises(CommandError, match="still running"):
        run(scheduler, "delete", experiment="e")

    # NOTE: Finishing one job launches the next, so finish them all.
    while any(j.status().active() for j in scheduler._jobs.values()):
        for job in scheduler._jobs.values():
            if job.status().active():
                root.status[str(job.outdir())] = "exited"
        scheduler._tick()
    run(scheduler, "delete", experiment="e")
    assert "e" not in scheduler._experiments and not scheduler._jobs
    assert "was deleted; skipped" in run(
        scheduler, "reload", script=str(script)
    )[0]
    run(scheduler, "undelete", experiment="e")
    assert len(scheduler._experiments["e"].jobs()) == 2


def test_deleting_a_script_removes_its_jobs_and_stops_reloading(tmp_path):
    store = StateStore("test")
    scheduler, _ = new_scheduler(store)
    first = write_script(tmp_path, "a", 2)
    second = write_script(tmp_path, "b", 1)
    run(scheduler, "load", script=str(first))
    run(scheduler, "load", script=str(second))

    lines = run(scheduler, "delete", script=str(first))
    assert "2 job(s)" in lines[0] and "a" in lines[0]
    assert job_ids(scheduler) == ["b-0000"]
    assert "a" not in scheduler._experiments
    assert str(first.resolve()) not in scheduler._scripts

    scheduler._persist(force=True)
    resumed, _ = new_scheduler(store)
    resumed.resume(store.load())
    assert job_ids(resumed) == ["b-0000"]

    # NOTE: Loading it again is a fresh load, not blocked by the delete.
    run(resumed, "load", script=str(first))
    assert job_ids(resumed) == ["a-0000", "a-0001", "b-0000"]


def test_script_delete_is_refused_while_its_jobs_run(tmp_path):
    scheduler, _ = new_scheduler(capacity=1)
    script = write_script(tmp_path, "e", 1)
    run(scheduler, "load", script=str(script))
    scheduler._tick()
    assert scheduler._jobs["e-0000"].status() == JobStatus.RUNNING
    with pytest.raises(CommandError, match="still running"):
        run(scheduler, "delete", script=str(script))
