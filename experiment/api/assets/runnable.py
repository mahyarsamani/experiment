def runnable(run):
    def wrapper(*args, **kwargs):
        from m5 import options
        from m5.util import inform

        import json, os, sys
        from pathlib import Path

        here = Path(__file__)
        to_append = str(here.resolve().parent.parent.parent)
        sys.path.append(to_append)

        params = dict()
        arg_names = run.__code__.co_varnames[: run.__code__.co_argcount]
        for arg_name, arg_value in zip(arg_names, args):
            params[arg_name] = arg_value
        if kwargs:
            raise ValueError(
                "You should not pass any keyword arguments. "
                "If you need to allow optional arguments, "
                "you should do that using argparse."
            )

        with open(os.path.join(options.outdir, "params.json"), "w") as par_ser:
            json.dump(params, par_ser, indent=2)
        (Path(options.outdir) / "run_function_called").touch()
        run(*args, **kwargs)
        (Path(options.outdir) / "simulation_finished").touch()

    return wrapper
