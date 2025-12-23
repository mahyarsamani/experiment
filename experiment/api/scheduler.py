from .dashboard.server import DashboardServer
from .host import Host
from .work import Experiment

from rpyc import Connection, Service, connect
from threading import RLock, Thread
from time import sleep
from typing import Dict
from warnings import warn


class Scheduler(Service):
    def __init__(self, polling_secs: int, dashboard_port: int) -> None:
        super().__init__()
        self._polling_secs = polling_secs

        self._hosts = list()
        self._hosts_pending_removal = list()
        self._hosts_lock = RLock()

        self._experiments = list()
        self._depleted_experiments = list()
        self._experiments_pending_removal = list()
        self._experiments_lock = RLock()

        self._dashboard = DashboardServer(
            "Experiment Dashboard", "localhost", dashboard_port
        )
        self._dashboard_thread = Thread(
            target=self._dashboard.run, daemon=True
        )
        self._schedule_thread = Thread(target=self._schedule_loop, daemon=True)

    def start_service(self) -> None:
        self._dashboard_thread.start()
        self._schedule_thread.start()

    def _schedule_loop(self):
        pending_jobs = list()
        while True:
            with self._hosts_lock, self._experiments_lock:
                # NOTE: Collect all jobs from all experiments.
                for experiment in self._experiments:
                    pending_jobs.extend(experiment.jobs())
                self._depleted_experiments.extend(self._experiments)
                self._experiments.clear()
                warn(f"I am scheduling {pending_jobs} on {self._hosts}.")
                # NOTE: Kill all the killed experiments first.
                for experiment in self._experiments_pending_removal:
                    for host in self._hosts:
                        host.kill_experiment(experiment)
                    for job in [
                        j for j in pending_jobs if j.experiment() == experiment
                    ]:
                        job.set_status("exited")
                        pending_jobs.remove(job)
                self._experiments_pending_removal.clear()

                # NOTE: Update hosts pending removal and remove
                # them  if they have no running jobs.
                hosts_to_remove = list()
                for host in self._hosts_pending_removal:
                    host.update()
                    if host.num_running_jobs() == 0:
                        host.disconnect()
                        hosts_to_remove.append(host)
                self._hosts_pending_removal = [
                    h
                    for h in self._hosts_pending_removal
                    if h not in hosts_to_remove
                ]
                hosts_to_remove.clear()

                # NOTE: Schedule new jobs on available hosts.
                did_schedule = True
                while did_schedule:
                    did_schedule = False

                    for host in self._hosts:
                        host.update()

                    self._hosts.sort(
                        key=lambda host: host.capacity(), reverse=True
                    )
                    for host in self._hosts:
                        eligible_jobs = sorted(
                            [
                                job
                                for job in pending_jobs
                                if job.demand() <= host.capacity()
                            ],
                            key=lambda job: job.demand(),
                            reverse=True,
                        )
                        if len(eligible_jobs) > 0:
                            job = eligible_jobs[0]
                            host.launch_job(job)
                            warn(f"Launched {job} on {host}.")
                            pending_jobs.remove(job)
                            did_schedule |= True
                snapshot_hosts = self._hosts.copy()
                snapshot_pending_jobs = pending_jobs.copy()
            self._dashboard.update(snapshot_pending_jobs, snapshot_hosts)
            sleep(self._polling_secs)

    def exposed_add_host(self, serialized_host: Dict):
        host = Host.deserialize(serialized_host)
        with self._hosts_lock:
            if host.name() in [host.name() for host in self._hosts]:
                warn(f"{host} already added! Won't add again.")
            else:
                self._hosts.append(host)
                self._hosts[-1].connect()

    def exposed_remove_host(self, host_name: str):
        with self._hosts_lock:
            if host_name not in [host.name() for host in self._hosts]:
                warn(f"{host_name} not found!")
            else:
                host = next(h for h in self._hosts if h.name() == host_name)
                self._hosts_pending_removal.append(host)
                self._hosts.remove(host)

    def exposed_add_experiment(self, serialized_experiment: Dict):
        experiment = Experiment.deserialize(serialized_experiment)
        with self._experiments_lock:
            if experiment.name() in [e.name() for e in self._experiments]:
                warn(f"{experiment} already added! Won't add again.")
            else:
                self._experiments.append(experiment)

    def exposed_remove_experiment(self, experiment_name: str):
        with self._experiments_lock:
            if experiment_name not in [e.name() for e in self._experiments]:
                warn(f"{experiment_name} not found!")
            else:
                experiment = next(
                    e for e in self._experiments if e.name() == experiment_name
                )
                self._experiments_pending_removal.append(experiment)
                self._experiments.remove(experiment)


def connect_to_scheduler(domain: str, port: int) -> Connection:
    return connect(domain, port).root
