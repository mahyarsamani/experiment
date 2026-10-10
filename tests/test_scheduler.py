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


def make_host(
    root: FakeRoot, name: str = "h", capacity: int = 2, isa: str = "x86_64"
) -> Host:
    host = Host(name, isa, "localhost", capacity, insecure=True)

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


def test_watcher_retries_then_gives_up(tmp_path):
    root = FakeRoot()
    experiment = make_experiment("e", 1, tmp_path)
    scheduler, _ = make_scheduler(tmp_path, root, [experiment])
    job = experiment.jobs()[0]
    outdir = str(job.outdir())
    launches = []
    original = root.launch_job

    def count_launch(*args):
        launches.append(args[0])
        return original(*args)

    root.launch_job = count_launch
    root.job_statuses = lambda outdirs: tuple(
        (root.status.get(o, "missing"), 1, "watcher: flaky", None, None)
        for o in outdirs
    )
    scheduler._tick()
    for attempt in range(1, job.max_retries + 2):
        assert job.status() == JobStatus.RUNNING
        root.status[outdir] = "retry"
        # NOTE: A retried job is queued and relaunched in the same tick.
        scheduler._tick()
        if attempt <= job.max_retries:
            assert job.retries() == attempt
            assert len(launches) == attempt + 1
    assert job.status() == JobStatus.FAILED
    assert "gave up after 3 retries" in job.message()
    assert len(launches) == job.max_retries + 1

    run(scheduler, "reset", jobs=[job.id()])
    assert job.retries() == 0 and job.status() == JobStatus.QUEUED


def test_retries_survive_a_restart(tmp_path):
    job = make_experiment("e", 1, tmp_path).jobs()[0]
    job.assign("h")
    job.retry("flaky")
    restored = make_experiment("e", 1, tmp_path).jobs()[0]
    restored.restore_runtime_state(job.runtime_state())
    assert restored.retries() == 1


def test_host_isa_is_required_and_checked():
    assert Host("a", "aarch64", "localhost", 4).isa() == "aarch64"
    with pytest.raises(ValueError, match="isa must be one of"):
        Host("a", 9101, "localhost", 4)
    spec = Host("a", "x86_64", "localhost", 4).spec()
    assert Host.from_spec(spec).isa() == "x86_64"


class ArmOnlyJob(Job):
    def compatible_with_host(self, host):
        return host.isa() == "aarch64"


class BrokenCheckJob(Job):
    def compatible_with_host(self, host):
        raise RuntimeError("oops")


def make_job(cls, name, tmp_path):
    return cls(
        "e", tmp_path, name, name, tmp_path / "e" / name, 1, f"{name}-id"
    )


def test_jobs_only_run_on_compatible_hosts(tmp_path):
    experiment = Experiment("e", tmp_path / "e")
    arm = make_job(ArmOnlyJob, "arm", tmp_path)
    broken = make_job(BrokenCheckJob, "broken", tmp_path)
    plain = make_job(Job, "plain", tmp_path)
    for job in (arm, broken, plain):
        experiment.register_job(job)
    x86_root, arm_root = FakeRoot(), FakeRoot()
    scheduler, _ = make_scheduler(tmp_path, x86_root, [experiment])
    arm_host = make_host(arm_root, name="arm-host", isa="aarch64")
    scheduler._add_host(arm_host)
    assert arm_host.connect()

    scheduler._tick()
    assert arm.host() == "arm-host"
    assert plain.status() == JobStatus.RUNNING
    assert broken.status() == JobStatus.QUEUED
    views = {job["id"]: job for job in scheduler.snapshot().jobs}
    assert views["broken-id"]["message"] == "no compatible host"
    assert any(
        "compatible_with_host" in event[3]
        for event in scheduler.events().since(0)
    )


def test_start_and_end_times_come_from_the_worker(tmp_path):
    root = FakeRoot()
    times = {}
    root.job_statuses = lambda outdirs: tuple(
        (root.status.get(o, "missing"), 0, None, *times.get(o, (None, None)))
        for o in outdirs
    )
    experiment = make_experiment("e", 1, tmp_path)
    store = StateStore("test")
    scheduler, _ = make_scheduler(tmp_path, root, [experiment], store=store)
    scheduler._tick()
    job = experiment.jobs()[0]
    outdir = str(job.outdir())
    times[outdir] = (100.0, None)
    scheduler._tick()
    assert (job.start_time(), job.end_time()) == (100.0, None)
    root.status[outdir], times[outdir] = "exited", (100.0, 160.0)
    scheduler._tick()
    assert (job.start_time(), job.end_time()) == (100.0, 160.0)
    scheduler._persist(force=True)
    saved = store.load()["jobs"][job.id()]
    assert (saved["start_time"], saved["end_time"]) == (100.0, 160.0)
    assert job.reset() and job.start_time() is None
