"""Codex owns authentication. No credential file, login URL or email is read or logged."""

import json
import os
import queue
import subprocess
import tempfile
import threading
import time
from pathlib import Path

from airalert.news.codex_runner import CodexError, codex_command, kill_tree, safe_env, spawn
from airalert.schemas import AuthStatus


class Auth:
    def __init__(self) -> None:
        self.models: dict[str, set[str]] = {}
        self._login: subprocess.Popen[str] | None = None
        self._login_started = 0.0
        self._lock = threading.RLock()
        self._cached: AuthStatus | None = None
        self._checked = 0.0

    def discover(self) -> dict[str, set[str]]:
        """Request the paginated model catalog via private stdio app-server only."""
        with tempfile.TemporaryDirectory(prefix="airalert-catalog-") as folder:
            proc = spawn(
                [*codex_command(), "app-server", "--stdio", "-c", "mcp_servers={}"], Path(folder)
            )
            responses: queue.Queue[str] = queue.Queue(maxsize=128)

            def reader() -> None:
                assert proc.stdout is not None
                for line in proc.stdout:
                    try:
                        responses.put(line, timeout=1)
                    except queue.Full:
                        break

            threading.Thread(target=reader, daemon=True).start()
            assert proc.stdin is not None
            deadline = time.monotonic() + 40

            def request(body: dict[str, object]) -> dict[str, object]:
                proc.stdin.write(json.dumps(body) + "\n")
                proc.stdin.flush()
                if "id" not in body:
                    return {}
                while time.monotonic() < deadline:
                    try:
                        value = json.loads(
                            responses.get(timeout=max(0.01, deadline - time.monotonic()))
                        )
                    except queue.Empty as exc:
                        raise CodexError("Codex model discovery timed out") from exc
                    if value.get("id") == body["id"]:
                        if "error" in value or not isinstance(value.get("result"), dict):
                            raise CodexError("Codex model discovery failed")
                        return value["result"]
                raise CodexError("Codex model discovery timed out")

            try:
                request(
                    {
                        "id": 1,
                        "method": "initialize",
                        "params": {
                            "clientInfo": {"name": "airalert_news", "version": "0.1"},
                            "capabilities": {"experimentalApi": True},
                        },
                    }
                )
                request({"method": "initialized", "params": {}})
                models: dict[str, set[str]] = {}
                cursor = None
                for page in range(10):
                    result = request(
                        {
                            "id": page + 2,
                            "method": "model/list",
                            "params": {
                                "limit": 100,
                                "includeHidden": False,
                                "cursor": cursor,
                            },
                        }
                    )
                    for row in result.get("data", []):
                        if row.get("hidden"):
                            continue
                        models[row["model"]] = {
                            e["reasoningEffort"] for e in row["supportedReasoningEfforts"]
                        }
                    cursor = result.get("nextCursor")
                    if not cursor:
                        self.models = models
                        return models
                raise CodexError("Codex model catalog exceeded the pagination limit")
            finally:
                kill_tree(proc)

    def status(self, refresh: bool = False) -> AuthStatus:
        with self._lock:
            if self._login is not None and (
                self._login.poll() is not None or time.monotonic() - self._login_started > 300
            ):
                kill_tree(self._login)
                self._login = None
                refresh = True
            if not refresh and self._cached is not None and time.monotonic() - self._checked < 30:
                return self._cached.model_copy(
                    update={"login_in_progress": self._login is not None}
                )
            try:
                command = codex_command()
            except CodexError:
                return AuthStatus(
                    codex_installed=False,
                    logged_in=False,
                    login_in_progress=False,
                    available_models=[],
                    message="Install Codex CLI to enable news",
                )
            try:
                with tempfile.TemporaryDirectory(prefix="airalert-auth-") as folder:
                    proc = spawn([*command, "login", "status"], Path(folder), status_output=True)
                    # Inspect only the auth-method marker, then discard possible key excerpts.
                    try:
                        stdout, stderr = proc.communicate(timeout=15)
                    finally:
                        kill_tree(proc)
                    logged_in = proc.returncode == 0 and "Logged in using ChatGPT" in (
                        stdout + stderr
                    )
                message = None if logged_in else "Log in to Codex with ChatGPT to enable news"
                if logged_in and (refresh or not self.models):
                    self.discover()
            except (CodexError, OSError, subprocess.TimeoutExpired, ValueError):
                logged_in = False
                message = "Codex status or model discovery is unavailable"
            self._cached = AuthStatus(
                codex_installed=True,
                logged_in=logged_in,
                login_in_progress=self._login is not None,
                available_models=sorted(self.models),
                message=message,
            )
            self._checked = time.monotonic()
            return self._cached

    def login(self) -> AuthStatus:
        with self._lock:
            if self._login is None or self._login.poll() is not None:
                # DEVNULL prevents URL output from entering a pipe, log or response.
                self._login = subprocess.Popen(
                    [*codex_command(), "login"],
                    stdin=subprocess.DEVNULL,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    cwd=tempfile.gettempdir(),
                    env=safe_env(),
                    creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
                    start_new_session=os.name != "nt",
                )
                self._login_started = time.monotonic()
            return self.status()

    def cancel(self) -> AuthStatus:
        self.stop_login()
        return self.status(refresh=True)

    def stop_login(self) -> None:
        """Close only this app's login child without checking auth or discovering models."""
        with self._lock:
            if self._login is not None:
                kill_tree(self._login)
                self._login = None

    def logout(self) -> AuthStatus:
        """Explicit machine-wide Codex logout, affecting every Codex use on this machine."""
        self.cancel()
        with tempfile.TemporaryDirectory(prefix="airalert-auth-") as folder:
            proc = spawn([*codex_command(), "logout"], Path(folder))
            try:
                proc.communicate(timeout=15)
                if proc.returncode != 0:
                    raise CodexError(
                        "Codex logout failed; it affects every Codex use on this machine"
                    )
            finally:
                kill_tree(proc)
        self.models = {}
        return self.status(refresh=True).model_copy(
            update={
                "message": "Logged out of Codex. This affects every Codex use on this machine.",
            }
        )
