isa_translator = {"x86": "X86", "arm": "ARM", "riscv": "RISCV", "null": "null"}

protocol_translator = {
    "classic": "CLASSIC",
    "chi": "CHI",
    "mesi2": "MESI_Two_Level",
    "mesi3": "MESI_Three_Level",
    "viper": "GPU_VIPER",
    "cxl": "CXL",
}

binary_opt_translator = {"debug": "debug", "opt": "opt", "fast": "fast"}

kvm_support = {"aarch64": "arm", "x86_64": "x86"}


def _parse_build_opt(build_opt, configuration):
    ret = {"isa": list(), "protocol": list(), "binary_opt": list()}

    if build_opt is not None:
        tokens = build_opt.split("-")
        for token in tokens:
            if token in isa_translator:
                ret["isa"].append(isa_translator[token])
            elif token in protocol_translator:
                ret["protocol"].append(protocol_translator[token])
            elif token in binary_opt_translator:
                ret["binary_opt"].append(binary_opt_translator[token])
                if len(ret["binary_opt"]) > 1:
                    raise RuntimeError(
                        f"Multiple binary options specified: {ret['binary_opt']}"
                    )
            else:
                raise RuntimeError(f"Unknown build option: {token}")

    if len(ret["isa"]) == 0:
        isas = configuration.get("default_isa")
        for isa in isas:
            ret["isa"].append(isa_translator[isa])
    if len(ret["protocol"]) == 0:
        protocols = configuration.get("default_protocol")
        for protocol in protocols:
            ret["protocol"].append(protocol_translator[protocol])
    if len(ret["binary_opt"]) == 0:
        opt = configuration.get("default_binary_opt")
        if isinstance(opt, list):
            raise RuntimeError(f"Multiple binary options specified: {opt}")
        ret["binary_opt"] = binary_opt_translator[opt]

    return sorted(ret["isa"]), sorted(ret["protocol"]), ret["binary_opt"]
