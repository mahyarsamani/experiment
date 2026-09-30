"""Scheduler and Host logic against a fake worker (no network)."""

from pathlib import Path

import pytest

from experiment.api.host import Host, HostState
from experiment.api.scheduler.scheduler import CommandError, Scheduler
from experiment.api.scheduler.state import StateStore
from experiment.api.work import Experiment, Job, JobStatus


class RemoteError(RuntimeError):
    """Looks like an exception rpyc re-raised from the worker."""

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self._remote_tb = f"Traceback...\nRuntimeError: {message}"


class FakeRoot:
    """Stands in for `conn.root` of a worker."""

    def __init__(self) -> None:
        self.status: dict[str, str] = {}
        self.signals: list[tuple[str, int]] = []
        self.fail_launch: set[str] = set()
        self.down = False

    def _check(self):
        if self.down:
            raise EOFError("connection closed by peer")

    def ping(self):
        self._check()
        return "fake"

    def launch_job(self, job_id, cwd, command, outdir, files, dumps):
        self._check()
        if job_id in self.fail_launch:
            raise RemoteError("could not launch")
        self.status[outdir] = "running"
        return 1234

    def job_statuses(self, outdirs):
        self._check()
        return tuple(
            (self.status.get(outdir, "missing"), None, None)
            for outdir in outdirs
        )

    def signal_job(self, outdir, signum):
        self._check()
        self.signals.append((outdir, signum))
        if self.status.get(outdir) != "running":
            return False
        self.status[outdir] = "killed"
        return True


class FakeConnection:
    def __init__(self, root: FakeRoot) -> None:
        self.root = root

    def close(self):
        pass


def make_host(root: FakeRoot, name: str = "h", capacity: int = 2) -> Host:
    host = Host(name, "localhost", capacity, insecure=True)

    def open_fake():
        root._check()
        return FakeConnection(root)

    host._open = open_fake
    return host


def make_experiment(name: str, count: int, tmp_path: Path) -> Experiment:
    experiment = Experiment(name, tmp_path / name)
    for i in range(count):
        experiment.register_job(
            Job(
                name,
                tmp_path,
                f"cmd{i}",
                f"cmd{i}",
                tmp_path / name / str(i),
                1,
                f"{name}-{i:04d}",
            )
        )
    return experiment


def make_scheduler(tmp_path, root, experiments, store=None, capacity=2):
    scheduler = Scheduler("test", polling_secs=0, store=store)
    host = make_host(root, capacity=capacity)
    scheduler._add_host(host)
    for experiment in experiments:
        scheduler._experiments[experiment.name()] = experiment
        for job in experiment.jobs():
            scheduler._jobs[job.id()] = job
    assert host.connect()
    return scheduler, host


def run(scheduler: Scheduler, name: str, **kwargs):
    return scheduler._handlers[name](**kwargs)


def statuses(experiment: Experiment) -> list[str]:
    return [job.status().value for job in experiment.jobs()]


def test_respects_capacity(tmp_path):
    root = FakeRoot()
    experiment = make_experiment("e", 3, tmp_path)
    scheduler, _ = make_scheduler(tmp_path, root, [experiment])
    scheduler._tick()
    assert statuses(experiment) == ["running", "running", "queued"]
    root.status[str(experiment.jobs()[0].outdir())] = "exited"
    scheduler._tick()
    scheduler._tick()
    assert statuses(experiment) == ["exited", "running", "running"]


def test_kill_experiment_kills_every_job(tmp_path):
    root = FakeRoot()
    experiment = make_experiment("e", 4, tmp_path)
    scheduler, host = make_scheduler(
        tmp_path, root, [experiment], capacity=4
    )
    scheduler._tick()
    run(scheduler, "kill", experiment="e")
    scheduler._tick()
    scheduler._tick()
    assert len({outdir for outdir, _ in root.signals}) == 4
    assert "e" not in scheduler._experiments
    assert host.up()


def test_launch_error_fails_job_not_host(tmp_path):
    root = FakeRoot()
    experiment = make_experiment("e", 2, tmp_path)
    root.fail_launch.add("e-0000")
    scheduler, host = make_scheduler(tmp_path, root, [experiment])
    scheduler._tick()
    assert statuses(experiment) == ["failed", "running"]
    assert "could not launch" in experiment.jobs()[0].message()
    assert host.up()


def test_signal_to_finished_job_is_harmless(tmp_path):
    root = FakeRoot()
    experiment = make_experiment("e", 1, tmp_path)
    scheduler, host = make_scheduler(tmp_path, root, [experiment])
    scheduler._tick()
    job = experiment.jobs()[0]
    root.status[str(job.outdir())] = "exited"
    assert run(scheduler, "signal", jobs=["e-0"], signal="kill") == [
        "e-0000: was not running"
    ]
    assert host.up()


def test_lost_host_reattaches_without_relaunch(tmp_path):
    root = FakeRoot()
    experiment = make_experiment("e", 1, tmp_path)
    scheduler, host = make_scheduler(tmp_path, root, [experiment])
    scheduler._tick()
    root.down = True
    scheduler._tick()
    assert host.state() == HostState.DOWN
    assert statuses(experiment) == ["unknown"]

    root.down = False
    host._next_attempt = 0
    assert host.connect()
    launches_before = len(root.status)
    scheduler._tick()
    assert statuses(experiment) == ["running"]
    assert len(root.status) == launches_before


def test_unknown_job_with_no_record_is_requeued(tmp_path):
    root = FakeRoot()
    experiment = make_experiment("e", 1, tmp_path)
    scheduler, _ = make_scheduler(tmp_path, root, [experiment], capacity=0)
    job = experiment.jobs()[0]
    job.restore_runtime_state(
        {"status": "launching", "host": "h", "returncode": None}
    )
    assert job.status() == JobStatus.UNKNOWN
    scheduler._tick()
    assert job.status() == JobStatus.QUEUED


def test_reset_requires_force_for_unknown(tmp_path):
    root = FakeRoot()
    experiment = make_experiment("e", 1, tmp_path)
    scheduler, _ = make_scheduler(tmp_path, root, [experiment], capacity=0)
    job = experiment.jobs()[0]
    job.set_status(JobStatus.UNKNOWN)
    assert "use --force" in run(scheduler, "reset", jobs=["e-0"])[0]
    run(scheduler, "reset", jobs=["e-0"], force=True)
    assert job.status() == JobStatus.QUEUED


def test_ambiguous_job_prefix(tmp_path):
    experiment = make_experiment("e", 2, tmp_path)
    scheduler, _ = make_scheduler(tmp_path, FakeRoot(), [experiment])
    with pytest.raises(CommandError, match="matches 2 jobs"):
        run(scheduler, "signal", jobs=["e-"], signal="kill")


def test_state_is_written_before_launch_and_resumed(tmp_path):
    root = FakeRoot()
    store = StateStore("test")
    saved_during_launch = []
    original = root.launch_job

    def launch_and_peek(*args):
        saved_during_launch.append(store.load()["jobs"])
        return original(*args)

    root.launch_job = launch_and_peek
    experiment = make_experiment("e", 1, tmp_path)
    scheduler, _ = make_scheduler(tmp_path, root, [experiment], store=store)
    scheduler._tick()
    assert saved_during_launch[0]["e-0000"]["status"] == "launching"

    scheduler._persist(force=True)
    data = store.load()
    resumed = Scheduler("test", polling_secs=0, store=store)
    fresh = make_experiment("e", 1, tmp_path)
    resumed._experiments["e"] = fresh
    resumed._jobs = {job.id(): job for job in fresh.jobs()}
    resumed.resume(data)
    assert statuses(fresh) == ["running"]


def test_commands_from_other_threads(tmp_path):
    import threading

    experiment = make_experiment("e", 1, tmp_path)
    scheduler, _ = make_scheduler(tmp_path, FakeRoot(), [experiment])
    thread = threading.Thread(target=scheduler.run)
    thread.start()
    try:
        assert scheduler.call("capacity", host="h", change="+3") == [
            "host h capacity is now 5"
        ]
        with pytest.raises(CommandError):
            scheduler.call("kill", experiment="nope")
        assert scheduler.snapshot().hosts[0]["capacity"] == 5
    finally:
        scheduler.stop()
        thread.join(5)
    assert not thread.is_alive()
