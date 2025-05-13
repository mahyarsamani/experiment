import json
import sys

from pathlib import Path


def expose_prject_dir(run):
    def wrapper(*args, **kwargs):
        proj_dir = Path(__file__).resolve().parent.parent
        sys.path.append(proj_dir)

        run(*args, **kwargs)

    return wrapper


def record_args(run):
    def wrapper(*args, **kwargs):
        try:
            from m5 import options

            outdir = Path(options.outdir).resolve()
        except ImportError:
            raise RuntimeError("`record_args` can only be used with gem5.")

        params = dict()
        arg_names = run.__code__.co_varnames[: run.__code__.co_argcount]
        for arg_name, arg_value in zip(arg_names, args):
            params[arg_name] = arg_value
        if kwargs:
            raise ValueError(
                "`record_args` does not allow keyword arguments to the run "
                "function. If you need to allow optional arguments, "
                "you should do that using argparse with default values."
            )

        with open(outdir / "params.json", "w") as par_ser:
            json.dump(params, par_ser, indent=2)
        (outdir / "run_function_called").touch()
        run(*args, **kwargs)
        (outdir / "simulation_finished").touch()

    return wrapper
