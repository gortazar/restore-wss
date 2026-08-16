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

    <!-- Something about the windows changed. Coalesced inside the extension: a window drag emits
         position-changed continuously and one message per motion event would be absurd. -->
    <signal name="WindowsChanged"/>
  </interface>
</node>
`;
