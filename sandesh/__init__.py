"""sandesh — SQLite-backed multi-project messaging for cooperating agent sessions."""

from importlib import metadata as _metadata

try:
    __version__ = _metadata.version("sandesh-relay")
except _metadata.PackageNotFoundError:  # running from a source checkout, not installed
    __version__ = "0+unknown"

