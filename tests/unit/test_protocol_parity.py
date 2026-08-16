"""The two copies of the compositor-side interface must not drift apart.

``src/restore_wss/protocol.py`` and ``src/extension/protocol.js`` each spell out
``org.gnome.SessionCore`` because the extension is loaded as plain JS with no build step. Two
copies is a deliberate trade — and this test is the other half of it.
"""

from __future__ import annotations

import re
from pathlib import Path

from restore_wss.protocol import API_VERSION, SHELL_IFACE_XML, SHELL_NAME, SHELL_OBJECT_PATH

EXTENSION_PROTOCOL = Path(__file__).resolve().parents[2] / "src" / "extension" / "protocol.js"


def _js_source() -> str:
    return EXTENSION_PROTOCOL.read_text()


def _js_const(name: str) -> str:
    match = re.search(rf"export const {name} = '([^']*)';", _js_source())
    assert match, f"{name} not found in {EXTENSION_PROTOCOL}"
    return match.group(1)


def _js_iface_xml() -> str:
    match = re.search(r"export const SHELL_IFACE_XML = `(.*?)`;", _js_source(), re.S)
    assert match, "SHELL_IFACE_XML not found"
    return match.group(1)


def _normalise(xml: str) -> str:
    """Compare the shape, not the indentation: whitespace differences are not drift."""
    without_comments = re.sub(r"<!--.*?-->", "", xml, flags=re.S)
    return re.sub(r"\s+", " ", without_comments).strip()


def test_the_interface_xml_matches():
    assert _normalise(_js_iface_xml()) == _normalise(SHELL_IFACE_XML)


def test_the_names_match():
    assert _js_const("SHELL_NAME") == SHELL_NAME
    assert _js_const("SHELL_OBJECT_PATH") == SHELL_OBJECT_PATH


def test_the_api_version_matches():
    match = re.search(r"export const API_VERSION = (\d+);", _js_source())
    assert match and int(match.group(1)) == API_VERSION
