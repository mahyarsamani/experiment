# experiment memory archive

Older or superseded material, kept for the record.
Same sections as `memory.md`, no line limit, newest first within a section.

## What this is
- Before 2026-09-30 the package was `setup.py`-based, with a Flask file server per worker (port 9101), unauthenticated rpyc on all interfaces, polling without persistence, and `gem5FSSimulation` as the job class.

## Current state (historical snapshots)
- 2026-10-03: a plan for build-before-simulate dependencies and content-addressed code snapshots (`experiment/api/snapshot.py`, build provenance written by `helper build`, `reload` moving queued jobs to a new snapshot) was drafted and then cancelled as too complicated.
- 2026-10-01: first deployment to perle failed twice, on the certificate name and on port 9100; fixed by `--san` support and a busy-port message.
- 2026-09-30: versions went 0.27.0 (reverted) to 0.26.09.30 to 0.26.09.1 as the scheme settled.
- 2026-01-05 to 2026-01-07: `cascade.scheduler.log` in the repo records SIFT checkpoint-restore jobs launched and killed on cascade with the old scheduler.
- 2026-01-29: Gemini Antigravity's code tracker shows edits to `build.py` and `run.py`, the only trace of the tool in the Gemini history.

## Decisions
- 2026-10-03 (cancelled with the plan): build per host and pin simulations to it, because `/scr` is per machine; dependents of a failed job wait as "blocked"; freeze gem5 by content (commit, diff, untracked files) at load.
- 2026-09-30: the version scheme was first `0.YY.MM.DD`, changed the next day to `0.YY.MM.N`.
- 2026-09-30: `stop --kill-jobs` keeps experiments so they can be reset; no status toolbar in the console.
- 2026-09-30: on restart, check pid status so running jobs are not relaunched; job truth lives on disk in each outdir.

## Constraints and gotchas
- `psutil.Process(-1)` raises `ValueError`, not a `psutil.Error`.
- If a scheduler tick exceeded the poll interval, console commands were never processed (fixed in 0f17708).
- Old workers' jobs are invisible to the new scheduler after the rewrite.
- SSH from cascade to perle stops at host-key verification the first time.
- The sandbox `experiment-test` uses build `main` with `gem5.fast` from gem5 `stable`, outputs under the gem5 output directory `exp-test/linear-sweep/<job id>/`.

## Open questions (historical)
- Keep a `setup.py` shim for old installers? (Offered, never requested.)

## Next steps (historical)
- From the 2026-10-02 discussion: per-job wall-clock timeout, stall detection, and a `simulation_finished` marker were proposed; none chosen.

## Pointers
- The 2026-09-24 code review and the 2026-09-30 plan live only in the Claude transcript for the old `experiment` project folder.

## Provenance
Same sources as `memory.md`.
