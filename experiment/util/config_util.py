from .defaults import config_file_name
from .project_config import ProjectConfiguration

import json

from pathlib import Path


def _get_project_config():
    path = Path.cwd().resolve()
    while True:
        if (path / config_file_name).exists():
            break
        else:
            if path.parent == path:
                raise RuntimeError(
                    f"Could not find a project config file anywhere "
                    f"in the path to current directory:\n\t{Path.cwd().resolve()}"
                )
            else:
                path = path.parent

    config_dict = {}
    with open(path / config_file_name, "r") as config_file:
        config_dict = json.load(config_file)
    return ProjectConfiguration.from_dict(config_dict)
