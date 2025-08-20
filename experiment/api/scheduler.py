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
        debug: bool = False,
    ):
        remaining_jobs = experiment.get_jobs()
        num_active_jobs = 0
        while True:
            schedule_queue = []
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
                schedule_queue.append(
                    (host, host.capacity() - len(active_jobs))
                )
            schedule_queue.sort(key=lambda x: x[1], reverse=True)
            while True:
                if not remaining_jobs:
                    break
                if not schedule_queue:
                    break

                host, available_slots = schedule_queue.pop(0)
                if available_slots == 0:
                    continue

                connection = self._host_connections[host]
                active_jobs = self._host_info[host.name()]["active_jobs"]
                job = remaining_jobs.pop(0)
                serialized_job = job.serialize()
                pid = connection.root.launch_job(
                    serialized_job,
                    host.python_env_path(),
                    host.env_vars(),
                    debug,
                )
                active_jobs.append((pid, job.command()))
                num_active_jobs += 1
                schedule_queue.append((host, available_slots - 1))

            with open(status_board_path, "w") as status_board:
                json.dump(self._host_info, status_board, indent=2)
            if num_active_jobs == 0 and len(remaining_jobs) == 0:
                break
            sleep(poll_interval_seconds)
        print("All jobs completed.")


# TODO: Incorporate the following snippet into the Scheduler class
# import paramiko


# def run_command_in_tmux(
#     host, username, key_path, venv_path, command, session_name="my_session"
# ):
#     # Set up SSH client
#     ssh = paramiko.SSHClient()
#     ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
#     ssh.connect(hostname=host, username=username, key_filename=key_path)

#     # Build the command to run inside tmux
#     full_cmd = f"""
#     tmux new-session -d -s {session_name} '
#     echo "Activating venv..." > ~/tmux_debug.log;
#     source {venv_path}/bin/activate 2>> ~/tmux_debug.log;
#     echo "Running command..." >> ~/tmux_debug.log;
#     {command} >> ~/tmux_debug.log 2>&1;
#     echo "Exit code: $?" >> ~/tmux_debug.log;
#     sleep 60'
#     """

#     stdin, stdout, stderr = ssh.exec_command(full_cmd)
#     exit_status = stdout.channel.recv_exit_status()

#     # Output logs
#     print("STDOUT:")
#     print(stdout.read().decode())
#     print("STDERR:")
#     print(stderr.read().decode())

#     if exit_status == 0:
#         print("✅ Command started in tmux session.")
#     else:
#         print("❌ Error starting command in tmux session.")

#     ssh.close()


# if __name__ == "__main__":
#     # Example usage
#     run_command_in_tmux(
#         host="azacca.idav.ucdavis.edu",
#         username="msamani",
#         key_path="/home/msamani/.ssh/experiment_key",
#         venv_path="/home/msamani/darchr/scatter-gather-opt/env/aarch64",
#         command="helper work",
#     )
