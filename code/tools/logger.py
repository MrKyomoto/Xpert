import contextlib
import datetime
import os
import sys
import threading
from typing import IO, Iterator, Tuple


LOG_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "logs"
)


class Tee:
    """Mirror one console stream into a shared UTF-8 log file."""

    def __init__(self, file: IO[str], stream: IO[str], lock: threading.Lock):
        self.file = file
        self.stream = stream
        self.lock = lock

    def write(self, text: str) -> int:
        with self.lock:
            written = self.stream.write(text)
            self.file.write(text)
            self.file.flush()
        return written

    def flush(self) -> None:
        with self.lock:
            self.stream.flush()
            self.file.flush()

    def isatty(self) -> bool:
        return bool(getattr(self.stream, "isatty", lambda: False)())

    @property
    def encoding(self) -> str:
        return getattr(self.stream, "encoding", None) or "utf-8"


def _new_log_file(run_tag: str = "") -> Tuple[str, IO[str]]:
    os.makedirs(LOG_DIR, exist_ok=True)
    safe_tag = "".join(ch for ch in run_tag if ch.isalnum() or ch in "-_")
    tag = f"_{safe_tag}" if safe_tag else ""

    # Microseconds make collisions unlikely; exclusive creation makes accidental
    # overwrites impossible even when multiple runs start concurrently.
    for counter in range(100):
        timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S_%f")
        suffix = f"_{counter}" if counter else ""
        path = os.path.join(LOG_DIR, f"{timestamp}{tag}{suffix}.log")
        try:
            return path, open(path, "x", encoding="utf-8")
        except FileExistsError:
            continue
    raise RuntimeError("无法分配唯一日志文件名")


@contextlib.contextmanager
def capture_output(run_tag: str = "") -> Iterator[str]:
    """Capture stdout and stderr without ever truncating an existing log."""
    log_path, file = _new_log_file(run_tag)
    old_stdout, old_stderr = sys.stdout, sys.stderr
    lock = threading.Lock()
    sys.stdout = Tee(file, old_stdout, lock)  # type: ignore[assignment]
    sys.stderr = Tee(file, old_stderr, lock)  # type: ignore[assignment]
    try:
        yield log_path
    finally:
        sys.stdout = old_stdout
        sys.stderr = old_stderr
        file.close()
