"""Reading the freedesktop recent-files store, and LibreOffice's private one.

``~/.local/share/recently-used.xbel`` turned out to be a real source on this machine: 1000 entries
(the cap), written by 25 different applications, each with the application name and a timestamp
(``docs/platform-findings.md`` §4). LibreOffice is *not* one of them — it keeps a picklist of its
own in ``registrymodifications.xcu`` — which is exactly why the ladder has a per-application rung.

Both readers are lenient. These files belong to other programs; a malformed one, a missing one or
one in a format this code has never seen is a source that contributes nothing, not an error.
"""

from __future__ import annotations

import os
import re
import xml.etree.ElementTree as ElementTree
from datetime import datetime
from pathlib import Path

BOOKMARK_NS = "{http://www.freedesktop.org/standards/desktop-bookmarks}"


def recent_files_path() -> Path:
    data_home = os.environ.get("XDG_DATA_HOME") or (Path.home() / ".local" / "share")
    return Path(data_home) / "recently-used.xbel"


def _parse_time(value: str | None) -> float:
    if not value:
        return 0.0
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()
    except ValueError:
        return 0.0


def read_recent_files(path: Path | None = None, limit: int = 400) -> list[tuple[str, str, float]]:
    """``(uri, application name, modified time)``, newest first.

    One entry per (document, application) pair: the same file opened by two applications is two
    facts, and which one matters depends on the window being asked about.
    """
    path = path or recent_files_path()
    try:
        root = ElementTree.parse(path).getroot()
    except (OSError, ElementTree.ParseError):
        return []

    entries: list[tuple[str, str, float]] = []
    for bookmark in root.findall("bookmark"):
        uri = bookmark.get("href") or ""
        if not uri:
            continue
        visited = _parse_time(bookmark.get("modified") or bookmark.get("visited"))
        for application in bookmark.iter(f"{BOOKMARK_NS}application"):
            entries.append(
                (
                    uri,
                    application.get("name") or "",
                    _parse_time(application.get("modified")) or visited,
                )
            )
    entries.sort(key=lambda entry: entry[2], reverse=True)
    return entries[:limit]


def libreoffice_config_paths() -> list[Path]:
    """Every ``registrymodifications.xcu`` this machine might be using.

    Several, because a snap keeps its own per-revision copy: on this machine the live one was
    ``~/snap/libreoffice/377/.config/libreoffice/4/user/``, and the deb path is checked too.
    """
    home = Path.home()
    candidates = [
        home / ".config" / "libreoffice" / "4" / "user" / "registrymodifications.xcu",
        *sorted(
            home.glob("snap/libreoffice/*/.config/libreoffice/4/user/registrymodifications.xcu"),
            reverse=True,
        ),
        *sorted(
            home.glob(
                ".var/app/org.libreoffice.LibreOffice/config/libreoffice/4/user/"
                "registrymodifications.xcu"
            ),
        ),
    ]
    return [path for path in candidates if path.exists()]


# What the picklist actually looks like in the file, checked against the real 4.4 MB
# registrymodifications.xcu on this machine — the URI is the *node name*, not a value:
#
#   <item oor:path="…Histories:HistoryInfo['PickList']/ItemList">
#     <node oor:name="file:///home/user/Thesis.odt" oor:op="replace">
#       <prop oor:name="Title" …><value>Thesis</value></prop>
#
# Parsed with a regex rather than an XML parser on purpose: the file is megabytes of unrelated
# settings, most of it base64 thumbnails, and this needs one attribute out of it.
_PICKLIST_ITEM_RE = re.compile(
    r"HistoryInfo\['PickList'\]/ItemList\"><node oor:name=\"(file://[^\"]+)\"",
)


def read_libreoffice_history(paths: list[Path] | None = None, limit: int = 25) -> list[str]:
    """The URIs in LibreOffice's own most-recently-used list.

    LibreOffice does not write to the freedesktop recent store, which is why the headline
    "Thesis in LibreOffice" case needs this (``docs/platform-findings.md`` §4).
    """
    for path in paths if paths is not None else libreoffice_config_paths():
        try:
            text = path.read_text(errors="replace")
        except OSError:
            continue
        uris = _PICKLIST_ITEM_RE.findall(text)
        if uris:
            # Most recent first is not recorded in the file; the item order is what there is, and
            # the caller treats the whole list as candidates rather than as a ranking.
            return uris[:limit]
    return []
