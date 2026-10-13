"""The run backend, `helper run`, and gem5 jobs, with a fake gem5."""

import json
import os
import subprocess
import sys
import time

from pathlib import Path

import pytest

import experiment.api.simulate as simulate
from experiment.api.simulate import RunSpec, Simulator
from experiment.api.worker import Worker, resolve_status
from experiment.cli.run import _process_run_args, parse_run_args
from experiment.common.gem5_work import (
    OtherValues,
    calculate_hash,
    gem5Experiment,
    gem5Job,
    gem5ProjectConfiguration,
)

REPO = Path(__file__).resolve().parent.parent


@pytest.fixture
def project(tmp_path):
    for name in ("project", "gem5", "bin/main", "out"):
        (tmp_path / name).mkdir(parents=True)
    gem5 = tmp_path / "bin" / "main" / "gem5.fast"
    # NOTE: A fake gem5 that prints its arguments and environment.
    gem5.write_text(
        "#!/bin/bash\n"
        'echo "args: $*"\n'
        'echo "env: $GEM5_RESOURCE_JSON_APPEND $M5_OVERRIDE_PY_SOURCE"\n'
        "exit ${FAKE_GEM5_EXIT:-0}\n"
    )
    gem5.chmod(0o755)
    (tmp_path / "project" / "run.py").write_text("")
    return gem5ProjectConfiguration(
        "p", tmp_path / "project", tmp_path / "gem5", tmp_path / "bin",
        tmp_path / "out", None, "ARM", "CHI", "fast", 64,
    )


@pytest.fixture
def runs(monkeypatch):
    calls = []

    class FakeCompleted:
        returncode = 0

    def fake_run(argv, cwd, env):
        calls.append((argv, cwd, env))
        return FakeCompleted()

    monkeypatch.setattr(subprocess, "run", fake_run)
    return calls


def helper_run(project, *argv):
    return _process_run_args(lambda: project, *parse_run_args(list(argv)))


def test_helper_run_builds_the_same_command(project, runs, tmp_path):
    helper_run(
        project, "main", "run.py", "--outdir", "o", "--debug-flags", "Ruby",
        "--debug-start", "1", "--debug-end", "2", "--with-gdb", "--gdbinit",
        "init.gdb", "--override-py", "--gem5-resource-json", "r.json",
        "--cores", "4",
    )
    (argv, cwd, env), = runs
    paths = project.path_config()
    # NOTE: What `helper run` ran before the refactor, as an argument list.
    assert argv == [
        "gdb", "-x", "init.gdb", "--args",
        str(paths.gem5_binary_base_dir() / "main" / "gem5.fast"),
        "-re", f"--outdir={paths.gem5_out_base_dir() / 'o'}",
        "--debug-flags=Ruby", "--debug-start=1", "--debug-end=2",
        str(paths.project_dir() / "run.py"), "--cores", "4",
    ]
    assert cwd == str(paths.project_dir())
    assert env["GEM5_RESOURCE_JSON_APPEND"] == "r.json"
    assert env["M5_OVERRIDE_PY_SOURCE"] == "true"


def test_plain_helper_run_has_no_redirect(project, runs):
    helper_run(project, "main", "run.py")
    (argv, _, env), = runs
    assert argv[1:] == [str(project.path_config().project_dir() / "run.py")]
    assert "M5_OVERRIDE_PY_SOURCE" not in env


def test_missing_binary_names_the_host(project, runs):
    with pytest.raises(FileNotFoundError, match=os.uname().nodename):
        helper_run(project, "other", "run.py")


def make_job(project, *args, **kwargs):
    paths = project.path_config()
    experiment = gem5Experiment(
        "sweep", paths.project_dir(),
        paths.gem5_binary_base_dir() / "main" / "gem5.fast",
        paths.gem5_out_base_dir() / "sweep",
    )
    script = paths.project_dir() / "run.py"
    return experiment, script, gem5Job(experiment, 1, script, *args, **kwargs)


def test_gem5_job_keeps_its_id_and_gem5_command(project):
    experiment, script, job = make_job(
        project, 4, "a b", flag=OtherValues.Store_Const, size=2
    )
    kwargs = {"flag": OtherValues.Store_Const, "size": 2}
    assert job.id() == calculate_hash(
        ["sweep", experiment.gem5_path(), script, [4, "a b"],
         sorted(kwargs.items())]
    )
    assert job.display_command() == gem5Job.make_command(
        experiment.gem5_path(), job.outdir(), script, 4, "a b", **kwargs
    )
    assert "experiment.api.simulate" in job.command()
    dumped = {label: content for label, content, _ in job.dumps()}
    assert RunSpec(**json.loads(dumped["run.json"])) == job.run_spec()
    assert job.view()["full_command"] == job.display_command()


def wait_ended(outdir, timeout=15):
    deadline = time.time() + timeout
    while time.time() < deadline:
        status = resolve_status(outdir)
        if status[0] not in ("running", "missing"):
            return status
        time.sleep(0.05)
    raise AssertionError(resolve_status(outdir))


def launch(job):
    Worker().exposed_launch_job(
        job.id(), str(job.cwd()), job.command(), str(job.outdir()),
        tuple((label, str(path)) for label, path in job.files()),
        tuple((content, str(path)) for _, content, path in job.dumps()),
    )


@pytest.fixture
def importable(monkeypatch):
    # NOTE: The job's command imports this package from its own cwd.
    monkeypatch.setenv("PYTHONPATH", str(REPO))


def test_job_runs_gem5_through_the_worker(project, importable, monkeypatch):
    monkeypatch.setenv("FAKE_GEM5_EXIT", "3")
    _, _, job = make_job(project, 4, size=2)
    launch(job)
    status, returncode, _, _, _ = wait_ended(job.outdir())
    assert (status, returncode) == ("failed", 3)
    stdout = (job.outdir() / "stdout").read_text()
    assert f"--outdir={job.outdir()}" in stdout and "4 --size 2" in stdout


def test_job_with_missing_binary_fails_clearly(project, importable):
    paths = project.path_config()
    experiment = gem5Experiment(
        "x", paths.project_dir(), paths.gem5_binary_base_dir() / "nope",
        paths.gem5_out_base_dir() / "x",
    )
    job = gem5Job(experiment, 1, paths.project_dir() / "run.py")
    launch(job)
    status, returncode, _, _, _ = wait_ended(job.outdir())
    assert (status, returncode) == ("failed", 127)
    assert "gem5 binary not found" in (job.outdir() / "stderr").read_text()


def test_rerun_a_job_from_its_spec(project, runs, tmp_path):
    _, _, job = make_job(project, 4)
    spec_path = tmp_path / "run.json"
    spec_path.write_text(job.run_spec().to_json())
    helper_run(project, "--spec", str(spec_path), "--debug-flags", "Exec",
               "--with-gdb")
    (argv, _, _), = runs
    assert argv[:2] == ["gdb", "--args"]
    outdir = next(a for a in argv if a.startswith("--outdir="))
    assert outdir.startswith(f"--outdir={job.outdir()}/rerun-")
    assert "--debug-flags=Exec" in argv and argv[-1] == "4"
    with pytest.raises(ValueError, match="takes no build name"):
        helper_run(project, "main", "--spec", str(spec_path))
