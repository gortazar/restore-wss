// The compositor-side half of the D-Bus contract.
//
// This file is the twin of src/restore_wss/protocol.py, and tests/unit/test_protocol_parity.py
// fails if the two interface definitions drift apart. Two copies rather than a generated one
// because the extension is loaded by gnome-shell from a directory of plain JS with no build step,
// and a build step is exactly the thing that goes stale.

export const API_VERSION = 1;

export const SHELL_NAME = 'org.gnome.SessionCore';
export const SHELL_OBJECT_PATH = '/org/gnome/SessionCore';
export const SHELL_INTERFACE = 'org.gnome.SessionCore';

export const SHELL_IFACE_XML = `
<node>
  <interface name="org.gnome.SessionCore">
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

    <!-- Register a placement for a window that is *about* to appear, without launching anything.
         The daemon uses this when it starts a process itself — a terminal, which has to be given
         a working directory and a command on its command line, neither of which a desktop file
         can express. Same matching, same report, no spawning inside the compositor. -->
    <method name="ExpectWindow">
      <arg type="s" name="desktop_id" direction="in"/>
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
`;
