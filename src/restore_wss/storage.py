"""Crash-safe snapshot storage.

The whole idea rests on the snapshot being readable after an unclean power-off, so writing it is
not "open the file and dump JSON". Every write is:

1. written to a temp file in the same directory,
2. ``fsync``\\ ed, so the bytes are on the device and not only in the page cache,
3. ``rename(2)``\\ d over the target, which POSIX makes atomic — a reader sees either the whole old
   file or the whole new one, never a mixture,
4. and the directory itself is ``fsync``\\ ed, so the rename survives too.

Before the rename, the previous generation is kept as ``session.prev.json``. That is belt and
braces against the case a rename cannot protect against: a snapshot that is *valid JSON and wrong*,
or a file damaged by something other than a torn write.

Everything lives under ``~/.restore-wss/``, created mode 0700: this file records which documents
the user opens and which commands they run, so it is nobody else's business on a shared machine.
"""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path

from .model import Snapshot

STATE_DIR_ENV = "RESTORE_WSS_HOME"
DEFAULT_DIR_NAME = ".restore-wss"

CURRENT_NAME = "session.json"
PREVIOUS_NAME = "session.prev.json"


def default_state_dir() -> Path:
    """``~/.restore-wss/state``, or wherever ``RESTORE_WSS_HOME`` points (tests, and the user)."""
    root = os.environ.get(STATE_DIR_ENV)
    base = Path(root) if root else Path.home() / DEFAULT_DIR_NAME
    return base / "state"


class SnapshotStore:
    """The two-generation snapshot file pair in one directory."""

    def __init__(self, directory: Path | str):
        self.directory = Path(directory)

    @property
    def current_path(self) -> Path:
        return self.directory / CURRENT_NAME

    @property
    def previous_path(self) -> Path:
        return self.directory / PREVIOUS_NAME

    def ensure_directory(self) -> None:
        self.directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        # mkdir's mode is masked by umask and does nothing at all if the directory already
        # existed, so set it explicitly. A world-readable state directory is a privacy bug.
        os.chmod(self.directory, 0o700)

    def save(self, snapshot: Snapshot) -> Path:
        """Write ``snapshot`` atomically, rotating the current file to ``session.prev.json``."""
        self.ensure_directory()
        payload = snapshot.dumps() + "\n"

        if self.current_path.exists():
            # A copy, not a rename: if the machine dies here, the current file is still intact.
            self._atomic_write(self.previous_path, self.current_path.read_text())

        self._atomic_write(self.current_path, payload)
        return self.current_path

    def _atomic_write(self, target: Path, text: str) -> None:
        fd, tmp_name = tempfile.mkstemp(dir=str(self.directory), prefix=".tmp-", suffix=".json")
        tmp = Path(tmp_name)
        try:
            with os.fdopen(fd, "w") as handle:
                handle.write(text)
                handle.flush()
                os.fsync(handle.fileno())
            os.chmod(tmp, 0o600)
            os.replace(tmp, target)
            self._fsync_directory()
        except BaseException:
            tmp.unlink(missing_ok=True)
            raise

    def _fsync_directory(self) -> None:
        dir_fd = os.open(self.directory, os.O_RDONLY)
        try:
            os.fsync(dir_fd)
        finally:
            os.close(dir_fd)

    def load(self) -> Snapshot | None:
        """The newest readable snapshot, or ``None``.

        A current file that will not parse is *not* fatal: the previous generation is tried, so a
        damaged snapshot costs a few minutes of capture rather than the whole session.
        """
        for path in (self.current_path, self.previous_path):
            snapshot = self._load_one(path)
            if snapshot is not None:
                return snapshot
        return None

    @staticmethod
    def _load_one(path: Path) -> Snapshot | None:
        try:
            text = path.read_text()
        except (OSError, UnicodeDecodeError):
            return None
        try:
            return Snapshot.loads(text)
        except (json.JSONDecodeError, TypeError, ValueError):
            return None
