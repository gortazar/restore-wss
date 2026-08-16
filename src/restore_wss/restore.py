"""Carrying out a restore plan.

The plan (``plan.py``) decides what to do; this drives the compositor-side core until it is done,
and reports what actually happened — which is not the same thing, because a launch can fail, an
application can refuse a size, and a window can never appear at all.

Nothing here decides anything on its own. In particular it never closes a window and never touches
a window the snapshot does not describe: restore adds, it does not tidy up.
"""

from __future__ import annotations

import contextlib
import json
import subprocess
import time
from dataclasses import dataclass, field

from .plan import LAUNCH, PLACE, TERMINAL, Action, RestorePlan
from .terminalcmd import terminal_argv


@dataclass
class ActionResult:
    action: Action
    #: ``done``, ``failed``, or ``pending`` when a launched window never turned up in time.
    state: str
    detail: str = ""

    def describe(self) -> str:
        mark = {"done": "ok  ", "failed": "FAIL", "pending": "....."}.get(self.state, "?   ")
        return f"{mark} {self.action.describe()}" + (f" — {self.detail}" if self.detail else "")


@dataclass
class RestoreResult:
    results: list[ActionResult] = field(default_factory=list)
    workspaces_before: int = 0
    workspaces_after: int = 0

    @property
    def failures(self) -> list[ActionResult]:
        return [r for r in self.results if r.state == "failed"]

    def describe(self) -> list[str]:
        return [r.describe() for r in self.results]


def _spawn(argv: list[str]) -> None:
    """Start a process, detached, with **no shell**.

    argv, never a string through a shell: a captured command line is untrusted input, and a shell
    would interpret whatever is in it. Pipes, redirections and `&&` in a captured command arrive
    here as literal arguments and do nothing, which is the intended behaviour rather than a
    limitation to work around.
    """
    subprocess.Popen(  # noqa: S603 — argv, no shell, and every element came from /proc as argv
        argv,
        start_new_session=True,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


def execute(
    plan: RestorePlan,
    core,
    *,
    wait_seconds: float = 20.0,
    sleep=time.sleep,
    spawn=_spawn,
) -> RestoreResult:
    """Run ``plan`` against a ``ShellCoreClient``-shaped object.

    Launches are started first and collected afterwards, rather than waiting for each in turn: a
    cold application start was measured at up to 30 s, and seven of those in series is a restore
    the user watches for four minutes.
    """
    result = RestoreResult()

    if plan.workspace_count:
        result.workspaces_after = core.ensure_workspaces(plan.workspace_count)

    pending: list[tuple[Action, str]] = []

    for action in plan.actions:
        if action.kind == PLACE:
            try:
                core.place_window(action.window_id, json.dumps(action.placement.to_json()))
                result.results.append(ActionResult(action, "done"))
            except Exception as error:  # noqa: BLE001 — one bad window must not stop the restore
                result.results.append(ActionResult(action, "failed", str(error)))
        elif action.kind == TERMINAL:
            try:
                # The extension is told to expect the window, then the daemon starts the process
                # itself: a terminal needs a working directory and a command on its command line,
                # and spawning belongs outside the compositor in any case.
                launch_id = core.expect_window(
                    action.app_id, json.dumps(action.placement.to_json())
                )
                argv = terminal_argv(action.app_id, action.tabs)
                spawn(argv)
                pending.append((action, launch_id))
            except Exception as error:  # noqa: BLE001
                result.results.append(ActionResult(action, "failed", str(error)))
        elif action.kind == LAUNCH:
            try:
                launch_id = core.launch_app(
                    action.app_id,
                    json.dumps(action.uris),
                    json.dumps(action.placement.to_json()),
                )
                pending.append((action, launch_id))
            except Exception as error:  # noqa: BLE001
                result.results.append(ActionResult(action, "failed", str(error)))

    deadline = time.monotonic() + wait_seconds
    outstanding = list(pending)
    while outstanding and time.monotonic() < deadline:
        sleep(0.5)
        still_waiting = []
        for action, launch_id in outstanding:
            report = json.loads(core.get_launch_report(launch_id) or "{}")
            state = report.get("state")
            if state in ("placed", "failed", "timeout", "unknown"):
                result.results.append(_result_for(action, report))
            else:
                still_waiting.append((action, launch_id))
        outstanding = still_waiting

    for action, _launch_id in outstanding:
        result.results.append(
            ActionResult(action, "pending", f"no window yet after {wait_seconds:.0f} s")
        )

    if plan.active_workspace:
        # Which workspace ends up in front is cosmetic; never worth failing a restore over.
        with contextlib.suppress(Exception):
            core.activate_workspace(plan.active_workspace)

    return result


def _result_for(action: Action, report: dict) -> ActionResult:
    state = report.get("state")
    if state == "placed":
        detail = f"matched by {report.get('strategy', 'unknown')}"
        verdict = report.get("verdict") or {}
        if verdict.get("note"):
            detail += f"; {verdict['note']}"
        return ActionResult(action, "done", detail)
    return ActionResult(action, "failed", report.get("error") or f"launch {state}")
