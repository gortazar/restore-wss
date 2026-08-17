"""Reading Firefox's ``mozlz4`` files without a dependency.

Firefox stores its session in ``sessionstore-backups/recovery.jsonlz4``: the magic ``mozLz40\\0``,
a little-endian ``u32`` of the decompressed size, then a single **LZ4 block**. The block format is
small enough to decode here — a token byte splitting literal and match lengths, then literals and
back-references — which is worth ~40 lines to avoid making the daemon depend on a compression
library it needs for one file.

Verified against the live profile on the development machine: 223 538 bytes in, 762 688 out,
6 windows and 27 tabs (``docs/browser-extensions-research.md`` §5).
"""

from __future__ import annotations

import json
import struct
from pathlib import Path

MAGIC = b"mozLz40\0"


class MozLz4Error(ValueError):
    """The file is not a mozlz4 file, or the block does not decode."""


def decompress_block(source: bytes, expected_size: int = 0) -> bytes:
    """Decode one LZ4 block.

    ``expected_size`` is the size the container claims; it is checked at the end rather than trusted
    up front, because a truncated file should be reported as such and not silently half-decoded.
    """
    out = bytearray()
    index = 0
    length = len(source)

    while index < length:
        token = source[index]
        index += 1

        literals = token >> 4
        if literals == 15:
            while True:
                if index >= length:
                    raise MozLz4Error("truncated literal length")
                extra = source[index]
                index += 1
                literals += extra
                if extra != 255:
                    break
        out += source[index : index + literals]
        index += literals

        # The last sequence of a block has literals and no match, so running out here is normal.
        if index >= length:
            break

        if index + 1 >= length:
            raise MozLz4Error("truncated match offset")
        offset = source[index] | (source[index + 1] << 8)
        index += 2
        if offset == 0 or offset > len(out):
            raise MozLz4Error(f"match offset {offset} outside the output")

        match = token & 0x0F
        if match == 15:
            while True:
                if index >= length:
                    raise MozLz4Error("truncated match length")
                extra = source[index]
                index += 1
                match += extra
                if extra != 255:
                    break
        match += 4  # the minimum match length is 4 bytes

        start = len(out) - offset
        # Copied byte by byte on purpose: matches may overlap the region being written, which is how
        # LZ4 encodes runs, and a slice copy would get that wrong.
        for step in range(match):
            out.append(out[start + step])

    if expected_size and len(out) != expected_size:
        raise MozLz4Error(f"expected {expected_size} bytes, decoded {len(out)}")
    return bytes(out)


def read_mozlz4(path: Path | str) -> bytes:
    raw = Path(path).read_bytes()
    if raw[:8] != MAGIC:
        raise MozLz4Error(f"not a mozlz4 file (magic {raw[:8]!r})")
    (size,) = struct.unpack("<I", raw[8:12])
    return decompress_block(raw[12:], size)


def read_json(path: Path | str):
    """The decoded document, or raise ``MozLz4Error``/``ValueError``."""
    return json.loads(read_mozlz4(path))
