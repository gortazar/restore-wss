# Browsers and their tabs: what exists, and what this machine will actually allow

Written before any code for the v0.2 entry, from **reading sources and manifests and probing this
machine**, not from store descriptions. Where a claim comes from a document rather than an
observation it says so.

Target machine, as in v0.1:

```
GNOME Shell 46.0 · Mutter 14 · Wayland · Ubuntu 24.04 · Firefox 142 (snap, strict confinement)
```

Scope, per the answered open questions in `PLAN.md`: **Firefox only**; distribution by a signed
`.xpi` shipped as a release asset (option **c**); a third-party extension may only become a
dependency after the user has reviewed it; and the browser's own "restore previous session" stays
**on** — it already is here (`browser.startup.page = 3` in the live profile).

---

## 1. The finding that decides the design: snap confinement

Firefox on this machine is a strictly confined snap, so before comparing candidates the question is
what a native-messaging host can do *at all*. The answers are in the machine's own AppArmor profile,
`/var/lib/snapd/apparmor/profiles/snap.firefox.firefox`:

| Probe | Result | Consequence |
| --- | --- | --- |
| `grep -c native /…/snap.firefox.firefox` | **0** rules mention native messaging | nothing special is granted or denied by name; the generic file rules decide |
| `owner @{HOME}/snap/@{SNAP_INSTANCE_NAME}/** mrkix,` (line 305) | read **and execute** (`ix`) under `~/snap/firefox/` | a host binary and manifest placed under `~/snap/firefox/common/…` **can be executed** |
| `owner @{HOME}/snap/…/common/** wl,` (line 319) | write | the host can write files there, and so can the daemon |
| `owner @{HOME}/[^s.]** rwklix,` … (lines 2122‑2129) | everything in `$HOME` **except** hidden top-level directories and `~/snap` | `~/.mozilla/native-messaging-hosts` is **invisible** to this Firefox — which is why the machine's own `~/.mozilla/native-messaging-hosts/org.keepassxc.keepassxc_browser.json` cannot be working |
| `owner "@{HOME}/.mozilla/firefox{,/,/**}" rk,` (line 2348) | read-only | the deb-migration path, no execute |
| D-Bus rules: 140 `bus=session` rules, all `peer=(name=…)` allow-lists (portals, a11y, MPRIS, StatusNotifier, `org.freedesktop.DBus`) | no rule for an arbitrary name | a host executed with `ix` **inherits this confinement and cannot call `org.gnome.RestoreWss`** |

Two conclusions, and they are the spine of everything below:

1. **The bridge is viable** — but only if the manifest *and* the host live under
   `~/snap/firefox/common/.mozilla/native-messaging-hosts/` (the directory does not exist yet; the
   installer creates it).
2. **The bridge cannot speak D-Bus.** `ix` means the host runs inside Firefox's sandbox. So the
   host cannot be a D-Bus client of the daemon the way `gnome-tasks`' host is. The channel between
   the two has to be a **file drop under `~/snap/firefox/common/`**, which the confined host may
   write and the unconfined daemon may read (and vice versa for restore requests).

That is a design constraint discovered by probing, not a preference, and it is what makes the
sibling project's host unusable as-is (§4).

---

## 2. Session managers: could one be used as-is?

The deciding question for every one of them is not how good it is — it is **whether anything
outside the browser can read its state or ask it to restore**. A session manager with no
machine-readable interface cannot serve this project no matter how good it is.

| Extension | Licence / open? | Where it keeps state | Reachable from outside the browser? | Verdict |
| --- | --- | --- | --- | --- |
| **Tab Session Manager** (`sienori`, ★2.4k, active) | MPL‑2.0, open | extension storage (`storage`, `unlimitedStorage`); manual JSON export; optional cloud sync | **No.** Its manifest permissions are `storage, unlimitedStorage, tabs, downloads, identity, alarms, offscreen` — **no `nativeMessaging`**, so nothing local can talk to it, and nothing can ask it to restore | unusable as a component |
| **Tab Stash** (`josh-berry`, ★1.1k, active) | MPL‑2.0, open | **bookmarks** — "your open tabs will be stashed away in your bookmarks" | **Read: yes, indirectly.** Bookmarks live in `places.sqlite`, which an outside process can read. **Drive: no** — no native messaging, and a bookmark folder is not a live window | interesting as a *data* source, not as a mechanism |
| **Sync Tab Groups** (`Morikko`, ★149) | MIT, open, **archived 2021** | extension storage | No | unusable, and unmaintained |
| **Session Buddy** | closed source, Chrome-only | its own store | No published interface | out on licence, on browser, and on interface |
| **OneTab** | closed source | its own store | No | out |
| **Tabs Outliner** | closed source, Chrome-only | its own store | No | out |

**None of them is usable as a component.** The good ones are MPL and open, but "open source" is not
the property this project needs: it needs an interface, and a WebExtension without
`nativeMessaging` is a black box to the desktop by design. The two candidates that *do* expose
something (Tab Stash via bookmarks, Session Buddy via nothing) prove the point from both ends.

Worth stating plainly because it is the temptation: adopting one of these would mean either
persuading its author to add a native host, or scraping its private storage — a format it may
change at any time and never promised anyone.

---

## 3. Native-messaging bridges that already expose tabs to the desktop

### `brotab` — MIT, ★514, the strongest "use as-is" candidate

A Python CLI plus a WebExtension, connected by a native-messaging "mediator". Read from source:

* the host manifest is ordinary (`brotab/mediator/firefox_mediator.json`: `type: stdio`,
  `allowed_extensions: ["brotab_mediator@example.org"]`);
* the mediator then **opens an HTTP server on `127.0.0.1:4625`** (and the next free port per
  browser: `brotab/api.py`, `brotab/inout.py:get_available_tcp_port`);
* the surface (`brotab/mediator/http_server.py`) is `/list_tabs`, `/open_urls`, `/update_tabs`,
  `/get_active_tabs`, `/get_words`, `/get_text`, `/get_html`, `/get_screenshot`, `/get_pid`,
  `/get_browser`, `/shutdown`;
* tabs are identified as `prefix.window_id.tab_id` (`brotab/tab.py`), so **window grouping is
  available**, and `open_urls(urls, window_id=…)` can target a window.

So functionally it does most of what B2 and B4 need. Three reasons it is not adopted:

1. **It is an unauthenticated local HTTP service exposing the user's browsing.** Any process on the
   machine — any snap, any script, anything that can make a localhost request — can read every tab's
   URL, title, **text, HTML and screenshots**, and open pages. That is a much larger attack surface
   than this project needs, and v0.1's whole posture is the opposite (`docs/limitations.md`).
2. **It cannot work with snap Firefox as installed**, for the same reason our own host must be
   redesigned (§1): its mediator lives outside `~/snap/firefox/`, and it would also need to listen
   on a socket from inside the sandbox.
3. **The answered open question forbids adopting it silently**: a third-party extension with the
   `tabs` permission may only become a dependency after the user has reviewed it. Recommending an
   unauthenticated localhost tab API without that review would be exactly the wrong default.

What is taken from it: the **shape of the API** (list windows→tabs, open a set of URLs into a
named window) and the `prefix.window.tab` addressing idea.

### Tridactyl's native messenger — BSD‑2, and a warning

Its host (`src/native_main.nim`) accepts `run`, `run_async`, `eval`, `read`, `write`, `mkdir`,
`move`, `temp`, `env`, `getconfig`, `win_firefox_restart`: **arbitrary command execution and
filesystem access, driven by a browser extension.** It is well-made and it is what its users want.

Taken from it: the demonstration of what a native host must *not* be here. restore-wss's host will
have exactly two verbs — "here are my windows and tabs" and "reopen these" — with no path, no
command, and no eval anywhere in the protocol.

### KDE's Plasma Browser Integration — GPL‑3, published on AMO and the Chrome store

Read from the repository listing: the host is a set of plugins — `mprisplugin`, `downloadplugin`,
`kdeconnectplugin`, `purposeplugin`, `historyrunnerplugin` and **`tabsrunnerplugin`**, shipping
`plasma-runner-browsertabs.desktop`; the only D-Bus interfaces in `dbus/` are
`org.kde.krunner1.xml` and the two MPRIS ones.

So tabs *are* exposed outside the browser — but through **KRunner's search contract**
(`Match(query) → matches`, `Run(id, action)`): you can find a tab and activate it. There is no
"enumerate every window with its tabs", and no way to rebuild a window. Plus it requires Plasma's
runner infrastructure. A GNOME tool could technically speak `org.kde.krunner1` to it, and would get
a search box, not a snapshot.

Taken from it: confirmation that the *host-per-desktop, extension-in-the-store* model is normal and
respectable, and that a search interface is not a session interface.

### `chrome-gnome-shell` / `gnome-browser-connector` — the packaging model, present on this machine

This is the best available template for shipping a host manifest with a desktop application, and it
is installed here, so it can be read rather than guessed:

```console
$ cat /usr/lib/mozilla/native-messaging-hosts/org.gnome.browser_connector.json
{ "name": "org.gnome.browser_connector", "path": "/usr/bin/gnome-browser-connector-host",
  "type": "stdio", "allowed_extensions": ["chrome-gnome-shell@gnome.org"] }
$ ls /etc/chromium/native-messaging-hosts/ /etc/opt/chrome/native-messaging-hosts/
org.gnome.browser_connector.json  org.gnome.chrome_gnome_shell.json
```

Taken from it: the manifest shape, the `allowed_extensions` pinning (which is how the host refuses
to talk to any extension but ours), and the per-browser directory table the installer needs — with
the snap path added from §1, which this model does not cover.

---

## 4. The sibling extension: `gnome-tasks/browser/`

Read from source (123 lines of `background.js`, a 136-line GJS host, two manifests).

**What it gets right, and what is worth reusing:**

* one `background.js` for Firefox (`browser`, MV2) and Chrome (`chrome`, MV3), switching on
  `typeof browser !== 'undefined'`;
* a debounced report (2500 ms) fed by every relevant event —
  `tabs.onCreated/onRemoved/onUpdated/onMoved/onAttached/onDetached/onActivated`,
  `windows.onCreated/onRemoved/onFocusChanged`;
* `windows.getAll({populate: true})` filtered to `type === 'normal' && !incognito`, reporting
  `{url, title, pinned, active}` per tab;
* restore that **skips a window already showing exactly those URLs**
  (`openSets.has(urls.join('\n'))`), so restoring twice does not double the tabs;
* pinning applied after `windows.create()`, because `create` takes no pinned flag;
* a reconnect-on-demand port, because the host dies with the browser.

**What cannot be reused:**

* the **host** (`src/native-host/gnome-tasks-browser-host.js`) forwards to
  `org.gnome.Tasks.ReportAppState` **over D-Bus** — impossible under snap confinement (§1), and it
  is GJS, where this project's daemon side is Python;
* its restore contract is per-task ("rebuild this set"), not "reconcile with what the browser
  already restored by itself", which is the actual problem here now that
  `browser.startup.page = 3` stays on;
* it reports no window geometry or id beyond `window.id`, so it cannot help correlate a browser
  window with a compositor window (§6);
* it is unsigned and loaded by hand, which the answered distribution question rules out.

**Recommendation: adapt the extension, replace the host.** The reporting/reconciliation logic is
proven and small; it is copied with attribution and changed where the constraints differ (Firefox
only, geometry included, file-drop transport, reconcile-not-rebuild). The host is written fresh, in
Python, as a file-drop bridge with two verbs.

---

## 5. Routes that need no extension at all

### Firefox's `recovery.jsonlz4` — much stronger than expected

Decoded here with a 40-line pure-Python `mozlz4` reader (magic `mozLz40\0`, `u32` size, one LZ4
block — no dependency needed), against the live profile
`~/snap/firefox/common/.mozilla/firefox/cqdb58zj.default/sessionstore-backups/recovery.jsonlz4`:

```
decompressed bytes: 762688
top-level keys: _closedWindows, cookies, global, maxSplitViewId, savedGroups,
                selectedWindow, session, version, windows
windows: 6 | closed windows: 5
  window 0: 7 tabs, selected=2, geometry={1165x1408 @0,0}, sizemode=maximized
  window 1: 1 tab   … window 5: 3 tabs            (27 tabs in total)
  per tab: entries[] (url, title), pinned, index, lastAccessed, hidden, image, userContextId
  per window: width, height, screenX, screenY, sizemode, groups, closedGroups, _closedTabs
```

That is: **every window, every tab with URL and title, pinned state, the selected tab, tab groups —
and per-window geometry.** No extension, no `tabs` permission, no signing, no confinement problem.

The costs, stated honestly:

* **It is an undocumented internal format.** `entries[]` shapes differ between tabs (some carry a
  full session-history entry with `docshellUUID`, others just `{title, url,
  triggeringPrincipal_base64}`), and Firefox may change any of it.
* **Freshness is Firefox's business, not ours.** The documented flush interval is 15 s
  (`browser.sessionstore.interval`, not overridden here), but the live file was **48 minutes old**
  when probed — consistent with an idle browser that had nothing to flush, and *not* something this
  project can guarantee at the moment of a power cut.
* **It is read-only.** There is no way to ask a running Firefox to reopen a window from it, so it
  cannot serve restore-time reconciliation; and rewriting the file is out of the question.
* **It cannot name a compositor window** — but its geometry gives correlation a much better signal
  than titles alone (§6).

### Chrome's SNSS `Sessions/Session_*`

Priced and out of scope: the answered question is Firefox only. For the record, the format is
Chrome's own length-prefixed command log; `chrome-session-dump` parses it, per-command semantics are
undocumented, and nothing about it identifies a compositor window either.

### Marionette and the DevTools/BiDi port

`firefox --marionette` (default TCP 2828) and `--remote-debugging-port` expose full control of the
browser. Both are **unauthenticated control channels on localhost** — anything that can connect can
read every page and act as the user — and Marionette is designed for automation, not for sharing a
profile with a human. Documentation-level facts, not probed here. Rejected: enabling either to read
a tab list would be a far larger hole than the `tabs` permission this project is already careful
about.

### The browser's own "restore previous session"

Already on here (`browser.startup.page = 3`), and per the answered question it stays on. This is
what makes the design *reconciliation* rather than restoration: Firefox brings its own windows and
tabs back; what it cannot do is put window 1 on workspace 3 and window 2 on workspace 6. That is the
job.

---

## 6. Correlating a compositor window with a browser window

The crux, and the reason this entry is not "easy". Available signals, best first:

| Signal | Where from | Strength |
| --- | --- | --- |
| **Window geometry** | `recovery.jsonlz4` (`screenX/screenY/width/height/sizemode`) vs `Meta.Window.get_frame_rect()` | strong when windows differ in size/position; useless when they are all maximized — **as they are on this machine: all six windows report `1165x1408 @0,0 maximized`** |
| **Active tab title vs window title** | extension/`selected` tab vs `Meta.Window.get_title()` | Firefox titles a window after its active tab, so this is usually decisive — and it is what v0.1's matcher already scores |
| **Window count and creation order** | both sides | breaks ties, cheaply, and is wrong exactly when the user has reordered windows |
| **Tab count** | both sides | weak on its own, useful as a filter |

The honest reading of the machine's own data: geometry will *not* save us here, because every window
is maximized, so title matching stays the primary signal and the v0.1 rule applies unchanged — score
it, require a spread, and **degrade to "a browser window on this workspace, tabs unknown" rather than
attach the wrong tab set**.

---

## 7. What restore-wss takes from each

| From | Taken |
| --- | --- |
| This machine's AppArmor profile | The transport: host + manifest under `~/snap/firefox/common/`, and a **file drop** instead of D-Bus, because a host executed with `ix` inherits the browser's confinement |
| `brotab` | The API shape (windows→tabs, open URLs into a window) and `prefix.window.tab` addressing. **Not** its unauthenticated localhost HTTP server |
| Tridactyl's messenger | A worked example of what a host must not be: no `run`, no `eval`, no paths in the protocol |
| Plasma Browser Integration | Confirmation of the host-per-desktop + store-published-extension model; and that a search interface is not a session interface |
| `chrome-gnome-shell` / `gnome-browser-connector` | The host manifest shape, `allowed_extensions` pinning, and the per-browser directory table (installed here, so copied rather than guessed) |
| `gnome-tasks/browser/` | The reporter and its debounce, the private-window filter, and the "leave a window already showing these URLs alone" rule. Host replaced |
| Tab Stash | The idea that bookmarks are the one browser store an outside process can read — noted, not used |
| Tab Session Manager | A negative control: excellent, open, and unusable here, because no `nativeMessaging` means no interface |
| `recovery.jsonlz4` | A **real** fallback: windows, tabs, pinned, selected, groups and geometry, with a pure-Python reader and no permissions at all |

## 8. Go / no-go

**Go, with an extension written here — and the offline reader promoted from footnote to first-class
fallback.**

1. **No existing extension is adopted.** None of the session managers has an interface; `brotab` has
   one but pays for it with an unauthenticated local HTTP service and cannot work with snap Firefox
   as installed; and the answered open question requires user review before any such dependency.
   The extension is ours, adapted from the sibling project with attribution.
2. **The host is a file-drop bridge, not a D-Bus client.** Forced by confinement (§1), and it turns
   out to be simpler and easier to test: two JSON files under `~/snap/firefox/common/`, one each
   way, with the daemon watching one and the host polling the other.
3. **`recovery.jsonlz4` is implemented too, as the tier below the extension.** It needs no
   permission and no install step, it works before the user has ever enabled the add-on, and this
   probe showed it carries more than expected. The snapshot records which source a browser block
   came from, and the review step says so.
4. **Correlation stays title-first with a confidence**, because on this machine geometry is
   degenerate (all windows maximized). Low confidence degrades to "browser window, tabs unknown" —
   never to a wrong tab set.
5. **Signing is a release-time step needing the user's AMO credentials.** The `.xpi` is built and
   published by CI; `web-ext sign` runs only when `AMO_JWT_ISSUER`/`AMO_JWT_SECRET` are present in
   the repository's secrets. Until they are, the asset is unsigned and the README says so — a fact
   to hand to the user, not a decision to make for them.

### What this changes in the plan

* B2 gains a transport design it did not have (file drop, not D-Bus), and loses the Chrome half.
* B2 also gains the `recovery.jsonlz4` reader, which the plan had as a fallback "footnote" and this
  research promotes to a shipped tier — it is the only thing that works with no extension installed.
* B3's primary signal is confirmed to be the title, not geometry, on this hardware.
* B5's release step needs a documented, secret-gated signing job rather than an assumed one.
