from ..host import Host, NoHost
from ..work import Job
from .state import JobView, Link

import requests

from dataclasses import asdict
from flask import (
    abort,
    Flask,
    jsonify,
    render_template,
    request,
    Response,
    stream_with_context,
)
from pathlib import Path
import threading, time
from typing import List
from urllib.parse import quote

status_color = {
    "pending": "#F59E0B",
    "running": "#10B981",
    "killed": "#EF4444",
    "exited": "#6B7280",
}


class DashboardServer:
    def __init__(self, title: str, host: str, port: int) -> None:
        self._title = title
        self._host = host
        self._port = port

        self._lock = threading.RLock()
        self._state = {
            "title": self._title,
            "hosts": [],
            "jobs": [],
            "last_update_epoch": 0.0,
        }

        self._app = Flask(
            __name__,
            template_folder="templates",
            static_folder="static",
            static_url_path="/static",
        )
        self._initialize_routes()

    def _initialize_routes(self) -> None:
        @self._app.get("/")
        def index():
            return render_template("base.html", title=self._title)

        @self._app.get("/api/state")
        def api_state():
            with self._lock:
                return jsonify(self._state)

        @self._app.get("/health")
        def health():
            return {"ok": True, "title": self._title}, 200

        @self._app.get("/files")
        def proxy_files():
            host_dom = request.args.get("host", "")
            raw_path = request.args.get("path", "")

            if not host_dom or not raw_path:
                abort(400, "missing host or path")

            p = Path(raw_path)
            if not p.is_absolute():
                abort(400, "path must be absolute")

            # Build the worker URL and stream it back
            worker_url = f"http://{host_dom}/files?path={quote(str(p))}"
            try:
                r = requests.get(worker_url, stream=True, timeout=10)
            except requests.RequestException:
                abort(502, "upstream worker unreachable")

            # Mirror status & content-type; stream the body
            headers = {}
            ct = r.headers.get("Content-Type")
            if ct:
                headers["Content-Type"] = ct

            def generate():
                for chunk in r.iter_content(chunk_size=8192):
                    if chunk:
                        yield chunk

            return Response(
                stream_with_context(generate()),
                status=r.status_code,
                headers=headers,
            )

    def update(self, pending_jobs: List[Job], hosts: List[Host]) -> None:
        with self._lock:
            job_views = []
            for job in pending_jobs:
                no_host = NoHost()
                links = [
                    Link(name, self.get_file_href(no_host, path))
                    for name, path in job.file_io()
                ]
                job_views.append(
                    JobView(
                        job.id(),
                        job.experiment().name(),
                        job.status(),
                        status_color.get(job.status(), "#0000FF"),
                        no_host.name(),
                        tuple(links),
                    )
                )
            for host in hosts:
                for job in host.jobs():
                    links = [
                        Link(name, self.get_file_href(host, path))
                        for name, path in job.file_io()
                    ]
                    job_views.append(
                        JobView(
                            job.id(),
                            job.experiment().name(),
                            job.status(),
                            status_color.get(job.status(), "#0000FF"),
                            host.name(),
                            tuple(links),
                        )
                    )
            self._state = {
                "title": self._title,
                "hosts": [host.name() for host in hosts],
                "jobs": [
                    {**asdict(v), "links": [asdict(l) for l in v.links]}
                    for v in job_views
                ],
                "last_update_epoch": time.time(),
            }

    def run(self) -> None:
        self._app.run(
            host=self._host, port=self._port, debug=False, use_reloader=False
        )

    def get_file_href(self, host: Host, path: Path) -> str:
        if isinstance(host, NoHost):
            return "#"
        return f"/files?host={host.file_domain()}&path={quote(str(path))}"
