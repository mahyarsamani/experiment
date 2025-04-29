from setuptools import setup, find_packages

setup(
    name="experiment",
    version="0.5",
    description="Experimentation tooling for gem5",
    author="Mahyar Samani",
    author_email="msamani@ucdavis.edu",
    url="https://github.com/mahyarsamani/experiment",
    packages=find_packages(),
    package_data={"experiment.util": ["data/*.json"]},
    entry_points={
        "console_scripts": ["helper = experiment.cli.helper:main_function"]
    },
)
