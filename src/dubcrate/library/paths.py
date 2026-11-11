from pathlib import Path

from platformdirs import user_data_dir


def default_library_path() -> Path:
    """Where Dubcrate's own library database lives (never rekordbox's)."""
    return Path(user_data_dir("Dubcrate", appauthor=False)) / "library.db"
