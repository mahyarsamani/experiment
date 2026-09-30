import subprocess

from pathlib import Path
from typing import List
from .log import info


def run_command(commands: List[str], cwd: Path) -> int:
    """Run `commands` in `cwd` and return its exit code."""
    info(f"Running: {' '.join(commands)} (in {cwd})")
    return subprocess.run(commands, cwd=cwd).returncode
