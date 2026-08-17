"""The file-drop bridge, from both ends — the shipped host and the daemon's reader."""

import importlib.util
import io
import json
import struct
import time
from pathlib import Path

import pytest

from restore_wss.bridge import MAX_REPORT_AGE_SECONDS, read_report, request_pending, write_request

HOST_PATH = (
    Path(__file__).resolve().parents[2] / "src" / "native-host" / "restore-wss-firefox-host.py"
)


def load_host():
    """Import the host that actually ships, so its framing is tested rather than a copy of it."""
    spec = importlib.util.spec_from_file_location("restore_wss_host", HOST_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


host = load_host()


def a_report(url="https://example.com/", window_id="7"):
    return {
        "type": "report",
        "browser": "firefox",
        "version": "142.0",
        "profile": "cqdb58zj.default",
        "windows": [
            {
                "id": window_id,
                "width": 900,
                "height": 700,
                "left": 10,
                "top": 20,
                "state": "normal",
                "tabs": [
                    {"url": url, "title": "Example", "pinned": True},
                    {"url": "https://gnome.org/", "title": "GNOME", "active": True},
                ],
            }
        ],
    }


# --- framing, against the shipped host ------------------------------------------------------


def test_a_frame_round_trips():
    message = {"type": "report", "windows": []}
    stream = io.BytesIO(host.frame(message))
    assert host.read_frame(stream) == message


def test_end_of_input_is_not_an_error():
    assert host.read_frame(io.BytesIO(b"")) is None


def test_a_truncated_length_prefix_is_refused():
    with pytest.raises(ValueError, match="truncated length prefix"):
        host.read_frame(io.BytesIO(b"\x02\x00"))


def test_a_truncated_payload_is_refused():
    framed = host.frame({"type": "report"})
    with pytest.raises(ValueError, match="truncated frame"):
        host.read_frame(io.BytesIO(framed[:-3]))


def test_an_oversized_frame_is_refused_without_allocating_it():
    """A length prefix is input like any other: 3 GB must be a refusal, not an allocation."""
    absurd = struct.pack("<I", 3_000_000_000)
    with pytest.raises(ValueError, match="refused"):
        host.read_frame(io.BytesIO(absurd))


# --- the drop, end to end -------------------------------------------------------------------


def test_what_the_host_writes_is_what_the_daemon_reads(tmp_path):
    host.store_report(a_report(), tmp_path)
    report = read_report(roots=[str(tmp_path)])
    assert report is not None
    assert [tab.url for tab in report.windows[0].tabs] == [
        "https://example.com/",
        "https://gnome.org/",
    ]
    assert report.windows[0].tabs[0].pinned
    assert report.windows[0].tabs[1].active
    assert report.windows[0].window_id == "7"
    assert report.windows[0].profile == "cqdb58zj.default"
    assert report.geometries[0] == {
        "x": 10,
        "y": 20,
        "width": 900,
        "height": 700,
        "maximized": False,
    }
    assert report.is_fresh


def test_a_stale_report_is_returned_but_flagged(tmp_path):
    host.store_report(a_report(), tmp_path)
    path = tmp_path / "report.json"
    document = json.loads(path.read_text())
    document["reported_at"] = time.time() - (MAX_REPORT_AGE_SECONDS + 60)
    path.write_text(json.dumps(document))

    report = read_report(roots=[str(tmp_path)])
    assert report is not None
    assert not report.is_fresh


def test_no_drop_at_all_is_the_normal_state(tmp_path):
    assert read_report(roots=[str(tmp_path / "nothing")]) is None


def test_a_damaged_report_contributes_nothing(tmp_path):
    (tmp_path / "report.json").write_text("{ half a fi")
    assert read_report(roots=[str(tmp_path)]) is None


def test_a_request_reaches_the_host_and_is_consumed_once(tmp_path):
    written = write_request(
        [{"urls": ["https://a/"], "pinned": [False], "active": 0}], roots=[str(tmp_path)]
    )
    assert written is not None
    assert request_pending(roots=[str(tmp_path)])

    request = host.take_request(tmp_path)
    assert request["windows"][0]["urls"] == ["https://a/"]
    # Consumed: the host deletes it, so a restore is never replayed.
    assert not request_pending(roots=[str(tmp_path)])
    assert host.take_request(tmp_path) is None


def test_a_request_is_not_written_where_no_browser_ever_ran(tmp_path):
    assert write_request([{"urls": ["https://a/"]}], roots=[str(tmp_path / "absent")]) is None


def test_an_unparsable_request_is_consumed_rather_than_retried_for_ever(tmp_path):
    (tmp_path / "request.json").write_text("not json")
    assert host.take_request(tmp_path) is None
    assert not (tmp_path / "request.json").exists()


def test_the_host_answers_a_handshake(tmp_path):
    out = io.BytesIO()
    host.handle({"type": "hello"}, tmp_path, out)
    reply = host.read_frame(io.BytesIO(out.getvalue()))
    assert reply["host"] == "restore-wss"


def test_the_host_runs_as_a_process_and_writes_what_it_is_sent(tmp_path):
    """The shipped host, launched the way Firefox launches it: framed JSON on stdin.

    This is the only test that exercises its main loop — the select() poll that lets a restore
    request reach an idle browser — rather than its functions.
    """
    import subprocess
    import sys
    import time

    drop = tmp_path / "drop"
    process = subprocess.Popen(
        [sys.executable, str(HOST_PATH)],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        env={"RESTORE_WSS_BROWSER_DROP": str(drop), "PATH": "/usr/bin:/bin"},
    )
    try:
        process.stdin.write(host.frame(a_report(url="https://spawned.example/")))
        process.stdin.flush()

        report = None
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            report = read_report(roots=[str(drop)])
            if report is not None:
                break
            time.sleep(0.1)
        assert report is not None, "the host did not write a report"
        assert report.windows[0].tabs[0].url == "https://spawned.example/"

        # And a request left for it is picked up while the browser says nothing at all.
        write_request(
            [{"urls": ["https://asked-for/"], "pinned": [False], "active": 0}], roots=[str(drop)]
        )
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline and request_pending(roots=[str(drop)]):
            time.sleep(0.1)
        assert not request_pending(roots=[str(drop)]), "the host never took the request"

        forwarded = host.read_frame(process.stdout)
        assert forwarded["type"] == "restore"
        assert forwarded["windows"][0]["urls"] == ["https://asked-for/"]
    finally:
        process.stdin.close()
        process.wait(timeout=10)
