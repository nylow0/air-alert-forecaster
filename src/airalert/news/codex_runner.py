"""Isolated CLI completions with streaming, fail-closed tool detection.

The event guard is reactive. Stock exec still advertises tools, so this is not
a proof that a tool cannot start before its first event reaches the parent.
"""

import contextlib
import json
import os
import queue
import shutil
import signal
import subprocess
import tempfile
import threading
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path

from airalert.news.capabilities import DISABLED_FEATURES


class CodexError(RuntimeError):
    """Safe public error text, never subprocess output or a credential-bearing URL."""


class ToolAttempt(CodexError):
    pass


def safe_env() -> dict[str, str]:
    allowed = {
        "PATH",
        "SYSTEMROOT",
        "WINDIR",
        "COMSPEC",
        "PATHEXT",
        "TEMP",
        "TMP",
        "USERPROFILE",
        "HOME",
        "APPDATA",
        "LOCALAPPDATA",
        "PROGRAMFILES",
        "PROGRAMFILES(X86)",
        "SYSTEMDRIVE",
        "SSL_CERT_FILE",
        "SSL_CERT_DIR",
    }
    return {k: v for k, v in os.environ.items() if k.upper() in allowed}


def codex_command() -> tuple[str, ...]:
    executable = shutil.which("codex")
    if executable is None:
        raise CodexError("Codex CLI is not installed")
    return (executable,)


def spawn(args: Sequence[str], cwd: Path, status_output: bool = False) -> subprocess.Popen[str]:
    return subprocess.Popen(
        list(args),
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE if status_output else subprocess.DEVNULL,
        text=True,
        encoding="utf-8",
        errors="strict",
        cwd=cwd,
        env=safe_env(),
        creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
        start_new_session=os.name != "nt",
    )


def kill_tree(proc: subprocess.Popen[str]) -> None:
    """Terminate only the child tree created by this app; never a parent Codex session."""
    if os.name == "nt":
        subprocess.run(
            ["taskkill", "/PID", str(proc.pid), "/T", "/F"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
            creationflags=subprocess.CREATE_NO_WINDOW,
            timeout=10,
        )
    else:
        with contextlib.suppress(ProcessLookupError):
            os.killpg(proc.pid, signal.SIGKILL)
    if proc.poll() is None:
        proc.kill()
    proc.wait(timeout=10)


def unsafe_event(event: dict[str, object]) -> bool:
    """Permit only text/reasoning items; unknown action items are unsafe too."""
    kind = str(event.get("type", ""))
    if kind not in {
        "thread.started",
        "turn.started",
        "turn.completed",
        "turn.failed",
        "error",
        "item.started",
        "item.updated",
        "item.completed",
    }:
        return True
    if any(word in kind.lower() for word in ("tool", "command", "patch", "file_change")):
        return True
    item = event.get("item")
    return isinstance(item, dict) and item.get("type") not in {
        "agent_message",
        "reasoning",
        "error",
    }


@dataclass(frozen=True)
class Completion:
    output: dict[str, object]
    input_tokens: int
    output_tokens: int
    seconds: float


class CodexRunner:
    def __init__(
        self,
        command: Sequence[str] | None = None,
        breaker: Callable[[str], None] | None = None,
        timeout: float = 120,
    ) -> None:
        self.command = tuple(command) if command else None
        self.breaker = breaker
        self.timeout = timeout
        self._active: subprocess.Popen[str] | None = None
        self._lock = threading.Lock()
        self._call_lock = threading.Lock()

    def cancel(self) -> None:
        with self._lock:
            if self._active is not None:
                kill_tree(self._active)

    def complete(
        self, prompt: str, schema: dict[str, object], model: str, effort: str
    ) -> Completion:
        # Both stages are serialized here as well as by the worker.
        with self._call_lock, tempfile.TemporaryDirectory(prefix="airalert-news-") as folder:
            scratch = Path(folder)
            # Keep the host-only schema outside the otherwise empty child cwd.
            cwd = scratch / "empty"
            cwd.mkdir()
            schema_path = scratch / "output.schema.json"
            schema_path.write_text(json.dumps(schema), encoding="utf-8")
            args = [
                *(self.command or codex_command()),
                "exec",
                "--ignore-user-config",
                "--ignore-rules",
                "--ephemeral",
                "--skip-git-repo-check",
                "--sandbox",
                "read-only",
                "--json",
                "--color",
                "never",
                "--cd",
                str(cwd),
                "--model",
                model,
                "--output-schema",
                str(schema_path),
            ]
            overrides = [
                f"model_reasoning_effort={effort}",
                "approval_policy=never",
                "web_search=disabled",
                "mcp_servers={}",
                "shell_environment_policy.inherit=none",
            ]
            overrides.extend(f"features.{feature}=false" for feature in DISABLED_FEATURES)
            overrides.append("features.skip_host_skill_discovery=true")
            for value in overrides:
                args.extend(("-c", value))
            args.append("-")
            started = time.monotonic()
            proc = spawn(args, cwd)
            with self._lock:
                self._active = proc
            events: queue.Queue[dict[str, object] | None] = queue.Queue(maxsize=256)
            unsafe = threading.Event()
            invalid = threading.Event()

            def read_stream() -> None:
                assert proc.stdout is not None
                total = 0
                try:
                    while line := proc.stdout.readline(1_048_577):
                        total += len(line)
                        if len(line) > 1_048_576 or total > 4_194_304:
                            invalid.set()
                            kill_tree(proc)
                            break
                        event = json.loads(line)
                        if not isinstance(event, dict):
                            raise ValueError("Invalid event")
                        if unsafe_event(event):
                            unsafe.set()
                            # Kill in the reader, before queuing or inspecting model output.
                            kill_tree(proc)
                            if self.breaker is not None:
                                self.breaker(
                                    "News circuit breaker: Codex attempted a tool, "
                                    "command or patch; result discarded"
                                )
                            break
                        events.put(event, timeout=1)
                except (ValueError, UnicodeError, queue.Full, OSError):
                    invalid.set()
                    kill_tree(proc)
                finally:
                    try:
                        events.put(None, timeout=1)
                    except queue.Full:
                        invalid.set()

            reader = threading.Thread(target=read_stream, daemon=True)
            reader.start()

            def write_prompt() -> None:
                assert proc.stdin is not None
                try:
                    proc.stdin.write(prompt)
                    proc.stdin.close()
                except (BrokenPipeError, OSError):
                    if not unsafe.is_set():
                        invalid.set()

            writer = threading.Thread(target=write_prompt, daemon=True)
            writer.start()
            output: dict[str, object] | None = None
            completed = False
            inputs = outputs = 0
            try:
                while True:
                    remaining = self.timeout - (time.monotonic() - started)
                    if remaining <= 0:
                        raise CodexError("Codex completion timed out")
                    try:
                        event = events.get(timeout=min(remaining, 0.1))
                    except queue.Empty:
                        if unsafe.is_set() or invalid.is_set():
                            break
                        continue
                    if event is None:
                        break
                    if event.get("type") in {"error", "turn.failed"}:
                        raise CodexError("Codex rejected or failed the selected model request")
                    item = event.get("item")
                    if (
                        event.get("type") == "item.completed"
                        and isinstance(item, dict)
                        and item.get("type") == "agent_message"
                    ):
                        if output is not None:
                            raise CodexError("Codex returned multiple final messages")
                        parsed = json.loads(str(item.get("text", "")))
                        if not isinstance(parsed, dict):
                            raise ValueError("Output must be an object")
                        output = parsed
                    if event.get("type") == "turn.completed":
                        completed = True
                        usage = event.get("usage")
                        if isinstance(usage, dict):
                            inputs = int(usage.get("input_tokens", 0))
                            outputs = int(usage.get("output_tokens", 0))
                if unsafe.is_set():
                    message = (
                        "News circuit breaker: Codex attempted a tool, command or patch; "
                        "result discarded"
                    )
                    if self.breaker is not None:
                        self.breaker(message)
                    raise ToolAttempt(message)
                if invalid.is_set():
                    raise CodexError("Codex emitted invalid or oversized streaming output")
                proc.wait(timeout=max(0.1, self.timeout - (time.monotonic() - started)))
                if proc.returncode != 0 or not completed or output is None:
                    raise CodexError("Codex completion failed without a validated final result")
                return Completion(output, inputs, outputs, time.monotonic() - started)
            except (ValueError, BrokenPipeError, subprocess.TimeoutExpired) as exc:
                raise CodexError("Codex completion returned invalid output or timed out") from exc
            finally:
                kill_tree(proc)
                reader.join(timeout=2)
                writer.join(timeout=2)
                for pipe in (proc.stdout, proc.stdin):
                    if pipe is not None:
                        pipe.close()
                with self._lock:
                    self._active = None
