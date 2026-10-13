"""Dependencies and per-host jobs, against fake workers."""

from pathlib import Path

import pytest

from experiment.api.scheduler.scheduler import CommandError, Scheduler
from experiment.api.scheduler.state import StateStore
from experiment.api.work import Experiment, Job, JobStatus, PerHostJob

from test_scheduler import FakeRoot, make_host, run

S = JobStatus


def job(tmp_path, name, after=(), demand=1, cls=Job):
    return cls(
        "e", tmp_path, name, name, tmp_path / "e" / name, demand, name,
        after=after,
    )


class FakeBuild(PerHostJob):
    threads = 1
    hosts = None  # NOTE: None means every host.

    def compatible_with_host(self, host):
        return self.hosts is None or host.name() in self.hosts

    def make_job(self, host_name, id, outdir):
        return Job(
            "builds", outdir, "build", "build", outdir, self.threads, id
        )


class X86Job(Job):
    def compatible_with_host(self, host):
        return host.isa() == "x86_64"


def new_scheduler(store=None):
    return Scheduler("test", polling_secs=0, store=store)


def add_host(scheduler, name, capacity=4, isa="x86_64"):
    root = FakeRoot()
    host = make_host(root, name=name, capacity=capacity, isa=isa)
    scheduler._add_host(host)
    assert host.connect()
    return root


def install(scheduler, *jobs_and_templates, origin="script.py"):
    """What `load` does with a script's experiment, without a script."""
    experiment = Experiment("e", Path("/tmp/e"))
    for item in jobs_and_templates:
        experiment.register_job(item)
    scheduler._check_dependencies([experiment])
    scheduler._experiments.setdefault("e", Experiment("e", Path("/tmp/e")))
    for item in jobs_and_templates:
        if isinstance(item, PerHostJob):
            scheduler._register_template(item, origin)
        else:
            scheduler._experiments["e"].register_job(item)
            scheduler._jobs[item.id()] = item
            scheduler._job_origin[item.id()] = origin
    for item in jobs_and_templates:
        if isinstance(item, Job):
            scheduler._record_dependencies(item)


def finish(root, *jobs, status="exited"):
    for j in jobs:
        root.status[str(j.outdir())] = status


def test_a_chain_runs_in_order(tmp_path):
    scheduler = new_scheduler()
    root = add_host(scheduler, "h")
    a = job(tmp_path, "a")
    b = job(tmp_path, "b", after=[a])
    c = job(tmp_path, "c", after=[b])
    install(scheduler, a, b, c)
    scheduler._tick()
    assert [a.status(), b.status(), c.status()] == [S.RUNNING, S.WAITING, S.WAITING]
    assert b.message() == "waiting for a"
    finish(root, a)
    scheduler._tick()
    scheduler._tick()
    assert [b.status(), c.status()] == [S.RUNNING, S.WAITING]
    finish(root, b)
    scheduler._tick()
    scheduler._tick()
    assert c.status() == S.RUNNING


def test_a_failure_blocks_dependents_until_reset(tmp_path):
    scheduler = new_scheduler()
    root = add_host(scheduler, "h")
    a = job(tmp_path, "a")
    b = job(tmp_path, "b", after=[a])
    c = job(tmp_path, "c", after=[b])
    install(scheduler, a, b, c)
    scheduler._tick()
    finish(root, a, status="failed")
    scheduler._tick()
    scheduler._tick()
    assert [b.status(), c.status()] == [S.BLOCKED, S.BLOCKED]
    assert "a failed" in b.message() and "b is blocked" in c.message()

    run(scheduler, "reset", jobs=["a"])
    scheduler._tick()
    assert a.status() == S.RUNNING and b.status() == S.WAITING
    finish(root, a)
    for _ in range(3):
        scheduler._tick()
    assert b.status() == S.RUNNING


def test_unknown_dependencies_and_cycles_are_refused(tmp_path):
    scheduler = new_scheduler()
    stray = job(tmp_path, "stray")
    with pytest.raises(CommandError, match="neither loaded nor registered"):
        install(scheduler, job(tmp_path, "a", after=[stray]))
    a = job(tmp_path, "a")
    b = job(tmp_path, "b", after=[a])
    a.depends_on(b)
    with pytest.raises(CommandError, match="dependency cycle"):
        install(scheduler, a, b)
    with pytest.raises(ValueError):
        a.depends_on(a)


def test_per_host_builds_gate_each_host(tmp_path):
    """Builds run eagerly on every host; x86 jobs run only where theirs
    succeeded; a failed build takes its host out."""
    scheduler = new_scheduler()
    roots = {
        "x1": add_host(scheduler, "x1"),
        "x2": add_host(scheduler, "x2"),
        "a1": add_host(scheduler, "a1", isa="aarch64"),
    }
    build = FakeBuild("builds", "build-main", tmp_path / "builds")
    sims = [job(tmp_path, f"sim{i}", after=[build], cls=X86Job) for i in range(3)]
    install(scheduler, build, *sims)
    instances = {h: scheduler._jobs[f"build-main@{h}"] for h in roots}

    scheduler._tick()
    # NOTE: Eager: every host builds, even a1, which no sim can use.
    assert all(i.status() == S.RUNNING for i in instances.values())
    assert all(i.host() == h for h, i in instances.items())
    assert {s.status() for s in sims} == {S.WAITING}

    finish(roots["x1"], instances["x1"])
    finish(roots["x2"], instances["x2"], status="failed")
    scheduler._tick()
    scheduler._tick()
    assert {s.host() for s in sims} == {"x1"}
    assert {s.status() for s in sims} == {S.RUNNING}

    # NOTE: x1 is full now; x2 has room but its build failed.
    late = job(tmp_path, "late", after=[build], cls=X86Job)
    install(scheduler, late)
    scheduler._hosts["x1"].set_capacity(3)  # NOTE: full with the 3 sims
    scheduler._tick()
    assert late.status() == S.QUEUED
    view = next(v for v in scheduler.snapshot().jobs if v["id"] == "late")
    assert view["ready_hosts"] == ["x1"]


def test_build_failing_on_every_compatible_host_blocks(tmp_path):
    scheduler = new_scheduler()
    x1 = add_host(scheduler, "x1")
    a1 = add_host(scheduler, "a1", isa="aarch64")
    build = FakeBuild("builds", "build-main", tmp_path / "builds")
    sim = job(tmp_path, "sim", after=[build], cls=X86Job)
    install(scheduler, build, sim)
    scheduler._tick()
    finish(x1, scheduler._jobs["build-main@x1"], status="failed")
    finish(a1, scheduler._jobs["build-main@a1"])
    scheduler._tick()
    scheduler._tick()
    assert sim.status() == S.BLOCKED
    assert "no compatible host where build-main succeeded" in sim.message()


def test_build_and_jobs_have_separate_compatibility(tmp_path):
    scheduler = new_scheduler()
    add_host(scheduler, "x1")
    add_host(scheduler, "a1", isa="aarch64")
    build = FakeBuild("builds", "build-arm", tmp_path / "builds")
    build.hosts = {"a1"}
    sim = job(tmp_path, "sim", after=[build], cls=X86Job)
    install(scheduler, build, sim)
    assert "build-arm@x1" not in scheduler._jobs
    scheduler._tick()
    assert sim.status() == S.BLOCKED


def test_builds_take_at_most_their_host(tmp_path):
    scheduler = new_scheduler()
    add_host(scheduler, "x1", capacity=2)
    build = FakeBuild("builds", "build-main", tmp_path / "builds")
    build.threads = 16
    install(scheduler, build)
    scheduler._tick()
    instance = scheduler._jobs["build-main@x1"]
    assert instance.status() == S.RUNNING
    assert scheduler._used_capacity()["x1"] == 2


def test_new_hosts_get_their_build(tmp_path):
    scheduler = new_scheduler()
    add_host(scheduler, "x1")
    build = FakeBuild("builds", "build-main", tmp_path / "builds")
    install(scheduler, build)
    add_host(scheduler, "x2")
    assert "build-main@x2" in scheduler._jobs
    assert scheduler._jobs["build-main@x2"].pinned_host() == "x2"


def test_delete_respects_dependents(tmp_path):
    scheduler = new_scheduler()
    add_host(scheduler, "h", capacity=0)
    a = job(tmp_path, "a")
    b = job(tmp_path, "b", after=[a])
    install(scheduler, a, b)
    scheduler._tick()
    lines = run(scheduler, "delete", jobs=["a"])
    assert "depend on it" in lines[0] and lines[-1] == "deleted 0 job(s)"
    assert run(scheduler, "delete", jobs=["a", "b"])[-1] == "deleted 2 job(s)"


SCRIPT = """
from pathlib import Path
from experiment.api.host import Host
from experiment.api.work import Experiment, Job, PerHostJob

class Build(PerHostJob):
    def make_job(self, host_name, id, outdir):
        return Job("builds", outdir, "b", "b", outdir, 1, id)

build = Build("builds", "build-main", Path("{tmp}/builds"))
e = Experiment("e", Path("{tmp}/e"))
e.register_job(Job("e", Path("{tmp}"), "s", "s", Path("{tmp}/e/s"), 1, "sim",
                   after=[build]))
"""


def test_build_state_survives_a_restart(tmp_path):
    script = tmp_path / "jobs.py"
    script.write_text(SCRIPT.format(tmp=tmp_path))
    store = StateStore("test")
    scheduler = new_scheduler(store)
    root = add_host(scheduler, "x1")
    assert "added per-host job build-main (1 host(s))" in run(
        scheduler, "load", script=str(script)
    )
    scheduler._tick()
    finish(root, scheduler._jobs["build-main@x1"])
    scheduler._tick()
    scheduler._tick()
    assert scheduler._jobs["sim"].status() == S.RUNNING
    scheduler._persist(force=True)

    resumed = new_scheduler(store)
    add_host(resumed, "x1")
    resumed.resume(store.load())
    assert resumed._jobs["build-main@x1"].status() == S.EXITED
    assert resumed._deps["sim"] == ([], ["build-main"])
