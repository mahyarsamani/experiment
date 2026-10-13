import json
import os
import shutil


from git import Repo
from importlib.resources import files
from pathlib import Path

from . import assets
from .log import warn
from .gem5_work import gem5ProjectConfiguration

config_file_name = "project_config.json"
gem5_repo_url = "https://github.com/gem5/gem5.git"


def _get_project_config(
    path: Path = Path.cwd().resolve(),
) -> gem5ProjectConfiguration:
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
    return gem5ProjectConfiguration.from_dict(config_dict)


def initialize_directories(gem5_proj_config: gem5ProjectConfiguration):
    path_config = gem5_proj_config.path_config()

    project_dir = path_config.project_dir()
    gem5_source_dir = path_config.gem5_source_dir()
    gem5_resource_json_path = path_config.gem5_resource_json_path()
    gem5_binary_base_dir = path_config.gem5_binary_base_dir()
    gem5_out_base_dir = path_config.gem5_out_base_dir()

    project_dir.mkdir(parents=True, exist_ok=True)

    components_dir = project_dir / "components"
    components_dir.mkdir(parents=True, exist_ok=True)
    (components_dir / "__init__.py").touch(exist_ok=True)

    scripts_dir = project_dir / "scripts"
    scripts_dir.mkdir(parents=True, exist_ok=True)
    scripts_util_dir = scripts_dir / "util"
    scripts_util_dir.mkdir(parents=True, exist_ok=True)
    (scripts_util_dir / "__init__.py").touch(exist_ok=True)

    if (scripts_util_dir / "decorators.py").exists():
        warn("`decorators.py` already exists. I am not going to overwrite it.")
    else:
        decorators_py = files(assets).joinpath("decorators.py")
        shutil.copy(
            decorators_py,
            scripts_util_dir / "decorators.py",
        )
    if (scripts_dir / "run_example.py").exists():
        warn(
            "`run_example.py` already exists. I am not going to overwrite it."
        )
    else:
        example_py = files(assets).joinpath("run_example.py")
        shutil.copy(
            example_py,
            scripts_dir / "run_example.py",
        )

    warn(
        "Created `components` and `scripts` directories. "
        "I recommend putting all the components you build "
        "(e.g. cache hierarchies, processors, memories, boards, etc.) "
        "in the `components` directory. I also recommend to put all your "
        "run_scripts in `scripts` directory. I have found this to be a "
        "good practice. Additionally, I recommend using two decorators "
        "to expose the project directory and record the arguments you pass"
        " to the run function. I have already added the files needed for "
        "them under `scripts/util`. Import them like below:\n"
        "from util.decorators import expose_project_dir, record_args\n"
        "I also will put an example in scripts directory that "
        "uses traffic generators with all the decorators."
    )

    if not gem5_source_dir.exists():
        gem5_source_dir.mkdir(parents=True, exist_ok=True)
        warn(
            f"{gem5_source_dir} does not exist. "
            "I am cloning mainline gem5 repository. "
            "If you want to use a different version, "
            "you should manually put it there."
        )
        Repo.clone_from(gem5_repo_url, gem5_source_dir)
    else:
        if not any(gem5_source_dir.iterdir()):
            warn(
                f"{gem5_source_dir} is empty. "
                "I am cloning mainline gem5 repository. "
                "If you want to use a different version, "
                "you should manually put it there."
            )
            Repo.clone_from(gem5_repo_url, gem5_source_dir)
    if (
        gem5_resource_json_path is not None
        and not gem5_resource_json_path.exists()
    ):
        raise ValueError(
            "`gem5_resource_json_path` must be set to a valid path."
        )
    gem5_binary_base_dir.mkdir(parents=True, exist_ok=True)
    gem5_out_base_dir.mkdir(parents=True, exist_ok=True)

    try:
        os.symlink(
            gem5_binary_base_dir,
            gem5_source_dir / "build",
            target_is_directory=True,
        )
    except FileExistsError:
        warn(
            f"{gem5_source_dir / 'build'}."
            "is already a symlink. "
            "I am not going to overwrite it. If it is not correct, "
            "You have to correct it manually."
        )
    except Exception as err:
        raise err

    try:
        os.symlink(
            gem5_out_base_dir,
            project_dir / "gem5-out",
            target_is_directory=True,
        )
    except FileExistsError:
        warn(
            f"{project_dir / 'gem5-out'}."
            "is already a symlink. "
            "I am not going to overwrite it. If it is not correct, "
            "You have to correct it manually."
        )
    except Exception as err:
        raise err

    with open(project_dir / config_file_name, "w") as config_json:
        json.dump(
            gem5_proj_config.to_dict(), config_json, indent=2, default=str
        )
