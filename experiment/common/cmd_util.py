import subprocess

from pathlib import Path
from typing import List
from warnings import warn


def run_command(commands: List[str], cwd: Path) -> None:
    warn(f"Running command: {" ".join(commands)} in {cwd}")
    subprocess.run(commands, cwd=cwd)
