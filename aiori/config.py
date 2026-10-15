"""Configuration defaults and environment variable resolution for PS-021."""

import os
from pathlib import Path

DEFAULT_DB_PATH = os.environ.get("AIORI_DB_PATH", str(Path(__file__).parent.parent / "aiori_state.db"))
DEFAULT_SERVER_HOST = os.environ.get("AIORI_HOST", "127.0.0.1")
DEFAULT_SERVER_PORT = int(os.environ.get("AIORI_PORT", "8000"))
