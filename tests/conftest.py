"""Pytest configuration and subprocess server management for PS-021 test suite.

Supports spawning real separate server processes, crashing them via termination/os._exit,
and restarting new processes on the same SQLite database to test real recovery.
"""

from __future__ import annotations

import os
import socket
import subprocess
import sys
import time
from pathlib import Path
from typing import Generator, Tuple

import httpx
import pytest

from aiori.client import AdapterAClient
from aiori.db import DatabaseManager


def find_free_port() -> int:
    """Find and return an available ephemeral TCP port."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class ServerProcessManager:
    """Manages the lifecycle of an external Adapter B server subprocess."""

    def __init__(self, db_path: Path, port: int):
        self.db_path = str(db_path)
        self.port = port
        self.host = "127.0.0.1"
        self.base_url = f"http://{self.host}:{self.port}"
        self.process: subprocess.Popen | None = None

    def start(self, wait_timeout: float = 10.0) -> None:
        """Start the server subprocess and wait for /health to be ready."""
        env = os.environ.copy()
        env["AIORI_DB_PATH"] = self.db_path
        env["AIORI_PORT"] = str(self.port)
        env["AIORI_HOST"] = self.host
        env["PYTHONUNBUFFERED"] = "1"

        cmd = [
            sys.executable,
            "-m",
            "uvicorn",
            "aiori.server:app",
            "--host",
            self.host,
            "--port",
            str(self.port),
            "--log-level",
            "warning",
        ]

        self.process = subprocess.Popen(
            cmd,
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            cwd=str(Path(__file__).parent.parent),
        )

        # Poll /health until server responds
        start_time = time.time()
        while time.time() - start_time < wait_timeout:
            if self.process.poll() is not None:
                _, stderr = self.process.communicate()
                raise RuntimeError(
                    f"Server process terminated prematurely with code {self.process.returncode}: {stderr.decode()}"
                )
            try:
                resp = httpx.get(f"{self.base_url}/health", timeout=0.5)
                if resp.status_code == 200:
                    return
            except Exception:
                pass
            time.sleep(0.1)

        self.kill()
        raise TimeoutError(f"Server failed to start and respond on {self.base_url} within {wait_timeout}s.")

    def kill(self) -> None:
        """Forcefully terminate the server process."""
        if self.process and self.process.poll() is None:
            self.process.kill()
            try:
                self.process.wait(timeout=2.0)
            except Exception:
                pass
        self.process = None

    def is_alive(self) -> bool:
        return self.process is not None and self.process.poll() is None


@pytest.fixture
def temp_db(tmp_path: Path) -> Path:
    """Fixture providing a fresh temporary SQLite database path."""
    return tmp_path / "test_aiori.db"


@pytest.fixture
def running_server(temp_db: Path) -> Generator[ServerProcessManager, None, None]:
    """Fixture that boots a live Adapter B subprocess and tears it down after test."""
    port = find_free_port()
    mgr = ServerProcessManager(temp_db, port)
    mgr.start()
    try:
        yield mgr
    finally:
        mgr.kill()


@pytest.fixture
def adapter_a(running_server: ServerProcessManager) -> Generator[AdapterAClient, None, None]:
    """Fixture providing an Adapter A client configured for the running server."""
    client = AdapterAClient(base_url=running_server.base_url)
    try:
        yield client
    finally:
        client.close()
