from .common_util import _parse_build_opt, kvm_support
from .configuration import _get_project_config

import argparse
import os
import platform
import re
import shutil

from types import SimpleNamespace


def parse_build_args(args):
    parser = argparse.ArgumentParser("Parse build command from helper.")
    parser.add_argument(
        "--build-opt",
        type=str,
        help="Build option to build gem5 with.",
        required=False,
    )
    parser.add_argument(
        "--threads",
        type=int,
        help="Number of threads to use to build gem5.",
        required=False,
    )
    parser.add_argument(
        "--bits-per-set",
        type=int,
        help="Number of bits per sit to use for Ruby compilation.",
        required=False,
    )
    parser.add_argument(
        "--gold",
        dest="gold",
        action="store_const",
        const=True,
        default=False,
        help="Enable linking with gold instead of ld.",
    )
    parser.add_argument(
        "--gprof",
        dest="gprof",
        action="store_const",
        const=True,
        default=False,
        help="Whether to compile gem5 with gprof.",
    )
    parser.add_argument(
        "--no-tcmalloc",
        dest="no_tcmalloc",
        action="store_const",
        const=True,
        default=False,
        help="Whether to compile gem5 without tcmalloc.",
    )

    return parser.parse_known_args(args)


def _get_config_as_namespace(old_config_path):
    build_ruby = False
    found_bits_per_set = None
    build_ruby_pattern = r"^RUBY=y"
    bits_per_set_pattern = r"^NUMBER_BITS_PER_SET=(\d+)"

    with open(old_config_path, "r") as config_file:
        for line in config_file.readlines():
            if re.match(build_ruby_pattern, line):
                build_ruby = True
            if match := re.search(bits_per_set_pattern, line):
                found_bits_per_set = int(match.group(1))

    if build_ruby:
        assert found_bits_per_set is not None
        bits_per_set = found_bits_per_set

    return SimpleNamespace(bits_per_set=bits_per_set)


def finalize_build_args(build_args, unknown_args):
    assert (unknown_args is None) or (len(unknown_args) == 0)

    configuration = _get_project_config()

    isa, protocol, opt = _parse_build_opt(build_args.build_opt, configuration)

    if build_args.threads is None:
        print(
            f"Number of threads not specified. "
            "Using 7/8 of available cores by default."
        )
        threads = int(os.cpu_count() * 7 / 8) or 1
    else:
        threads = build_args.threads

    gem5_dir = configuration["gem5_dir"]
    project_name = configuration["project_name"]
    base_dir = configuration["gem5_binary_base_dir"]

    path_parts = base_dir.split("/")[1:]
    assert os.path.exists(f"/{path_parts[0]}")
    assert os.access(f"/{path_parts[0]}", os.W_OK and os.R_OK)

    current_path = f"/{path_parts[0]}"
    for path_part in path_parts[1:]:
        test_path = os.path.join(current_path, path_part)
        if not os.path.exists(test_path):
            print(f"Path {test_path} does not exist, calling mkdir.")
            os.mkdir(test_path)
        else:
            print(f"Path {test_path} already exists.")
        current_path = test_path

    build_dir = os.path.join(
        current_path, f"{isa.lower()}-{protocol.lower()}-{opt.lower()}"
    )
    build_config = os.path.join(build_dir, "gem5.build")

    need_setconfig = False
    if os.path.exists(build_config):
        print(f"Path {build_config} already exists.")
        old_config = _get_config_as_namespace(f"{build_config}/config")
        if (
            build_args.bits_per_set is not None
            and old_config.bits_per_set != build_args.bits_per_set
        ):
            print("Bits per set changed. Need to setconfig.")
            need_setconfig = True
    else:
        print(f"Path {build_config} does not exist.")
        print("Need to setconfig.")
        need_setconfig = True

    ret = []
    if need_setconfig:
        command = (
            f"scons setconfig {build_dir}"
            f"{f' BUILD_ISA=y USE_{isa}_ISA=y' if isa != 'null' else ''}"
            f"{f' RUBY=y RUBY_PROTOCOL_{protocol}=y' if protocol != 'CLASSIC' else ''}"
        )
        if build_args.bits_per_set is not None:
            if protocol == "CLASSIC":
                raise ValueError(
                    f"`bits_per_set` defined with classic coherency protocol."
                )
            command += f" NUMBER_BITS_PER_SET={build_args.bits_per_set}"
        if (platform.machine() in kvm_support) and (
            kvm_support[platform.machine()] == isa
        ):
            command += f" USE_KVM=y KVM_ISA={kvm_support[platform.machine()]}"
        ret += [(command, gem5_dir)]

    command = f"scons -C {gem5_dir} gem5.{opt} -j {threads}"
    if build_args.gold:
        command += " --linker=gold"
    if build_args.gprof:
        command += " --gprof"
    if build_args.no_tcmalloc:
        command += " --without-tcmalloc"
    ret += [(command, build_dir)]

    return ret
