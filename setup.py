from setuptools import setup, find_packages

setup(
    name="experiment",
    version="0.2.1",
    description="Experimentation tooling for gem5",
    author="Mahyar Samani",
    author_email="msamani@ucdavis.edu",
    url="https://github.com/mahyarsamani/experiment",
    packages=find_packages(),
    package_data={"helper": ["data/*.json"]},
    entry_points={"console_scripts": ["helper = helper.helper:main_function"]},
)
