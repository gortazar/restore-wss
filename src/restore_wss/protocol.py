"""The D-Bus contract between the two restore-wss processes.

Import-light on purpose: this module is read by the daemon, by the CLI and by the tests, and it
must not need PyGObject to be importable, so that a machine with no desktop can still run the
schema and policy tests.

Two interfaces, pointing in opposite directions:

``org.gnome.SessionCore``
    Owned by the **extension**, i.e. living inside the compositor. It observes and places windows
    and nothing else: no file I/O, no process spawning, no policy. That is deliberate — it is the
    shared capture/placement core (see ``docs/shared-core.md``), so anything specific to restoring
    a session after a reboot belongs on the other side of this bus.

``org.gnome.RestoreWss``
    Owned by ``restore-wss-daemon``. Holds the snapshot, does the ``/proc`` walking, runs the
    policy, talks to NetworkManager, and answers the CLI.

Payload style follows the same rule as the disk format: simple things are typed D-Bus arguments,
whole documents cross as JSON strings, because the snapshot is already a versioned JSON schema
(``docs/state-schema.md``) and mirroring it into D-Bus types would mean two things to migrate.
"""

from __future__ import annotations

#: Bumped when a change to either interface is not backwards compatible. Both sides announce it,
#: and a mismatch is reported rather than worked around.
API_VERSION = 1

DAEMON_NAME = "org.gnome.RestoreWss"
DAEMON_OBJECT_PATH = "/org/gnome/RestoreWss"
DAEMON_INTERFACE = "org.gnome.RestoreWss"

SHELL_NAME = "org.gnome.SessionCore"
SHELL_OBJECT_PATH = "/org/gnome/SessionCore"
SHELL_INTERFACE = "org.gnome.SessionCore"

DAEMON_IFACE_XML = f"""
<node>
  <interface name="{DAEMON_INTERFACE}">
    <!-- Liveness and handshake. Returns "<api-version> <daemon-version> <message>", so a client
         can check it is talking to a daemon it understands before doing anything else. -->
    <method name="Ping">
      <arg type="s" name="message" direction="in"/>
      <arg type="s" name="reply" direction="out"/>
    </method>

    <!-- The current in-memory snapshot, as JSON (docs/state-schema.md). This is what
         `restore-wss status --json` prints; it is not necessarily what is on disk yet. -->
    <method name="GetSnapshot">
      <arg type="s" name="json" direction="out"/>
    </method>

    <!-- Capture now and write the snapshot to disk, ignoring the debounce. Returns the path
         written. -->
    <method name="Save">
      <arg type="s" name="path" direction="out"/>
    </method>

    <!-- What restoring the snapshot on disk would do to the desktop as it is right now, as JSON:
         the actions, what was skipped and why, and the matches too ambiguous to act on. Read-only
         — this is what `restore-wss restore --dry-run` and the review dialog show. -->
    <method name="PlanRestore">
      <arg type="s" name="json" direction="out"/>
    </method>

    <!-- Carry out the plan. `only` is a JSON array of action indices from PlanRestore, so the
         review step can restore a subset; an empty array means all of them. Returns the outcome
         per action. -->
    <method name="Restore">
      <arg type="s" name="only_json" direction="in"/>
      <arg type="s" name="result_json" direction="out"/>
    </method>

    <!-- True while capture is running and the compositor-side core is reachable. -->
    <property name="Capturing" type="b" access="read"/>
    <!-- Absent (empty string) when the extension is not running: the daemon is useless without
         it, and saying so is more helpful than an empty window list. -->
    <property name="ShellCoreVersion" type="s" access="read"/>
  </interface>
</node>
"""

SHELL_IFACE_XML = f"""
<node>
  <interface name="{SHELL_INTERFACE}">
    <!-- "<api-version> <extension-version>". -->
    <method name="Ping">
      <arg type="s" name="message" direction="in"/>
      <arg type="s" name="reply" direction="out"/>
    </method>

    <!-- Every window the compositor is managing, as a JSON array. One object per window with the
         fields docs/state-schema.md calls a window record: id, wm_class, title, pid, app_id,
         workspace, monitor, frame rect, maximized/fullscreen/minimized, and the stacking index. -->
    <method name="ListWindows">
      <arg type="s" name="json" direction="out"/>
    </method>

    <!-- Workspaces and monitors, as JSON: workspace count, active index, and for each monitor its
         index, connector, geometry and scale. Monitors are keyed by connector because Mutter's
         indices renumber when a display is replugged. -->
    <method name="GetLayout">
      <arg type="s" name="json" direction="out"/>
    </method>

    <!-- Make sure at least this many workspaces exist; returns how many there are afterwards.
         Under GNOME's dynamic-workspaces setting Mutter manages the count itself, so the answer
         can be larger than the request. -->
    <method name="EnsureWorkspaces">
      <arg type="u" name="count" direction="in"/>
      <arg type="u" name="actual" direction="out"/>
    </method>

    <method name="ActivateWorkspace">
      <arg type="u" name="index" direction="in"/>
    </method>

    <!-- Move a window that already exists. The placement is the JSON docs/state-schema.md calls a
         placement: workspace, monitor connector, frame (absolute or monitor-relative), and the
         maximized/fullscreen/minimized flags. Returns what was requested; whether the client
         accepted it is a later question, answered by GetPlacementVerdict. -->
    <method name="PlaceWindow">
      <arg type="s" name="window_id" direction="in"/>
      <arg type="s" name="placement_json" direction="in"/>
      <arg type="s" name="requested_json" direction="out"/>
    </method>

    <!-- Whether a placement stuck. Separate from PlaceWindow because a Wayland geometry change is
         a negotiation: the frame does not change until the client acknowledges the configure, so
         reading it back immediately always says "refused". -->
    <method name="GetPlacementVerdict">
      <arg type="s" name="window_id" direction="in"/>
      <arg type="s" name="requested_json" direction="in"/>
      <arg type="s" name="verdict_json" direction="out"/>
    </method>

    <!-- Start an application and place the window it produces. Returns a launch id to ask about
         later; the window is matched to the launch by application id and timing, because the
         activation token is not readable off the window. -->
    <method name="LaunchApp">
      <arg type="s" name="desktop_id" direction="in"/>
      <arg type="s" name="uris_json" direction="in"/>
      <arg type="s" name="placement_json" direction="in"/>
      <arg type="s" name="launch_id" direction="out"/>
    </method>

    <method name="GetLaunchReport">
      <arg type="s" name="launch_id" direction="in"/>
      <arg type="s" name="report_json" direction="out"/>
    </method>

    <!-- Something about the windows changed. Coalesced inside the extension: a window drag emits
         position-changed continuously and one message per motion event would be absurd. -->
    <signal name="WindowsChanged"/>
  </interface>
</node>
"""
