import argparse

from ..util.project_config import (
    ISA,
    Protocol,
    Linker,
    BinaryOpt,
    ProjectConfiguration,
)


def parse_initialize_args(args):
    parser = argparse.ArgumentParser(
        description="Parse initialize command from helper."
    )
    parser.add_argument("project_name", type=str, help="Name of the project.")
    parser.add_argument(
        "project_dir",
        type=str,
        help="Path to the base directory of the project.",
    )
    parser.add_argument(
        "gem5_source_dir",
        type=str,
        help="Path to the gem5 directory.",
    )
    parser.add_argument(
        "gem5_binary_base_dir",
        type=str,
        help="Path to the base directory to store the gem5 binaries. "
        "Recommend to make it project specific. Also recommend to use "
        "a directory under /scr for it.",
    )
    parser.add_argument(
        "gem5_out_base_dir",
        type=str,
        help="Path to the directory to store the gem5 outputs "
        "(simout, simerr, stats, ...). Recommend to make it project specific. "
        "Also recommend to use a directory under /home for it.",
    )
    parser.add_argument(
        "default_isas",
        type=str,
        help="Comma separated list of isas to compile by default. "
        f"Choose from: {ISA.return_all_values()}",
    )
    parser.add_argument(
        "default_protocols",
        type=str,
        help="Comma separated list of isas to compile by default. "
        f"Choose from: {Protocol.return_all_values()}",
    )
    parser.add_argument(
        "default_binary_opt",
        type=str,
        help="Default binary option to use when compiling.",
        choices=BinaryOpt.return_all_values(),
    )
    parser.add_argument(
        "default_linker",
        type=str,
        help="Default linker to use when compiling.",
        choices=Linker.return_all_values(),
    )
    parser.add_argument(
        "default_bits_per_set",
        type=int,
        help="Default bits per set. When passing `NOTHING` as "
        "protocol you should pass _1 for this. If you have specified at least "
        "one protocol, default value is 64.",
    )
    parser.add_argument(
        "default_threads",
        type=int,
        help="Default number of threads to use when compiling.",
    )

    parser.add_argument(
        "--gem5_resource_json_path",
        type=str,
        help="Path to the gem5 resource JSON file.",
        default="/dev/null",
        required=False,
    )

    return parser.parse_known_args(args)


def finalize_initialize_args(initialize_args, unknown_args):
    assert (unknown_args is None) or (len(unknown_args) == 0)

    config = ProjectConfiguration(
        initialize_args.project_name,
        initialize_args.project_dir,
        initialize_args.gem5_source_dir,
        initialize_args.gem5_resource_json_path,
        initialize_args.gem5_binary_base_dir,
        initialize_args.gem5_out_base_dir,
        initialize_args.default_isas,
        initialize_args.default_protocols,
        initialize_args.default_binary_opt,
        initialize_args.default_linker,
        initialize_args.default_bits_per_set,
        initialize_args.default_threads,
    )

    config.initialize_directories()
