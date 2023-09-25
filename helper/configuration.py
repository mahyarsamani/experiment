import os
import json
import importlib
import pkg_resources


def _get_settings_list():
    ret = None

    path = pkg_resources.resource_filename("helper", "data/settings.json")
    with open(
        pkg_resources.resource_filename("helper", "data/settings.json"), "r"
    ) as settings_file:
        ret = json.load(settings_file)
    return ret


def _get_default_settings():
    ret = None
    with open(
        pkg_resources.resource_filename("helper", "data/defaults.json"), "r"
    ) as defaults_file:
        ret = json.load(defaults_file)
    return ret


def _get_automate_settings():
    ret = {}

    with open(
        pkg_resources.resource_filename("helper", "data/automate.json"), "r"
    ) as automate_file:
        automate = json.load(automate_file)

    for setting, automation in automate.items():
        ret[setting] = getattr(
            importlib.import_module("helper.automate"), automation
        )
    return ret


def _get_project_config():
    configuration = {}
    path = os.path.abspath(os.getcwd())

    while True:
        if "project_config.json" in os.listdir(path):
            break
        else:
            path = os.path.dirname(path)

        if path == "/":
            raise RuntimeError(
                f"Could not find a project config file anywhere "
                f"in the path to current directory:\n\t{os.getcwd()}"
            )

    with open(os.path.join(path, "project_config.json"), "r") as config_file:
        configuration = json.load(config_file)

    settings = _get_settings_list()

    for setting in settings:
        if not setting in configuration:
            raise RuntimeError(
                f"Setting {setting} not set in configuration file. "
                "Please run helper init and set the parameters."
            )
    return configuration
