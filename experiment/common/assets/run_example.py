import argparse

from util.decorators import clear_outdir, expose_project_dir, record_args


@expose_project_dir
@clear_outdir
@record_args
def run_example(num_generator_cores):
    """
    `expose_project_dir` decorator exposes the project directory to the run
    function. In other words, you can import any modules from directories that
    have `__init__.py` files in them. Although in this example, I import
    everything from gem5, when you develop new components, you can put them in
    a directory with `__init__.py` file and import them here as long as the
    directory is in the project directory. I found it best to separate run
    scripts and components. That's why the experiment directory creates
    directories for components and scripts.
    """
    from gem5.components.boards.test_board import TestBoard
    from gem5.components.cachehierarchies.classic.private_l1_shared_l2_cache_hierarchy import (
        PrivateL1SharedL2CacheHierarchy,
    )
    from gem5.components.memory import DualChannelDDR4_2400
    from gem5.components.processors.linear_generator import LinearGenerator

    from gem5.simulate.simulator import Simulator

    board = TestBoard(
        clk_freq="1GHz",
        generator=LinearGenerator(num_cores=num_generator_cores),
        cache_hierarchy=PrivateL1SharedL2CacheHierarchy(
            l1d_size="32KiB", l1i_size="32KiB", l2_size="512KiB"
        ),
        memory=DualChannelDDR4_2400(),
    )

    simulator = Simulator(board=board)
    simulator.run()


def get_inputs():
    parser = argparse.ArgumentParser(
        description="Run a gem5 simulation with a specified number of generator cores."
    )
    parser.add_argument(
        "num_generator_cores",
        type=int,
        help="Number of generator cores to use.",
    )

    args = parser.parse_args()
    return [args.num_generator_cores]


if __name__ == "__m5_main__":
    run_example(*get_inputs())
