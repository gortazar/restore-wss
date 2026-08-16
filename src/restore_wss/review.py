"""The review step: a GTK/libadwaita window showing what restore is about to do.

Per the answered open question in ``PLAN.md`` this is a GTK dialog rather than a terminal UI,
because the case that matters is the one where nobody typed a command — the offer at the first
login after a reboot. ``restore-wss restore`` in a terminal keeps its text output; this is the same
plan in a window, with a switch per item.

The window is deliberately thin: the *model* below decides what is shown and what is pre-ticked,
and it is a pure function of the plan the daemon produced, so it is unit-tested without a display.
GTK only draws it.
"""

from __future__ import annotations

from dataclasses import dataclass, field

#: Actions at or above this confidence are ticked to begin with. Below it the user has to say yes,
#: which is the whole point of having a confidence.
PRE_TICK_CONFIDENCE = 0.9


@dataclass
class Row:
    """One line in the review window."""

    index: int
    title: str
    subtitle: str
    selected: bool
    #: ``window`` or ``vpn``. VPN rows are informational: activating a connection is not optional
    #: per-item, it happens if the restore happens.
    kind: str = "window"


@dataclass
class ReviewModel:
    rows: list[Row] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    heading: str = ""

    @property
    def selected_indices(self) -> list[int]:
        return [row.index for row in self.rows if row.selected and row.kind == "window"]


def build_model(plan: dict) -> ReviewModel:
    """Turn the daemon's plan into what the window shows."""
    actions = plan.get("actions", [])
    model = ReviewModel()

    windows = len(actions)
    workspaces = plan.get("workspace_count", 0)
    if windows:
        model.heading = (
            f"{windows} window{'' if windows == 1 else 's'} "
            f"across {workspaces} workspace{'' if workspaces == 1 else 's'}"
        )
    else:
        model.heading = "Nothing to restore — the snapshot matches what is already open"

    for action in actions:
        confidence = float(action.get("confidence", 1.0))
        description = action.get("description", "")
        title, _, rest = description.partition(" → ")
        subtitle = rest
        if action.get("reason"):
            subtitle = f"{rest} · {action['reason']}" if rest else action["reason"]
        model.rows.append(
            Row(
                index=int(action.get("index", 0)),
                title=title.strip() or description,
                subtitle=subtitle.strip(),
                selected=confidence >= PRE_TICK_CONFIDENCE,
            )
        )

    for entry in plan.get("vpn", []):
        model.rows.append(
            Row(
                index=-1,
                title=entry.get("description", entry.get("name", "VPN")),
                subtitle="NetworkManager connection",
                selected=True,
                kind="vpn",
            )
        )

    for entry in plan.get("skipped", []):
        model.notes.append(
            f"Skipping {entry.get('title') or entry.get('wm_class')}: {entry.get('reason')}"
        )
    for entry in plan.get("ambiguous", []):
        model.notes.append(
            f"{entry.get('title')} could be “{entry.get('candidate')}” — too close to call, "
            "so it is left where it is"
        )
    untouched = plan.get("untouched", [])
    if untouched:
        names = ", ".join(w.get("title") or w.get("wm_class") for w in untouched[:4])
        more = "" if len(untouched) <= 4 else f" and {len(untouched) - 4} more"
        model.notes.append(f"Leaving alone: {names}{more}")

    return model


def _save_screenshot(window, path: str) -> None:
    """Render the window to a PNG from inside the process.

    Not through the Shell's screenshot API: org.gnome.Shell.Screenshot answers
    "Screenshot is not allowed" to an ordinary client, and a nested test session has no portal.
    GTK can draw its own window into a texture, which needs neither.
    """
    from gi.repository import Gtk

    native = window.get_native()
    renderer = native.get_renderer() if native is not None else None
    if renderer is None:
        return
    paintable = Gtk.WidgetPaintable.new(window)
    snapshot = Gtk.Snapshot()
    paintable.snapshot(snapshot, window.get_width(), window.get_height())
    node = snapshot.to_node()
    if node is None:
        return
    renderer.render_texture(node, None).save_to_png(path)


def run_review(client, model: ReviewModel | None = None, screenshot: str | None = None) -> int:
    """Show the window. Returns a process exit code.

    Imports GTK here rather than at module scope so that ``build_model`` — the part with the
    decisions in it — can be imported and tested on a machine with no display.
    """
    import gi

    gi.require_version("Gtk", "4.0")
    gi.require_version("Adw", "1")
    from gi.repository import Adw, Gtk

    model = model if model is not None else build_model(client.plan_restore())
    outcome = {"code": 0}

    def on_activate(app):
        window = Adw.ApplicationWindow(application=app, default_width=680, default_height=560)
        window.set_title("Restore your workspaces")

        toolbar = Adw.ToolbarView()
        header = Adw.HeaderBar()
        toolbar.add_top_bar(header)

        page = Adw.PreferencesPage()
        group = Adw.PreferencesGroup(title=model.heading)
        switches: dict[int, Gtk.Switch] = {}

        for row in model.rows:
            action_row = Adw.ActionRow(title=row.title, subtitle=row.subtitle)
            action_row.set_title_lines(2)
            action_row.set_subtitle_lines(2)
            if row.kind == "window":
                switch = Gtk.Switch(active=row.selected, valign=Gtk.Align.CENTER)
                action_row.add_suffix(switch)
                action_row.set_activatable_widget(switch)
                switches[row.index] = switch
            else:
                action_row.add_suffix(Gtk.Image.new_from_icon_name("network-vpn-symbolic"))
            group.add(action_row)
        page.add(group)

        if model.notes:
            notes = Adw.PreferencesGroup(title="Not doing")
            for note in model.notes:
                label = Gtk.Label(label=note, wrap=True, xalign=0)
                label.add_css_class("dim-label")
                label.set_margin_start(12)
                label.set_margin_end(12)
                label.set_margin_top(6)
                label.set_margin_bottom(6)
                notes.add(label)
            page.add(notes)

        toolbar.set_content(page)

        cancel = Gtk.Button(label="Not now")
        cancel.connect("clicked", lambda _b: window.close())
        header.pack_start(cancel)

        restore = Gtk.Button(label="Restore")
        restore.add_css_class("suggested-action")
        restore.set_sensitive(bool(switches))

        def on_restore(_button):
            chosen = [index for index, switch in switches.items() if switch.get_active()]
            restore.set_sensitive(False)
            restore.set_label("Restoring…")
            try:
                result = client.restore(chosen)
            except Exception as error:  # noqa: BLE001
                outcome["code"] = 1
                restore.set_label(f"Failed: {error}")
                return
            failed = [r for r in result.get("results", []) if r["state"] != "done"]
            outcome["code"] = 1 if failed else 0
            window.close()

        restore.connect("clicked", on_restore)
        header.pack_end(restore)

        window.set_content(toolbar)
        window.present()

        if screenshot:
            from gi.repository import GLib

            # One frame after presenting: the widgets have to be allocated before they can be
            # drawn into a texture.
            GLib.timeout_add(1200, lambda: (_save_screenshot(window, screenshot), app.quit())[1])

    app = Adw.Application(application_id="io.github.gortazar.restore_wss.Review")
    app.connect("activate", on_activate)
    app.run([])
    return outcome["code"]
