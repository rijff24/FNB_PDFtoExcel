import tempfile
from pathlib import Path


def create_temp_path(suffix: str) -> Path:
    fd, path = tempfile.mkstemp(suffix=suffix)
    Path(path).unlink(missing_ok=True)
    return Path(path)
