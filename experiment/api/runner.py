"""Runs one job and records how it ended.

The worker launches this module as the leader of a new process group:

    python -m experiment.api.runner --outdir D --cwd C -- <command>

It runs `<command>` with bash, sends its output to D/stdout and D/stderr and,
when the command ends, atomically writes D/.job/exit.json. Together with the
D/.job/launch.json that the worker writes, this lets anyone (a restarted
worker, a restarted scheduler) tell whether the job is running, finished,
failed, or was killed, without having been its parent.

Signals are delivered to the whole process group by the worker, so the
runner only notes which signal it received (to tell "killed on request" from
"crashed") and keeps waiting for the command to end.
"""

import argparse
import json
import os
import signal
import subprocess
import sys
import time

from pathlib import Path

JOB_DIR = ".job"
LAUNCH_FILE = "launch.json"
EXIT_FILE = "exit.json"
FILES_FILE = "files.json"

FORWARDED_SIGNALS = (
    signal.SIGTERM,
    signal.SIGINT,
    signal.SIGQUIT,
    signal.SIGHUP,
)


def write_json_atomic(path: Path, data: dict) -> None:
    tmp = path.with_name(f".{path.name}.tmp")
    with open(tmp, "w") as f:
        json.dump(data, f)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)


def read_json(path: Path) -> dict | None:
    try:
        with open(path) as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return None


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="experiment.api.runner")
    parser.add_argument("--outdir", required=True, type=Path)
    parser.add_argument("--cwd", required=True, type=Path)
    parser.add_argument("command", nargs=argparse.REMAINDER)
    args = parser.parse_args(argv)

    command = args.command
    if command and command[0] == "--":
        command = command[1:]
    command = " ".join(command)

    job_dir = args.outdir / JOB_DIR
    job_dir.mkdir(parents=True, exist_ok=True)

    received = []

    def note_signal(signum, _frame):
        # NOTE: A Python handler (unlike SIG_IGN) is reset to the default
        # on exec, so the command still reacts to the signals normally.
        received.append(signum)

    for sig in FORWARDED_SIGNALS:
        signal.signal(sig, note_signal)

    returncode = None
    error = None
    try:
        with open(args.outdir / "stdout", "w") as out, open(
            args.outdir / "stderr", "w"
        ) as err:
            child = subprocess.Popen(
                ["bash", "-c", command],
                cwd=args.cwd,
                stdin=subprocess.DEVNULL,
                stdout=out,
                stderr=err,
            )
            returncode = child.wait()
    except OSError as e:
        error = f"could not start command: {e}"
        returncode = 127

    write_json_atomic(
        job_dir / EXIT_FILE,
        {
            "returncode": returncode,
            "signals_received": received,
            "end_time": time.time(),
            "error": error,
        },
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
