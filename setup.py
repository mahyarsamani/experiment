from setuptools import setup, find_packages

setup(
    name="experiment",
    version="0.1",
    description="Experimentation tooling for gem5",
    author="Mahyar Samani",
    author_email="msamani@ucdavis.edu",
    url="https://github.com/mahyarsamani/experiment",
    packages=find_packages(),
    install_requires=["os", "argparse", "json", "subprocess"],
    entry_points={
        "console_scripts": ["helper = experiment.helper.helper:main_function"]
    },
)
