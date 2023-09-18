import os

isa_translator = {"x86": "X86", "arm": "ARM", "riscv": "RISCV"}

protocol_translator = {
    "chi": "CHI",
    "mesi_two_level": "MESI_Two_Level",
    "mesi_three_level": "MESI_Three_Level",
    "mi_example": "MI_example",
}

binopt_translator = {"debug": "debug", "opt": "opt", "fast": "fast"}


def _get_project_config():
    configuration = {}
    path = os.path.abspath(os.getcwd())

    while True:
        if "project_config" in os.listdir(path):
            break
        else:
            path = os.path.dirname(path)

        if path == "/":
            raise RuntimeError(
                f"Could not find a project config file anywhere "
                "in the path to current directory: {os.getcwd()}"
            )

    with open(os.path.join(path, "project_config"), "r") as config_file:
        for line in config_file.readlines():
            name, value = [elem.strip().lower() for elem in line.split("=")]
            configuration[name] = value

    if not "gem5_dir" in configuration:
        if not os.getenv("GEM5_DIR") is None:
            configuration["gem5_dir"] = os.getenv("GEM5_DIR")
        if "gem5" in os.listdir(path):
            configuration["gem5_dir"] = str(os.path.join(path, "gem5"))

        if not "gem5_dir" in configuration:
            raise RuntimeError(
                "No GEM5_DIR defined or no gem5 "
                f"directory in the project path: {path}"
            )

    if not "project_name" in configuration:
        if not os.getenv("PROJECT_NAME") is None:
            configuration["project_name"] = os.getenv("PROJECT_NAME")
        else:
            configuration["project_name"] = os.path.basename(path)

    if not "gem5_binary_base_dir" in configuration:
        configuration["gem5_binary_base_dir"] = os.getenv(
            "GEM5_BINARY_BASE_DIR", "/scr/gem5-binary"
        )

    if not "gem5_out_base_dir" in configuration:
        configuration["gem5_out_base_dir"] = os.getenv(
            "GEM5_OUT_BASE_DIR", "/scr/gem5-out"
        )

    if not "default_isa" in configuration:
        configuration["default_isa"] = os.getenv("DEFAULT_ISA", "arm")

    if not "default_protocol" in configuration:
        configuration["default_protocol"] = os.getenv(
            "DEFAULT_PROTOCOL", "chi"
        )

    if not "default_binary_opt" in configuration:
        configuration["default_binary_opt"] = os.getenv(
            "DEFAULT_BINARY_OPT", "fast"
        )

    return configuration


def _parse_build_opt(build_opt, configuration):
    build_opts = build_opt.split("-")
    assert build_opts[0].lower() in isa_translator
    isa = isa_translator[build_opts[0].lower()]

    # TODO: Add support for specifying coherence.
    if build_opt is None:
        isa = configuration["default_isa"]
        protocol = configuration["default_protocol"]
        opt = configuration["default_binary_opt"]
    if len(build_opts) == 1:
        protocol = configuration["default_protocol"]
        opt = configuration["default_binary_opt"]
    elif len(build_opts) == 2:
        assert (
            build_opts[1].lower() in protocol_translator
            or build_opts[1].lower() in binopt_translator
        )
        if build_opts[1].lower() in protocol_translator:
            protocol = protocol_translator[build_opts[1].lower()]
            opt = configuration["default_binary_opt"]
        else:
            protocol = configuration["default_protocol"]
            opt = binopt_translator[build_opts[1].lower()]
    elif len(build_opts) == 3:
        assert (
            build_opts[1].lower() in protocol_translator
            and build_opts[2].lower() in binopt_translator
        )
        protocol = protocol_translator[build_opts[1]]
        opt = binopt_translator[build_opts[2]]
    else:
        error = (
            f"Please specify your build_opt in "
            "[isa]-[protocol]-[binary-opt] or [isa]-[protocol] or "
            "[isa]-[binary-opt] or [isa] format. Default value for "
            'protocol is "CHI" and default value for binary-opt '
            'is "fast". Here are all the options you can use:\n'
        )
        error += f"isa: {isa_translator.keys()}\n"
        error += f"protocol: {protocol_translator.keys()}\n"
        error += f"binary-opt: {binopt_translator.keys()}"
        print(error)
        exit()
    return isa, protocol, opt
