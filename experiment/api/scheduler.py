import json
import rpyc

from pathlib import Path
from time import sleep
from typing import List

from .experiment import Experiment
from .host import Host


class Scheduler:
    def __init__(self, hosts: List[Host], port: int):
        self._hosts = hosts
        self._port = port
        self._host_connections = dict()
        self._host_info = dict()

        self._connect_to_hosts()

    def _connect_to_hosts(self):
        for host in self._hosts:
            try:
                connection = rpyc.connect(host.domain(), self._port)
                self._host_connections[host] = connection
                self._host_info[host.name()] = dict()
                self._host_info[host.name()]["active_jobs"] = list()
                self._host_info[host.name()]["inactive_jobs"] = list()
            except Exception as err:
                print(f"Failed to connect to {host.domain()}: {err}")
                raise err

    def schedule(
        self,
        experiment: Experiment,
        status_board_path: Path,
        poll_interval_seconds: int = 120,
    ):
        remaining_jobs = experiment.get_jobs()
        num_active_jobs = 0
        while True:
            for host in self._hosts:
                connection = self._host_connections[host]
                active_jobs = self._host_info[host.name()]["active_jobs"]
                inactive_jobs = self._host_info[host.name()]["inactive_jobs"]
                for pid, job_cmd in active_jobs:
                    running = connection.root.is_running(pid)
                    if not running:
                        inactive_jobs.append((pid, job_cmd))
                        active_jobs.remove((pid, job_cmd))
                        num_active_jobs -= 1
                for _ in range(
                    min(
                        len(remaining_jobs), host.capacity() - len(active_jobs)
                    )
                ):
                    job = remaining_jobs.pop(0)
                    serialized_job = job.serialize()
                    pid = connection.root.launch_job(
                        serialized_job, debug=True
                    )
                    active_jobs.append((pid, job.command()))
                    num_active_jobs += 1
            with open(status_board_path, "w") as status_board:
                json.dump(self._host_info, status_board, indent=2)
            if num_active_jobs == 0 and len(remaining_jobs) == 0:
                break
            sleep(poll_interval_seconds)
        print("All jobs completed.")
