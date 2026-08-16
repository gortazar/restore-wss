"""Working out what restoring a snapshot would actually do.

Separated from doing it, for three reasons the plan asks for directly: the review step has to show
the user what is about to happen; ``--dry-run`` has to print it without touching the desktop; and
idempotency is a property of the plan, not of the execution — running restore when half the session
is already up must produce a plan that only fills in the other half.

Everything here is a pure function of a snapshot plus the current state of the desktop.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .matcher import Match, match_windows
from .model import Rect, Snapshot, Window
from .policy import Decision

#: What an action does.
PLACE = "place"  # the window is already open: move it to where it was
LAUNCH = "launch"  # nothing like it is running: start the application
TERMINAL = "terminal"  # a terminal window: reopen it at its directories, per the command policy


@dataclass
class Placement:
    """Where a window should end up. Fields that are ``None`` are left as they are."""

    workspace: int | None = None
    monitor: str | None = None
    frame: Rect | None = None
    #: ``"monitor"`` when ``frame`` is relative to the top-left of ``monitor``, ``"absolute"`` when
    #: it is in compositor coordinates. Monitor-relative is what survives a display being moved in
    #: the arrangement or replaced by one at a different offset, so it is preferred whenever the
    #: snapshot recorded the monitor's geometry.
    frame_space: str = "absolute"
    maximized: bool = False
    fullscreen: bool = False
    minimized: bool = False

    def to_json(self) -> dict[str, Any]:
        out: dict[str, Any] = {
            "maximized": self.maximized,
            "fullscreen": self.fullscreen,
            "minimized": self.minimized,
        }
        if self.workspace is not None:
            out["workspace"] = self.workspace
        if self.monitor:
            out["monitor"] = self.monitor
        if self.frame is not None:
            out["frame"] = self.frame.to_json()
            out["frame_space"] = self.frame_space
        return out


@dataclass
class TabPlan:
    """One tab of a terminal that is about to be reopened."""

    cwd: str = ""
    command: list[str] = field(default_factory=list)
    #: Whether the command will be re-run, and the policy's reason either way.
    run_command: bool = False
    reason: str = ""
    redacted: bool = False

    def describe(self) -> str:
        where = self.cwd or "~"
        if not self.command:
            return f"shell in {where}"
        if self.run_command:
            return f"{' '.join(self.command)} in {where}"
        return f"shell in {where} (not re-running {self.command[0]}: {self.reason})"

    def to_json(self) -> dict[str, Any]:
        return {
            "cwd": self.cwd,
            "command": self.command,
            "run_command": self.run_command,
            "reason": self.reason,
            "redacted": self.redacted,
        }


@dataclass
class Action:
    kind: str
    saved: Window
    placement: Placement
    #: For ``place``: the id of the live window this will move.
    window_id: str = ""
    #: For ``launch``: the desktop id to start, and the documents to hand it (M4 fills these in).
    app_id: str = ""
    uris: list[str] = field(default_factory=list)
    #: Human-readable justification, shown in the review step and in ``--dry-run``.
    reason: str = ""
    #: 0.0–1.0. Low-confidence actions are what the review step un-ticks by default.
    confidence: float = 1.0
    #: For ``terminal``: one entry per tab that will be reopened.
    tabs: list[TabPlan] = field(default_factory=list)

    def describe(self) -> str:
        where = []
        if self.placement.workspace is not None:
            where.append(f"workspace {self.placement.workspace + 1}")
        if self.placement.monitor:
            where.append(self.placement.monitor)
        target = ", ".join(where) if where else "wherever it opens"
        name = self.saved.title or self.saved.wm_class or self.app_id or "(unnamed)"
        if self.kind == PLACE:
            return f"move  {name} → {target} ({self.reason})"
        if self.kind == TERMINAL:
            tabs = "; ".join(tab.describe() for tab in self.tabs) or "an empty terminal"
            return f"start terminal → {target}: {tabs}"
        return f"start {self.app_id or self.saved.wm_class} for {name} → {target}"


@dataclass
class RestorePlan:
    #: Workspaces the snapshot needs. Restore creates up to this many before placing anything.
    workspace_count: int = 0
    active_workspace: int = 0
    actions: list[Action] = field(default_factory=list)
    #: Saved windows that could not be restored, and why — printed, never silently dropped.
    skipped: list[tuple[Window, str]] = field(default_factory=list)
    #: Matches the matcher refused to act on. The review step offers them; ``--yes`` ignores them.
    ambiguous: list[Match] = field(default_factory=list)
    #: Live windows the snapshot knows nothing about. Never touched — restore adds, never removes.
    untouched: list[Window] = field(default_factory=list)

    @property
    def is_empty(self) -> bool:
        return not self.actions

    def describe(self) -> list[str]:
        lines = [action.describe() for action in self.actions]
        lines += [f"skip  {window.title or window.wm_class}: {why}" for window, why in self.skipped]
        for match in self.ambiguous:
            lines.append(
                f"ask   {match.saved.title or match.saved.wm_class}: could be "
                f"{match.candidate.title!r} (score {match.score:.2f}), too close to call"
            )
        return lines


def _placement_for(window: Window, snapshot: Snapshot) -> Placement:
    frame = window.frame if (window.frame and window.frame.is_known) else None
    space = "absolute"
    if frame is not None and window.monitor:
        origin = next(
            (
                m.geometry
                for m in snapshot.monitors
                if m.connector == window.monitor and m.geometry is not None
            ),
            None,
        )
        if origin is not None:
            # Monitor-relative, so that a screen plugged in at a different offset — or a laptop
            # docked on the other side of the desk — still puts the window in the same corner.
            frame = Rect(frame.x - origin.x, frame.y - origin.y, frame.width, frame.height)
            space = "monitor"
    return Placement(
        workspace=window.workspace,
        monitor=window.monitor or None,
        frame=frame,
        frame_space=space,
        maximized=window.maximized,
        fullscreen=window.fullscreen,
        minimized=window.minimized,
    )


def _tab_plans(window: Window, policy) -> list[TabPlan]:
    """Turn a captured terminal block into what restore would do with it.

    Every tab is reopened at its working directory — that part is not in question. What the policy
    decides is only whether the command is *re-run*, and the reason is carried through so the
    review step can show it.
    """
    block = window.extra.get("terminal") or {}
    plans: list[TabPlan] = []
    for raw in block.get("tabs", []):
        command = [str(a) for a in raw.get("command", [])]
        redacted = bool(raw.get("redacted"))
        decision = (
            policy.decide(command, redacted=redacted)
            if policy is not None
            else Decision(False, "no command policy configured")
        )
        plans.append(
            TabPlan(
                cwd=str(raw.get("cwd", "")),
                command=command,
                run_command=decision.run,
                reason=decision.reason,
                redacted=redacted,
            )
        )
    return plans


def build_plan(
    snapshot: Snapshot,
    live_windows: list[Window],
    *,
    available_monitors: list[str] | None = None,
    command_policy=None,
) -> RestorePlan:
    """What restoring ``snapshot`` onto the current desktop would do.

    ``available_monitors`` is the list of connectors attached right now. A window remembered on a
    monitor that is not plugged in is not dropped: it is placed on whatever monitor exists, with
    its geometry left to the application, and the plan says so.
    """
    result = match_windows(snapshot.windows, live_windows)

    plan = RestorePlan(
        workspace_count=max(
            snapshot.workspace_count,
            max((w.workspace + 1 for w in snapshot.windows), default=0),
        ),
        active_workspace=snapshot.active_workspace,
        ambiguous=result.ambiguous,
        untouched=result.unmatched_live,
    )

    connected = set(available_monitors or [])

    def resolve(window: Window) -> tuple[Placement, str, float]:
        placement = _placement_for(window, snapshot)
        note = ""
        confidence = 1.0
        if placement.monitor and connected and placement.monitor not in connected:
            # The projector is not here. Keep the workspace, forget the coordinates: they belong
            # to a screen that does not exist, and a window at x=3000 is a window nobody can see.
            note = f"monitor {placement.monitor} is not connected"
            placement.monitor = None
            placement.frame = None
            confidence = 0.6
        return placement, note, confidence

    # Windows that are already open are moved, not launched again — this is what makes a second
    # `restore-wss restore` a no-op and lets a half-finished restore be completed.
    for match in result.matches:
        placement, note, confidence = resolve(match.saved)
        plan.actions.append(
            Action(
                kind=PLACE,
                saved=match.saved,
                placement=placement,
                window_id=match.candidate.id,
                reason=f"{match.reason} {match.score:.2f}" + (f"; {note}" if note else ""),
                confidence=confidence * (1.0 if match.score >= 1.0 else match.score),
            )
        )

    for window in result.unmatched_saved:
        if not window.app_id:
            # Tier 0 needs at least a desktop id. A window whose application the Shell never
            # identified cannot be launched, and guessing from wm_class is how you end up starting
            # the wrong program.
            plan.skipped.append((window, "no application id was captured"))
            continue
        placement, note, confidence = resolve(window)
        tabs = _tab_plans(window, command_policy)
        plan.actions.append(
            Action(
                kind=TERMINAL if tabs else LAUNCH,
                saved=window,
                placement=placement,
                app_id=window.app_id,
                reason=note,
                confidence=confidence,
                tabs=tabs,
            )
        )

    # Place before launch: moving a window that already exists is instant and cannot fail for
    # timing reasons, so the user sees the desktop settle before the slow part begins.
    plan.actions.sort(key=lambda action: (action.kind != PLACE, action.saved.workspace))
    return plan
