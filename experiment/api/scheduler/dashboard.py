"""The scheduler's web dashboard.

It listens on 127.0.0.1 only. Because other users on a shared machine can
reach 127.0.0.1 too, every request must carry a secret token: open the URL
with `?token=...` once and it is kept in a cookie. Requests whose Host
header isn't local are refused (DNS rebinding), and POSTs must be JSON (a
cross-site form can't send that without a CORS preflight, which we don't
answer).
"""

import hmac
import secrets

from flask import Flask, abort, jsonify, redirect, render_template, request
from flask import Response, stream_with_context
from werkzeug.exceptions import HTTPException
from werkzeug.serving import make_server

from ..host import HostUnreachable, JobError
from .scheduler import CommandError, Scheduler

COOKIE = "experiment_dashboard"
CHUNK = 256 * 1024


def summarize(lines: list[str], keep: int = 3) -> str:
    """A command's result lines, short enough for a toast."""
    if len(lines) <= keep + 1:
        return "; ".join(lines)
    return "; ".join(
        lines[:keep] + [f"… {len(lines) - keep - 1} more", lines[-1]]
    )


class Dashboard:
    def __init__(self, scheduler: Scheduler, port: int, title: str) -> None:
        self._scheduler = scheduler
        self._port = port
        self._title = title
        self._token = secrets.token_urlsafe(32)
        self._app = Flask(
            __name__,
            template_folder="templates",
            static_folder="static",
            static_url_path="/static",
        )
        self._setup_routes()
        self._server = make_server(
            "127.0.0.1", port, self._app, threaded=True
        )

    def url(self) -> str:
        return f"http://localhost:{self._port}/?token={self._token}"

    def serve_forever(self) -> None:
        self._server.serve_forever()

    def shutdown(self) -> None:
        self._server.shutdown()

    def _state(self) -> dict:
        snapshot = self._scheduler.snapshot()
        info = snapshot.info
        return {
            "title": self._title,
            "hosts": list(snapshot.hosts),
            "experiments": list(snapshot.experiments),
            "jobs": list(snapshot.jobs),
            "scripts": [
                {"path": path, "jobs": info.get("script_jobs", {}).get(path, 0)}
                for path in info.get("scripts", [])
            ],
            "deleted_jobs": len(info.get("deleted_jobs", [])),
            "deleted_experiments": info.get("deleted_experiments", []),
            "last_update_epoch": snapshot.taken_at,
        }

    def _call(self, name: str, **kwargs) -> list[str]:
        # NOTE: Bulk actions on hundreds of jobs can take a few seconds.
        return self._scheduler.call(name, timeout=60, **kwargs)

    def _authorized(self) -> bool:
        cookie = request.cookies.get(COOKIE, "")
        return hmac.compare_digest(cookie, self._token)

    def _setup_routes(self) -> None:
        app = self._app
        allowed_hosts = {
            f"{name}:{self._port}" for name in ("localhost", "127.0.0.1")
        } | {f"[::1]:{self._port}"}

        @app.before_request
        def check_request():
            if request.host not in allowed_hosts:
                abort(403, "unexpected Host header")
            token = request.args.get("token")
            if token is not None and hmac.compare_digest(token, self._token):
                response = redirect(request.path)
                response.set_cookie(
                    COOKIE, self._token, httponly=True, samesite="Strict"
                )
                return response
            if request.endpoint in ("static", "health"):
                return None
            if not self._authorized():
                abort(
                    401,
                    "open the dashboard with the URL shown by the console "
                    "(`info`)",
                )
            return None

        @app.after_request
        def security_headers(response):
            response.headers["X-Content-Type-Options"] = "nosniff"
            response.headers["X-Frame-Options"] = "DENY"
            response.headers["Referrer-Policy"] = "no-referrer"
            return response

        @app.errorhandler(HTTPException)
        def http_error(e: HTTPException):
            return jsonify({"ok": False, "error": e.description or e.name}), (
                e.code
            )

        @app.errorhandler(Exception)
        def unhandled_error(e: Exception):
            app.logger.exception("dashboard error")
            return jsonify({"ok": False, "error": "internal server error"}), 500

        @app.get("/health")
        def health():
            return {"ok": True}, 200

        @app.get("/")
        def index():
            # NOTE: The current state is embedded so the page renders right
            # away instead of after its first /api/state poll.
            return render_template(
                "base.html", title=self._title, state=self._state()
            )

        @app.get("/api/state")
        def api_state():
            return jsonify(self._state())

        @app.post("/api/action")
        def api_action():
            """One action on jobs, an experiment or a script.

            {"action": "kill"|"term"|"int"|"quit"|"reset"|"delete",
             "job_ids": [...]}, or {"action": "delete", "experiment": name},
             or {"action": "delete", "script": path}.
            """
            if not request.is_json:
                abort(415, "expected application/json")
            data = request.get_json(silent=True) or {}
            action = data.get("action")
            job_ids = data.get("job_ids") or []
            if not isinstance(job_ids, list) or not all(
                isinstance(job_id, str) and job_id for job_id in job_ids
            ):
                abort(400, "job_ids must be a list of job ids")
            experiment = data.get("experiment")
            script = data.get("script")
            try:
                if action == "delete" and script:
                    lines = self._call("delete", script=str(script))
                elif action == "delete" and experiment:
                    lines = self._call("delete", experiment=str(experiment))
                elif not job_ids:
                    abort(400, "no jobs given")
                elif action == "delete":
                    lines = self._call("delete", jobs=job_ids)
                elif action == "reset":
                    lines = self._call("reset", jobs=job_ids)
                elif action in Scheduler.SIGNALS:
                    lines = self._call("signal", jobs=job_ids, signal=action)
                else:
                    abort(400, f"unknown action {action!r}")
            except CommandError as e:
                abort(409, str(e))
            except TimeoutError:
                abort(504, "the scheduler did not answer in time")
            return jsonify(
                {"ok": True, "message": summarize(lines), "lines": lines}
            )

        @app.get("/files")
        def files():
            job_id = request.args.get("job", "")
            label = request.args.get("label", "")
            if not job_id or not label:
                abort(400, "missing job or label")
            try:
                first = self._scheduler.read_job_file(job_id, label, 0, CHUNK)
            except CommandError as e:
                abort(404, str(e))
            except HostUnreachable as e:
                abort(502, str(e))
            except JobError as e:
                abort(404, str(e))

            def generate():
                chunk, offset = first, 0
                while chunk:
                    yield chunk
                    offset += len(chunk)
                    if len(chunk) < CHUNK:
                        return
                    try:
                        chunk = self._scheduler.read_job_file(
                            job_id, label, offset, CHUNK
                        )
                    except (CommandError, HostUnreachable, JobError):
                        return

            return Response(
                stream_with_context(generate()),
                mimetype="text/plain",
                headers={"Content-Type": "text/plain; charset=utf-8"},
            )
