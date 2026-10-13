"""The build backend and its two frontends, with a fake scons."""

import json
import os
import subprocess
import sys
import threading
import time

from pathlib import Path

import pytest

import experiment.api.build as build
from experiment.api.build import Builder, BuildSpec
from experiment.cli.build import _process_build_args, parse_build_args
from experiment.common.gem5_work import gem5ProjectConfiguration


@pytest.fixture
def project(tmp_path):
    for name in ("project", "gem5", "bin", "out"):
        (tmp_path / name).mkdir()
    return gem5ProjectConfiguration(
        "p",
        tmp_path / "project",
        tmp_path / "gem5",
        tmp_path / "bin",
        tmp_path / "out",
        None,
        "ARM",
        "CHI",
        "fast",
        64,
    )


@pytest.fixture
def commands(monkeypatch):
    """Record run_command calls instead of running them."""
    calls = []
    returncodes = []

    def fake_run(argv, cwd):
        calls.append((argv[-1], Path(cwd)))
        returncode = returncodes.pop(0) if returncodes else 0
        words = argv[-1].split()
        if returncode == 0 and words[1] == "setconfig":
            # NOTE: Like the real setconfig, create the build directory.
            (Path(words[2]) / "gem5.build").mkdir(parents=True)
        return returncode

    monkeypatch.setattr(build, "run_command", fake_run)
    return calls, returncodes


def helper_build(project, *argv):
    return _process_build_args(project, *parse_build_args(list(argv)))


def test_helper_build_runs_the_same_commands(project, commands):
    calls, _ = commands
    paths = project.path_config()
    build_dir = paths.gem5_binary_base_dir() / "main"
    assert helper_build(
        project, "main", "--linker", "mold", "--gprof", "--no-tcmalloc",
        "--threads", "8",
    ) == 0
    setconfig, scons = calls
    assert setconfig == (
        project.build_config().make_setconfig_command(build_dir),
        paths.gem5_source_dir(),
    )
    # NOTE: The exact string `helper build` ran before the refactor.
    assert scons == (
        f"scons -C {paths.gem5_source_dir()} gem5.fast --linker=mold "
        "--gprof --without-tcmalloc -j 8",
        build_dir,
    )
    marks = build_dir / "gem5.build"
    assert (marks / "linker_override").read_text() == "mold\n"
    assert (marks / "built_with_gprof").exists()
    assert (marks / "built_without_tcmalloc").exists()

    calls.clear()
    assert helper_build(project, "main") == 0
    assert [command for command, _ in calls] == [
        f"scons -C {paths.gem5_source_dir()} gem5.fast"
    ]


def test_options_override_project_defaults(project, commands):
    calls, _ = commands
    helper_build(project, "x86", "--isas", "X86", "--protocols", "MESI2",
                 "--binary_opt", "opt", "--bits-per-set", "32")
    build_dir = project.path_config().gem5_binary_base_dir() / "x86"
    recorded = json.loads(
        (build_dir / "gem5.build" / "compile_config.json").read_text()
    )
    assert recorded == {
        "isas": ["X86"], "protocols": ["MESI_Two_Level"], "bits_per_set": 32
    }
    assert calls[-1][0].endswith("gem5.opt")


def test_failed_setconfig_is_not_recorded(project, commands):
    calls, returncodes = commands
    returncodes.append(3)
    assert helper_build(project, "main") == 3
    assert len(calls) == 1
    build_dir = project.path_config().gem5_binary_base_dir() / "main"
    assert not (build_dir / "gem5.build" / "compile_config.json").exists()
    calls.clear()
    helper_build(project, "main")
    assert len(calls) == 2  # setconfig is retried


def test_spec_round_trip_and_bad_options(project, tmp_path):
    spec = BuildSpec.from_project(project, "main", threads=4)
    path = tmp_path / "build.json"
    path.write_text(spec.to_json())
    assert BuildSpec.load(path) == spec
    assert spec.binary() == project.path_config().gem5_binary_base_dir() / "main" / "gem5.fast"
    with pytest.raises(ValueError):
        BuildSpec.from_project(project, "main", protocols="VIPER")


@pytest.fixture
def fake_scons(tmp_path, monkeypatch):
    """A `scons` on PATH that logs its start and end and fakes setconfig."""
    bin_dir = tmp_path / "fakebin"
    bin_dir.mkdir()
    log = tmp_path / "scons.log"
    script = bin_dir / "scons"
    script.write_text(
        "#!/bin/bash\n"
        f'echo "start $*" >> {log}\n'
        'if [ "$1" = setconfig ]; then mkdir -p "$2/gem5.build"; '
        f'else sleep 0.5; fi\necho "end" >> {log}\n'
    )
    script.chmod(0o755)
    monkeypatch.setenv("PATH", f"{bin_dir}:{os.environ['PATH']}")
    return log


def test_worker_entry_point_builds_from_a_spec(project, tmp_path, fake_scons):
    spec_path = tmp_path / "build.json"
    spec_path.write_text(BuildSpec.from_project(project, "main").to_json())
    result = subprocess.run(
        [sys.executable, "-m", "experiment.api.build", str(spec_path)],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    lines = fake_scons.read_text().splitlines()
    assert lines[0].startswith("start setconfig") and "gem5.fast" in lines[2]


def test_gem5_build_jobs_and_experiments(project):
    from experiment.common.gem5_work import gem5Build, gem5Experiment, gem5Job

    main = gem5Build(project, "main", threads=8, isas="X86")
    paths = project.path_config()
    assert main.id() == "build-p-main"
    instance = main.instance("perle")
    assert instance.id() == "build-p-main@perle"
    assert instance.pinned_host() == "perle" and instance.demand() == 8
    assert instance.outdir() == paths.gem5_out_base_dir() / "builds" / "main" / "perle"
    assert "experiment.api.build" in instance.command()
    (_, content, spec_path), = instance.dumps()
    assert BuildSpec(**json.loads(content)) == main.spec()
    assert spec_path == instance.outdir() / ".job" / "build.json"

    sweep = gem5Experiment(
        "sweep", paths.project_dir(), outdir=paths.gem5_out_base_dir() / "s",
        build=main,
    )
    assert sweep.gem5_path() == main.binary()
    sim = gem5Job(sweep, 1, paths.project_dir() / "run.py", 4)
    assert sim.parents() == [main]
    with pytest.raises(ValueError, match="gem5_path or build"):
        gem5Experiment("x", paths.project_dir(), outdir=paths.gem5_out_base_dir())


def test_two_builds_of_one_name_never_overlap(project, fake_scons):
    spec = BuildSpec.from_project(project, "main")
    Builder(spec).run()  # configure once, so both runs only build
    fake_scons.write_text("")
    threads = [
        threading.Thread(target=Builder(spec).run) for _ in range(2)
    ]
    for thread in threads:
        thread.start()
        time.sleep(0.1)
    for thread in threads:
        thread.join(10)
    events = [line.split()[0] for line in fake_scons.read_text().splitlines()]
    assert events == ["start", "end", "start", "end"]
