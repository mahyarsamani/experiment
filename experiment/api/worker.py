import os
import psutil
import subprocess

from pathlib import Path
from rpyc import Service

from .job import Job


class Worker(Service):
    def on_connect(self, conn):
        print("Client connected")

    def on_disconnect(self, conn):
        print("Client disconnected")

    def exposed_is_running(self, pid: int) -> bool:
        try:
            process = psutil.Process(pid)
            return process.is_running()
        except psutil.NoSuchProcess:
            return False

    def exposed_launch_job(
        self,
        serialized_job: dict,
        debug: bool = False,
    ) -> int:
        job = Job.deserialize(serialized_job)

        env = os.environ.copy()
        if job.env_path() != Path("/dev/null"):
            env["VIRTUAL_ENV"] = str(job.env_path())
            env["PATH"] = f"{job.env_path()}/bin:" + env["PATH"]

        if debug:
            with open(f"{job.cwd()}/" "stdout.log", "a") as stdout_log, open(
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
        return proc.pid
