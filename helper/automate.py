import os
import subprocess


def _automate_project_name():
    print(
        "No value set for project_name. "
        "Setting project_name to the name of the current directory."
    )
    return os.path.basename(os.path.abspath(os.getcwd()))


def _automate_gem5_dir():
    print("Nothing set for gem5_dir.")
    if not "gem5" in os.listdir(os.path.abspath(os.getcwd())):
        print("No gem5 directory found in the current directory.")
        subprocess.run(
            "git clone https://www.github.com/gem5/gem5.git", shell=True
        )
    else:
        print("Found gem5 director in the current directory.")
    return os.path.join(os.path.abspath(os.getcwd()), "gem5")
