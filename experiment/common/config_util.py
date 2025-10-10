from . import assets
from .cmd_util import run_command
from .gem5_work import (
    gem5BuildConfiguration,
    gem5ProjectConfiguration,
)

import json
import os
import shutil


from git import Repo
from importlib.resources import files
from pathlib import Path
from warnings import warn

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
        "to the run function. I have already added the files needed for "
        "them under `scripts/util`. Import them like below:\n"
        "from util.decorators import expose_prject_dir, record_args\n"
        "I also will put an example in scripts directory that"
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


def configure_build_directory(
    gem5_source_dir: Path,
    build_dir: Path,
    build_config: gem5BuildConfiguration,
) -> None:
    old_build_config_path = build_dir / "gem5.build" / "compile_config.json"

    need_setconfig = True
    if old_build_config_path.exists():
        warn(f"{old_build_config_path} already exists. Checking for changes.")
        with open(old_build_config_path, "r") as config_file:
            old_build_config_dict = json.load(config_file)
            need_setconfig = not build_config.is_same(old_build_config_dict)

    if not need_setconfig:
        warn("No need to set config as it matches current build config.")
    else:
        warn("Setting config as it does not match current build config.")
        shutil.rmtree(build_dir, ignore_errors=True)
        build_dir.mkdir(parents=True, exist_ok=True)
        setconfig_cmd = build_config.make_setconfig_command(build_dir)
        run_command(["bash", "-c", setconfig_cmd], gem5_source_dir)
        with open(old_build_config_path, "w") as config_file:
            json.dump(
                build_config.dump_config(), config_file, indent=2, default=str
            )
    return build_dir
