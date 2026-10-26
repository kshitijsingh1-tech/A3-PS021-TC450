"""Configuration defaults and environment variable resolution for PS-021."""

import os
from pathlib import Path

is_vercel = os.environ.get("VERCEL") == "1" or "VERCEL" in os.environ
default_db = "/tmp/aiori_state.db" if is_vercel else str(Path(__file__).parent.parent / "aiori_state.db")
DEFAULT_DB_PATH = os.environ.get("AIORI_DB_PATH", default_db)
DEFAULT_SERVER_HOST = os.environ.get("AIORI_HOST", "127.0.0.1")
DEFAULT_SERVER_PORT = int(os.environ.get("AIORI_PORT", "8000"))

