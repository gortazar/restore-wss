"""What the snapshot says, versus what is running now.

The question `restore-wss diff` answers is "if I restored right now, what would change?" — which
is a different question from the restore plan, because it includes what the *snapshot* is missing
as well as what the desktop is. Pure functions over two window lists, so it is tested without a
desktop.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .matcher import match_windows
from .model import Window


@dataclass
class Difference:
    #: In the snapshot, not on screen — restore would open these.
    only_in_snapshot: list[Window] = field(default_factory=list)
    #: On screen, not in the snapshot — opened since, and restore would leave them alone.
    only_running: list[Window] = field(default_factory=list)
    #: Matched, but somewhere other than where the snapshot remembers.
    moved: list[tuple[Window, Window]] = field(default_factory=list)
    #: Matched and in the same place.
    unchanged: int = 0

    @property
    def is_empty(self) -> bool:
        return not (self.only_in_snapshot or self.only_running or self.moved)

    def describe(self) -> list[str]:
        lines = []
        for window in self.only_in_snapshot:
            lines.append(f"- {window.title or window.wm_class} (not running)")
        for window in self.only_running:
            lines.append(f"+ {window.title or window.wm_class} (not in the snapshot)")
        for saved, live in self.moved:
            lines.append(
                f"~ {saved.title or saved.wm_class}: workspace {live.workspace + 1} now, "
                f"{saved.workspace + 1} in the snapshot"
            )
        if self.unchanged:
            lines.append(f"= {self.unchanged} window(s) already where the snapshot wants them")
        return lines


def diff_windows(saved: list[Window], live: list[Window]) -> Difference:
    result = match_windows(saved, live)
    difference = Difference(
        only_in_snapshot=list(result.unmatched_saved),
        only_running=list(result.unmatched_live),
    )
    for match in result.matches:
        if match.saved.workspace != match.candidate.workspace or (
            match.saved.monitor and match.saved.monitor != match.candidate.monitor
        ):
            difference.moved.append((match.saved, match.candidate))
        else:
            difference.unchanged += 1
    return difference
