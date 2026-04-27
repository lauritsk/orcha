"""Orcha package."""

from importlib.metadata import PackageNotFoundError, version

try:
    __version__ = version("orcha")
except PackageNotFoundError:
    __version__ = "0.0.0"
