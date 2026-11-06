"""Beacon version, read from the installed package metadata (pyproject.toml)."""

from importlib.metadata import PackageNotFoundError, version

try:
    VERSION = version("beacon")
    INSTALLED = True
except PackageNotFoundError:  # running from the sources without `pip install -e .`
    VERSION = "0.0.0+dev"
    INSTALLED = False
