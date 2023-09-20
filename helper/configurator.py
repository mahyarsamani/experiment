import os
import json

isa_translator = {"x86": "X86", "arm": "ARM", "riscv": "RISCV"}

protocol_translator = {
    "chi": "CHI",
    "mesi_two_level": "MESI_Two_Level",
    "mesi_three_level": "MESI_Three_Level",
    "mi_example": "MI_example",
}

binary_opt_translator = {"debug": "debug", "opt": "opt", "fast": "fast"}

translators = [
    (isa_translator, "isa"),
    (protocol_translator, "protocol"),
    (binary_opt_translator, "binary_opt"),
]


def _get_settings_list():
    ret = None
    with open(
        os.path.join(
            os.path.dirname(os.path.abspath(__file__)), "settings.json"
        ),
        "r",
    ) as settings_file:
        ret = json.load(settings_file)
    return ret


def _get_default_settings():
    ret = None
    with open(
        os.path.join(
            os.path.dirname(os.path.abspath(__file__)), "defaults.json"
        ),
        "r",
    ) as defaults_file:
        ret = json.load(defaults_file)
    return ret


def _get_automate_settings():
    ret = None
    with open(
        os.path.join(
            os.path.dirname(os.path.abspath(__file__)), "automate.json"
        ),
        "r",
    ) as automate_file:
        ret = json.load(automate_file)
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
                f"in the path to current directory: {os.getcwd()}"
            )

    with open(os.path.join(path, "project_config.json"), "r") as config_file:
        configuration = json.load(config_file)

    settings = _get_settings_list()
    defaults = _get_default_settings()
    for setting in settings:
        if not setting in configuration:
            if not setting in defaults:
                raise RuntimeError(
                    f"No value set for {setting}. There is "
                    "no global default for this setting either."
                )
            else:
                print(
                    f"Setting {setting} not specified. Defaulting "
                    f"to global default {defaults[setting]}"
                )
                configuration[setting] = defaults[setting]

    return configuration


def _parse_build_opt(build_opt, configuration):
    ret = {}
    if build_opt is None:
        ret["isa"] = isa_translator[configuration["default_isa"]]
        ret["protocol"] = protocol_translator[
            configuration["default_protocol"]
        ]
        ret["binary_opt"] = binary_opt_translator[
            configuration["default_binary_opt"]
        ]
    else:
        build_opts = build_opt.split("-")
        for opt in build_opts:
            for translator, key in translators:
                if opt in translator:
                    if key in ret:
                        raise RuntimeError(
                            f"More than one option passed for {key}."
                        )
                    ret[key] = translator[opt]

    for translator, key in translators:
        if not key in ret:
            ret[key] = configuration[f"default_{key}"]

    return ret["isa"], ret["protocol"], ret["binary_opt"]
