from setuptools import setup, find_packages

setup(
    name="aethel",
    version="0.1.0",
    packages=find_packages(),
    install_requires=[
        "typer[all]",
        "rich",
        "pydantic",
        "torch",
        "transformers",
        "peft",
    ],
    entry_points={
        "console_scripts": [
            "aethel=aethel.main:app",
        ],
    },
)
