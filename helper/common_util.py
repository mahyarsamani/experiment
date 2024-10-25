isa_translator = {"x86": "X86", "arm": "ARM", "riscv": "RISCV", "null": "null"}

protocol_translator = {
    "classic": "CLASSIC",
    "chi": "CHI",
    "mesi_two_level": "MESI_TWO_LEVEL",
    "mesi_three_level": "MESI_THREE_LEVEL",
    "mi_example": "MI_EXAMPLE",
}

binary_opt_translator = {"debug": "debug", "opt": "opt", "fast": "fast"}

translators = [
    (isa_translator, "isa"),
    (protocol_translator, "protocol"),
    (binary_opt_translator, "binary_opt"),
]

kvm_support = {"aarch64": "arm", "x86_64": "x86"}


def _parse_build_opt(build_opt, configuration):
    ret = {}
    if not build_opt is None:
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
            default = translator[configuration[f"default_{key}"]]
            print(f"No {key} specified. Using {default} for {key}.")
            ret[key] = default

    return ret["isa"], ret["protocol"], ret["binary_opt"]
