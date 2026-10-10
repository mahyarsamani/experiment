"""Runs one job and records how it ended.

The worker launches this module as the leader of a new process group:

    python -m experiment.api.runner --outdir D --cwd C --job-id I -- <command>

It runs `<command>` with bash, sends its output to D/stdout and D/stderr and,
when the command ends, atomically writes D/.job/exit.json. Together with the
D/.job/launch.json that the worker writes, this lets anyone (a restarted
worker, a restarted scheduler) tell whether the job is running, finished,
failed, or was killed, without having been its parent.

Signals are delivered to the whole process group by the worker, so the
runner only notes which signal it received (to tell "killed on request" from
"crashed") and keeps waiting for the command to end.

If the job has a watcher (D/.job/watcher, see `Job.watcher`), the runner runs
it every `interval` seconds while the command runs, and once more after it
ends. The first line the watcher prints decides what happens:

    (nothing) or "ok"    keep going
    "fail <message>"     kill the command; the job ends as failed
    "retry <message>"    kill the command; the scheduler queues it again

The verdict is recorded in exit.json, so the scheduler learns about it the
same way it learns about any other ending.
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
WATCHER_FILE = "watcher"
WATCHER_CONFIG_FILE = "watcher.json"
WATCHER_LOG_FILE = "watcher.log"

WATCHER_ACTIONS = ("ok", "fail", "retry")
KILL_GRACE_SECS = 10

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


class Watcher:
    """Runs a job's watcher script and interprets what it prints."""

    def __init__(
        self, job_dir: Path, outdir: Path, job_id: str, config: dict
    ) -> None:
        self._script = job_dir / WATCHER_FILE
        self._log = job_dir / WATCHER_LOG_FILE
        self._outdir = outdir
        self._job_id = job_id
        self.interval = float(config.get("interval", 60))
        self._timeout = float(config.get("timeout", 60))
        self._started = time.time()

    @classmethod
    def load(cls, job_dir: Path, outdir: Path, job_id: str) -> "Watcher | None":
        if not (job_dir / WATCHER_FILE).is_file():
            return None
        return cls(
            job_dir,
            outdir,
            job_id,
            read_json(job_dir / WATCHER_CONFIG_FILE) or {},
        )

    def log(self, message: str) -> None:
        stamp = time.strftime("%Y-%m-%d %H:%M:%S")
        with open(self._log, "a") as f:
            f.write(f"{stamp} {message}\n")

    def _command(self) -> list[str]:
        with open(self._script, "rb") as f:
            shebang = f.read(2) == b"#!"
        if shebang:
            os.chmod(self._script, 0o755)
            return [str(self._script)]
        return ["bash", str(self._script)]

    def check(self, pid: int, exit_code: int | None) -> tuple[str, str]:
        """Run the watcher once; returns (action, message)."""
        env = dict(
            os.environ,
            JOB_OUTDIR=str(self._outdir),
            JOB_ID=self._job_id,
            JOB_PID=str(pid),
            JOB_ELAPSED=str(int(time.time() - self._started)),
            JOB_RUNNING="0" if exit_code is not None else "1",
        )
        if exit_code is not None:
            env["JOB_EXIT_CODE"] = str(exit_code)
        with open(self._log, "a") as log:
            try:
                # NOTE: Its own session, so a timeout kills everything it ran.
                watcher = subprocess.Popen(
                    self._command(),
                    cwd=self._outdir,
                    env=env,
                    stdin=subprocess.DEVNULL,
                    stdout=subprocess.PIPE,
                    stderr=log,
                    text=True,
                    start_new_session=True,
                )
            except OSError as e:
                self.log(f"could not run the watcher: {e}")
                return "ok", ""
            try:
                output, _ = watcher.communicate(timeout=self._timeout)
            except subprocess.TimeoutExpired:
                os.killpg(watcher.pid, signal.SIGKILL)
                watcher.wait()
                self.log(f"watcher timed out after {self._timeout:g}s; ignored")
                return "ok", ""
        line = next((l.strip() for l in output.splitlines() if l.strip()), "")
        if not line:
            return "ok", ""
        action, _, message = line.partition(" ")
        if action not in WATCHER_ACTIONS:
            self.log(f"watcher printed {line!r}, not ok/fail/retry; ignored")
            return "ok", ""
        if action != "ok":
            self.log(f"{action}: {message}")
        return action, message.strip()


def kill_tree(child: subprocess.Popen) -> None:
    """TERM the command and everything it started, then KILL what's left."""
    import psutil

    try:
        procs = [psutil.Process(child.pid)]
        procs += procs[0].children(recursive=True)
    except psutil.NoSuchProcess:
        return
    for proc in procs:
        try:
            proc.terminate()
        except psutil.NoSuchProcess:
            pass
    _, alive = psutil.wait_procs(procs, timeout=KILL_GRACE_SECS)
    for proc in alive:
        try:
            proc.kill()
        except psutil.NoSuchProcess:
            pass


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="experiment.api.runner")
    parser.add_argument("--outdir", required=True, type=Path)
    parser.add_argument("--cwd", required=True, type=Path)
    parser.add_argument("--job-id", default="")
    parser.add_argument("command", nargs=argparse.REMAINDER)
    args = parser.parse_args(argv)

    command = args.command
    if command and command[0] == "--":
        command = command[1:]
    command = " ".join(command)

    job_dir = args.outdir / JOB_DIR
    job_dir.mkdir(parents=True, exist_ok=True)
    watcher = Watcher.load(job_dir, args.outdir, args.job_id)

    received = []

    def note_signal(signum, _frame):
        # NOTE: A Python handler (unlike SIG_IGN) is reset to the default
        # on exec, so the command still reacts to the signals normally.
        received.append(signum)

    for sig in FORWARDED_SIGNALS:
        signal.signal(sig, note_signal)

    returncode = None
    error = None
    verdict = ("ok", "")
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
            while True:
                try:
                    returncode = child.wait(
                        timeout=watcher.interval if watcher else None
                    )
                    break
                except subprocess.TimeoutExpired:
                    verdict = watcher.check(child.pid, None)
                    if verdict[0] != "ok":
                        kill_tree(child)
                        returncode = child.wait()
                        break
    except OSError as e:
        error = f"could not start command: {e}"
        returncode = 127

    if watcher and verdict[0] == "ok":
        # NOTE: Lets a watcher judge a run that ended on its own, e.g. turn
        # an exit code of 0 with a fatal in stderr into a failure.
        verdict = watcher.check(-1, returncode)

    write_json_atomic(
        job_dir / EXIT_FILE,
        {
            "returncode": returncode,
            "signals_received": received,
            "end_time": time.time(),
            "error": error,
            "watcher": (
                None
                if verdict[0] == "ok"
                else {"action": verdict[0], "message": verdict[1]}
            ),
        },
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
