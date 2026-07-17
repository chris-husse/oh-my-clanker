from importlib.metadata import PackageNotFoundError, version

try:
    __version__ = version("omc")
except PackageNotFoundError:  # dev checkout, not installed
    __version__ = "0.0.0-dev"
