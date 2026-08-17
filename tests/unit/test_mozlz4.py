"""The mozlz4 reader: the container, the block format, and the failure modes."""

import json
import struct

import pytest

from restore_wss.mozlz4 import MAGIC, MozLz4Error, decompress_block, read_json, read_mozlz4


def literal_block(payload: bytes) -> bytes:
    """Encode `payload` as a single all-literals LZ4 sequence.

    One sequence, not several: in LZ4 every sequence except the last must be followed by a match, so
    "literals only" is a valid encoding exactly once, as the final sequence of the block. Learned by
    writing it wrong and watching the decoder read a match offset out of the next token.
    """
    out = bytearray()
    if len(payload) < 15:
        out.append(len(payload) << 4)
    else:
        out.append(0xF0)
        extra = len(payload) - 15
        while extra >= 255:
            out.append(255)
            extra -= 255
        out.append(extra)
    out += payload
    return bytes(out)


def write_mozlz4(path, payload: bytes):
    path.write_bytes(MAGIC + struct.pack("<I", len(payload)) + literal_block(payload))
    return path


def test_a_document_round_trips_through_the_container(tmp_path):
    document = {"windows": [{"tabs": [{"entries": [{"url": "https://a/", "title": "A"}]}]}]}
    path = write_mozlz4(tmp_path / "recovery.jsonlz4", json.dumps(document).encode())
    assert read_json(path) == document


def test_matches_may_overlap_the_output_being_written():
    """How LZ4 encodes a run: offset 1, so the copy reads bytes it is still appending."""
    # literals "abc", then a match of the minimum length 4 at offset 1.
    block = bytes([0x30]) + b"abc" + bytes([0x01, 0x00])
    assert decompress_block(block) == b"abccccc"


def test_a_long_literal_run_uses_the_extension_bytes():
    payload = bytes(range(256)) * 3
    assert decompress_block(literal_block(payload), len(payload)) == payload


def test_a_file_that_is_not_mozlz4_is_refused(tmp_path):
    path = tmp_path / "not-a-session.jsonlz4"
    path.write_bytes(b"{ this is plain json }")
    with pytest.raises(MozLz4Error, match="not a mozlz4 file"):
        read_mozlz4(path)


def test_a_truncated_file_is_reported_not_half_decoded(tmp_path):
    payload = json.dumps({"windows": []}).encode()
    path = write_mozlz4(tmp_path / "recovery.jsonlz4", payload)
    raw = path.read_bytes()
    path.write_bytes(raw[: len(raw) - 5])
    with pytest.raises(MozLz4Error):
        read_mozlz4(path)


def test_a_match_pointing_outside_the_output_is_refused():
    block = bytes([0x10]) + b"a" + bytes([0x05, 0x00])  # offset 5 into a 1-byte output
    with pytest.raises(MozLz4Error, match="outside the output"):
        decompress_block(block)
