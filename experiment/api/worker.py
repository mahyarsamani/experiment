import os
import psutil
import subprocess

from pathlib import Path
from rpyc import Service
from typing import List, Tuple

from .job import Job


class Worker(Service):
    def __init__(self):
        self._processes = dict()

    def on_connect(self, conn):
        print("Client connected")

    def on_disconnect(self, conn):
        print("Client disconnected")

    def exposed_is_running(self, pid: int) -> bool:
        assert pid in self._processes, "PID not found in the process list"

        try:
            ps_proc = psutil.Process(pid)
            if ps_proc.status() == psutil.STATUS_ZOMBIE:
                self._processes[pid].poll()
                return False
            else:
                return ps_proc.is_running()
        except psutil.NoSuchProcess:
            return False

    def exposed_launch_job(
        self,
        serialized_job: dict,
        python_env_path: Path,
        env_vars: List[Tuple[str, str]] = [],
        debug: bool = False,
    ) -> int:
        job = Job.deserialize(serialized_job)

        env = os.environ.copy()
        env["VIRTUAL_ENV"] = str(python_env_path)
        env["PATH"] = f"{python_env_path}/bin:" + env["PATH"]

        for var, value in env_vars:
            env[var] = value

        if debug:
            with open(f"{job.cwd()}/stdout.log", "a") as stdout_log, open(
                f"{job.cwd()}/stderr.log", "a"
            ) as stderr_log:
                proc = subprocess.Popen(
                    ["/bin/bash", "-c", job.command()],
                    cwd=job.cwd(),
                    stdin=subprocess.DEVNULL,
                    stdout=stdout_log,
                    stderr=stderr_log,
                    env=env,
                    start_new_session=True,
                )
        else:
            proc = subprocess.Popen(
                ["/bin/bash", "-c", job.command()],
                cwd=job.cwd(),
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                env=env,
                start_new_session=True,
            )
        self._processes[proc.pid] = proc
        return proc.pid
