#!/usr/bin/env python3
"""The bridge between the Firefox extension and the restore-wss daemon.

Native messaging is browser-initiated: Firefox launches this process and talks to it over
stdin/stdout with 4-byte little-endian length prefixes. What makes this host unusual is where it
*cannot* go — under snap confinement it is executed with AppArmor's `ix`, so it inherits Firefox's
profile, and that profile allows session-bus traffic only to an allow-list of names that will never
include `org.gnome.RestoreWss` (`docs/browser-extensions-research.md` §1).

So this host does not speak D-Bus. It is a **file drop**, in a directory both sides can reach:

    ~/snap/firefox/common/restore-wss/report.json    written here, read by the daemon
    ~/snap/firefox/common/restore-wss/request.json   written by the daemon, read here

Constraints that shaped it, all from the same place:

* **stdlib only.** The interpreter is the core24 base snap's `/usr/bin/python3`; nothing else is
  installed inside the sandbox.
* **Two verbs, no more.** `report` in, `restore` out. There is no path, no command and no eval
  anywhere in the protocol — deliberately unlike Tridactyl's messenger, which accepts `run` and
  `eval` (§3 of the same document).
* **Atomic writes**, because the daemon reads this file whenever it likes.

Kept importable (everything in functions, `main()` under a guard) so the framing is covered by the
project's tests rather than by hope.
"""

from __future__ import annotations

import contextlib
import json
import os
import select
import struct
import sys
import time
from pathlib import Path

#: Firefox refuses to send a message larger than 1 MB, and a host that trusts a length prefix from
#: anywhere is a host that can be made to allocate anything. Frames above this are refused.
MAX_FRAME_BYTES = 4 * 1024 * 1024

REPORT_NAME = "report.json"
REQUEST_NAME = "request.json"

#: How often to look for a restore request. The daemon waits for the browser during a restore, so
#: this is the latency of that step; a second is invisible next to a browser starting up.
POLL_SECONDS = 1.0


def drop_directory() -> Path:
    """Where the two files live.

    Inside the sandbox `$HOME` is the snap's own directory, so this resolves to
    `~/snap/firefox/common/restore-wss/` seen from outside — which is the one place the confined
    browser may write and the unconfined daemon may read.
    """
    override = os.environ.get("RESTORE_WSS_BROWSER_DROP")
    if override:
        return Path(override)
    return Path(os.path.expanduser("~/.mozilla/restore-wss"))


def frame(message: dict) -> bytes:
    """A message in native-messaging framing: 4-byte little-endian length, then UTF-8 JSON."""
    payload = json.dumps(message, separators=(",", ":")).encode("utf-8")
    return struct.pack("<I", len(payload)) + payload


def read_frame(stream) -> dict | None:
    """One message from a stream, or ``None`` at end of input.

    Raises ``ValueError`` on a frame that is truncated or absurdly large — both of which are the
    same thing from here: something the browser did not send.
    """
    header = stream.read(4)
    if not header:
        return None
    if len(header) < 4:
        raise ValueError("truncated length prefix")
    (length,) = struct.unpack("<I", header)
    if length > MAX_FRAME_BYTES:
        raise ValueError(f"frame of {length} bytes refused (limit {MAX_FRAME_BYTES})")
    payload = stream.read(length)
    if len(payload) < length:
        raise ValueError(f"truncated frame: wanted {length} bytes, got {len(payload)}")
    return json.loads(payload.decode("utf-8"))


def write_atomically(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp")
    temporary.write_text(text)
    os.replace(temporary, path)


def store_report(message: dict, directory: Path | None = None) -> Path:
    """Write what the extension reported where the daemon will find it."""
    directory = directory or drop_directory()
    path = directory / REPORT_NAME
    write_atomically(
        path,
        json.dumps(
            {
                "reported_at": time.time(),
                "browser": message.get("browser", "firefox"),
                "version": message.get("version", ""),
                "profile": message.get("profile", ""),
                "windows": message.get("windows", []),
            },
            indent=1,
        ),
    )
    return path


def take_request(directory: Path | None = None) -> dict | None:
    """Read and consume a restore request, if the daemon left one."""
    directory = directory or drop_directory()
    path = directory / REQUEST_NAME
    try:
        raw = path.read_text()
    except OSError:
        return None
    try:
        request = json.loads(raw)
    except ValueError:
        request = None
    # Consumed either way: a request that cannot be parsed must not be retried for ever.
    with contextlib.suppress(OSError):
        path.unlink()
    return request


def handle(message: dict, directory: Path, out=None) -> None:
    """One message from the extension."""
    kind = message.get("type")
    if kind == "report":
        store_report(message, directory)
    elif kind == "hello" and out is not None:
        # A handshake so the extension can tell a live host from a missing one.
        out.write(frame({"type": "hello", "host": "restore-wss", "drop": str(directory)}))
        out.flush()


def main(argv: list[str]) -> int:  # pragma: no cover - the loop needs a browser on the other end
    directory = drop_directory()
    directory.mkdir(parents=True, exist_ok=True)
    stdin = sys.stdin.buffer
    stdout = sys.stdout.buffer

    # select, not a blocking read: a restore request can arrive while the browser has nothing to
    # say, and a host that only checks for requests after receiving a message would never deliver
    # one to an idle browser.
    while True:
        readable, _w, _x = select.select([stdin], [], [], POLL_SECONDS)
        if readable:
            try:
                message = read_frame(stdin)
            except ValueError as error:
                print(f"restore-wss host: {error}", file=sys.stderr)
                return 2
            if message is None:
                return 0  # the browser closed the port: it is shutting down, and so are we
            handle(message, directory, stdout)

        request = take_request(directory)
        if request:
            stdout.write(frame({"type": "restore", **request}))
            stdout.flush()


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main(sys.argv[1:]))
