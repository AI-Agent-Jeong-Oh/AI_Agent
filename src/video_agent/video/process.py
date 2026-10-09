from __future__ import annotations

import os
import signal
import subprocess
import time
from dataclasses import dataclass
from threading import Event


class OperationCanceled(RuntimeError):
    """Raised when a bounded subprocess is canceled by the caller."""


class ProcessTimeout(TimeoutError):
    """Raised when a media subprocess exceeds its approved deadline."""


@dataclass(frozen=True, slots=True)
class ProcessResult:
    stdout: bytes
    stderr: bytes


def run_bounded_process(
    command: list[str],
    *,
    timeout_seconds: float,
    cancel_event: Event | None = None,
    max_output_bytes: int = 2_000_000,
) -> ProcessResult:
    """Run without a shell and stop the child on timeout or cancellation."""
    if timeout_seconds <= 0:
        raise ValueError("timeout_seconds must be positive")
    if not command:
        raise ValueError("command cannot be empty")

    started = time.monotonic()
    process = subprocess.Popen(
        command,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        start_new_session=True,
    )
    while True:
        if cancel_event is not None and cancel_event.is_set():
            _stop_process(process)
            raise OperationCanceled("작업이 취소되었습니다.")
        remaining = timeout_seconds - (time.monotonic() - started)
        if remaining <= 0:
            _stop_process(process)
            raise ProcessTimeout(f"미디어 도구 실행이 {timeout_seconds:g}초 제한을 초과했습니다.")
        try:
            stdout, stderr = process.communicate(timeout=min(0.1, remaining))
            break
        except subprocess.TimeoutExpired:
            continue

    stdout = stdout[-max_output_bytes:]
    stderr = stderr[-max_output_bytes:]
    if process.returncode != 0:
        detail = stderr.decode("utf-8", errors="replace").strip()
        if len(detail) > 1_000:
            detail = detail[-1_000:]
        raise RuntimeError(detail or f"프로세스가 종료 코드 {process.returncode}로 실패했습니다.")
    return ProcessResult(stdout=stdout, stderr=stderr)


def _stop_process(process: subprocess.Popen[bytes]) -> None:
    if process.poll() is not None:
        return
    if os.name == "posix":
        os.killpg(process.pid, signal.SIGTERM)
    else:
        process.terminate()
    try:
        process.communicate(timeout=1.0)
    except subprocess.TimeoutExpired:
        if os.name == "posix":
            os.killpg(process.pid, signal.SIGKILL)
        else:
            process.kill()
        process.communicate()
