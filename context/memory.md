# experiment memory

Last updated: 2026-10-05 (Claude Code, written from the code, git history, transcripts, and Claude memory; see Provenance).

## What this is
`experiment` is my Python package behind the `helper` CLI: it initializes a gem5 project (`project_config.json`), builds gem5, runs run scripts, and schedules gem5 jobs across lab hosts.
A scheduler daemon on one machine drives stateless workers on other hosts over mutual-TLS rpyc, with an attachable console and a local dashboard.
Every gem5 project (SIFT, the `experiment-test` sandbox) is a client of it; project paths come from the nearest `project_config.json`.

## Current state
- Version 0.26.09.1 at `main` 45ae855 (2026-09-30); the remote default branch was renamed from `master` to `main` on 2026-10-05, but local `main` still tracks `origin/master` until `git fetch && git branch -u origin/main`.
- The 2026-09-30 rewrite replaced the scheduler, worker, host, console, and daemon code: durable state, reattach to running jobs after a restart, mutual TLS with a self-managed CA, a Unix-socket control channel, a token-protected dashboard, and 35 pytest tests.
- Verified end to end on 2026-10-02: a worker on perle (port 9360), the scheduler on cascade, and all `experiment-test/run_many.py` jobs launched; the 4 of 8 that failed used a non-power-of-two core count (a sandbox script issue, not a scheduler bug).
- The scheduler on cascade was stopped on 2026-10-05; nothing is queued.
- Installed from GitHub (not editable) in the PhD venv `.venv/x86_64` at 45ae855; the old `env/aarch64` venv has 45ae855 and the old `env/x86_64` has 620b7db, so hosts can disagree.
- Job success is judged by exit code only: a hung gem5 stays `running` and holds capacity, and hitting `--max-ticks` still counts as success.
- Not done: per-job timeouts, stall detection, a completion-marker check, build-before-simulate dependencies, and code snapshots (planned and then cancelled on 2026-10-03).
- Unsure: commit 3a0ff10 has neither `setup.py` nor `pyproject.toml`, so that single commit does not install; later commits are fine.

## Decisions
- 2026-10-03: dependency graphs and code snapshotting are postponed; gem5 is built by hand with `helper build`, and job IDs depend on parameters only, never on code.
- 2026-10-01: the version is `0.YY.MM.N` (beta, monthly counter from 1), lives only in `pyproject.toml`, and is bumped only on request as its own "Updating version number." commit.
- 2026-09-30: security is mutual TLS over rpyc with a self-managed CA (`helper certs init`, `helper certs issue scheduler|worker <name> [--san]`); the role is in the certificate OU, so workers accept only schedulers; the Flask file server and its second port are gone.
- 2026-09-30: one scheduler per user per machine, locked with `flock` in `/tmp/experiment-<uid>` because flock over NFS is unreliable; state is saved per hostname under `~/.local/state/experiment/<host>/<name>` because the home directory is shared.
- 2026-09-30: the console attaches to the running daemon (`helper schedule` starts or attaches, `helper console` reattaches, Ctrl-D detaches); commands take names directly (`kill <exp>`, `drain|undrain|remove <host>`, `signal <sig> <job>...`, `load|reload <script>`, `reset`).
- 2026-09-30: script compatibility was allowed to break: `Host(name, domain, max_capacity, port=9100)` has no file-server port and `domain` must match a certificate SAN; `gem5FSSimulation` became `gem5Job` (alias kept); `aux_file_io` became `aux_files`, `optional_dump` became `dumps`; job IDs changed.
- 2026-09-30: Python 3.12 or newer, `pyproject.toml` instead of `setup.py`, `cryptography` and `rpyc>=6` added, `requests` dropped, `pytest` as the `dev` extra.
- 2026-09-30: warnings print without the source line; gem5-side decorators format their own warnings.
- 2026-09-30: tests are the lowest priority, but they exist and pass.
- 2026-09-24: full code review found the kill-experiment bug, host-dropping on any failure, hash collisions, unauthenticated rpyc on all interfaces, and no persistence; all addressed by the rewrite.

## Constraints and gotchas
- `helper` finds `project_config.json` by walking up from the current directory and resolves every path, so symlinked project paths work.
- Build name comes first: `helper build main ...`, `helper run main <script> -- <args>`; otherwise `helper` looks for the binary under the script path.
- `helper run` returns 0 when gem5 panics or aborts; check the log.
- Certificates live in `~/.config/experiment/pki/`; `helper work --name <n>` selects `<n>.crt/.key` and defaults to the hostname.
- `socket.getfqdn()` returns the short name on lab machines, so worker certificates need `--san <fqdn>`, or TLS fails with a hostname mismatch that the scheduler only logs as a timeout.
- Port 9100 on perle is held by another process; use another port and the same `port=` in `Host(...)`.
- `/scr` is local to each host, so the gem5 binary must exist on every worker at the configured path.
- Logs: `~/.local/state/experiment/<host>/<name>/{scheduler,dashboard,control}.log` and `daemon.out`; `EXPERIMENT_RUNTIME_DIR` overrides the runtime directory for tests.
- Dashboard on localhost:9200: tunnel with `ssh -L 9200:localhost:9200`, then `info` in the console prints the URL with its token.
- Workers are stateless: everything about a job lives in its outdir, so deleting a running job's outdir marks it failed.
- The ARM hosts need an aarch64 venv; a non-editable install does not pick up `git pull`.
- `pgrep -f`/`pkill -f` match an agent's own shell; kill by PID.
- Python 3.12 `argparse` raises `SystemExit` even with `exit_on_error=False`, so the console keeps `except SystemExit`.
- The sandbox `run_example.py` needs a power-of-two number of generator cores; `run_many.py`'s sweep over 1 to 8 therefore fails for 3, 5, 6, and 7.

## Open questions
- Which of timeout, stall detection, or a `simulation_finished` marker should decide job success?
- Dependency graph and snapshot design, if revisited: freeze gem5 by content at load, declared globs for project code.
- A per-user default worker port?
- Should `master` be deleted on GitHub now that `main` is the default?

## Next steps
1. Repoint local `main` at `origin/main`.
2. Decide the job-success rule and implement it (timeout or marker).
3. Reinstall the same version on every host's venv before the SIFT sweeps.
4. Port SIFT's `take_checkpoints.py` and `restore_checkpoints*.py` to `gem5Job` and the `load` script form.

## Pointers
- CLI entry: `experiment/cli/helper.py`; subcommands in `experiment/cli/{initialize,build,run,work,schedule,certs}.py`.
- Scheduler: `experiment/api/scheduler/{scheduler,daemon,console,dashboard,state}.py`; worker: `experiment/api/{worker,runner,host,work}.py`; TLS: `experiment/common/pki.py`.
- Job and experiment classes: `experiment/common/gem5_work.py`; project config: `experiment/common/config_util.py`.
- Tests: `tests/`; run with `pytest` from the repo root.
- Example load script: the sandbox `experiment-test/run_many.py` (outside the PhD repo); SIFT hosts in `projects/sift/hosts.py` and `arm_hosts.py`.
- The cancelled snapshot plan survives in the Claude plans directory (`let-s-try-to-improve-piped-stream.md`).

## Provenance
The package source and `pyproject.toml`, `git log`, the `experiment-test` sandbox, the Claude Code transcript of 2026-09-24 to 2026-10-03, the Claude memory file `version-scheme.md`, the scheduler state and logs observed on 2026-10-05, and the PhD migration plan.
