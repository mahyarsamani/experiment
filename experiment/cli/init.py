from ..util.common_util import (
    binary_opt_translator,
    isa_translator,
    protocol_translator,
)
from ..util.configuration import (
    _get_automate_settings,
    _get_default_settings,
    _get_settings_list,
)

import argparse
import json
import os


def parse_init_args(args):
    parser = argparse.ArgumentParser(
        description="Parse init command from helper."
    )
    parser.add_argument("project_name", type=str, help="Name of the project.")
    parser.add_argument(
        "gem5_binary_base_dir",
        type=str,
        help="Path to the directory to store the gem5 binaries. "
        "Typically shared among projects.",
    )
    parser.add_argument(
        "gem5_out_base_dir",
        type=str,
        help="Path to the directory to store the gem5 outputs "
        "(simout, simerr, stats, ...). Typically shared among projects.",
    )
    parser.add_argument(
        "--gem5-dir",
        type=str,
        help="Path to the gem5 directory.",
        required=False,
    )
    parser.add_argument(
        "--default-isa",
        type=str,
        help="Default isa for the project.",
        required=False,
        choices=list(isa_translator.keys()),
    )
    parser.add_argument(
        "--default-protocol",
        type=str,
        help="Default protocol for the project.",
        required=False,
        choices=list(protocol_translator.keys()),
    )
    parser.add_argument(
        "--default-binary-opt",
        type=str,
        help="Default binary option to use.",
        required=False,
        choices=list(binary_opt_translator.keys()),
    )

    return parser.parse_known_args(args)


def finalize_init_args(init_args, unknown_args):
    assert (unknown_args is None) or (len(unknown_args) == 0)

    configuration = {}
    settings = _get_settings_list()
    defaults = _get_default_settings()
    automate = _get_automate_settings()

    for setting in settings:
        if not getattr(init_args, setting) is None:
            configuration[setting] = getattr(init_args, setting)
        elif setting in defaults:
            print(
                f"Setting {setting} not specified. "
                f"Defaulting to {defaults[setting]}."
            )
            configuration[setting] = defaults[setting]
        elif setting in automate:
            configuration[setting] = automate[setting]()
        else:
            raise RuntimeError(f"Don't know what to do with {setting}.")

    with open("project_config.json", "w") as config_file:
        json.dump(configuration, config_file, indent=2)

    return [
        (
            f"ln -sf {configuration['gem5_binary_base_dir']} {configuration['gem5_dir']}/build",
            os.getcwd(),
        ),
        (
            f"ln -sf {configuration['gem5_out_base_dir']} {os.getcwd()}/gem5-out",
            os.getcwd(),
        ),
    ]
