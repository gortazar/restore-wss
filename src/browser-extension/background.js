// The browser half of restore-wss.
//
// Provenance: adapted from the sibling gnome-tasks project's browser/background.js by the same
// author (GPL-2.0-or-later). The reporting shape, the debounce and the "leave a window that already
// shows these URLs alone" rule are its; what changed is why they are here
// (docs/browser-extensions-research.md §4):
//
//   * Firefox only, per the answered open question — so no Chrome branch;
//   * the browser's own "restore previous session" stays ON, so restore is *reconciliation*: fill in
//     the windows Firefox did not bring back, and never duplicate the ones it did;
//   * the host is a file drop, not a D-Bus client, because under snap confinement a native host
//     inherits Firefox's AppArmor profile and cannot reach the session bus.
//
// It has the `tabs` permission, which is the broadest trust in this whole tool, so there is nothing
// in here except reporting what is open and reopening what was lost.

const HOST = 'org.gnome.restore_wss';

/** Browsing generates a lot of events; one report per burst is plenty. */
const REPORT_DEBOUNCE_MS = 2500;

let port = null;
let reportTimer = null;

function connect() {
    try {
        port = browser.runtime.connectNative(HOST);
    } catch (error) {
        console.warn(`restore-wss: cannot reach the native host: ${error}`);
        return null;
    }

    port.onMessage.addListener(message => {
        if (message?.type === 'restore')
            reconcile(message).catch(error => console.warn(`restore-wss: restore failed: ${error}`));
    });

    // The host exits with the browser and the port dies with it; reconnecting on demand keeps this
    // working across a host restart without reloading the add-on.
    port.onDisconnect.addListener(() => {
        port = null;
    });

    return port;
}

function ensurePort() {
    return port ?? connect();
}

/** Every normal, non-private window with its tabs. Private windows are never reported at all. */
async function report() {
    const channel = ensurePort();
    if (!channel)
        return;

    const windows = await browser.windows.getAll({ populate: true });
    const info = await browser.runtime.getBrowserInfo?.().catch(() => null);

    channel.postMessage({
        type: 'report',
        browser: 'firefox',
        version: info?.version ?? '',
        windows: windows
            .filter(window => window.type === 'normal' && !window.incognito)
            .map(window => ({
                // The browser's own window id: it is what ties this report to a restore request
                // within one run of the browser, and it means nothing after a restart.
                id: `${window.id}`,
                focused: Boolean(window.focused),
                width: window.width,
                height: window.height,
                left: window.left,
                top: window.top,
                state: window.state ?? '',
                tabs: (window.tabs ?? [])
                    .filter(tab => !tab.incognito)
                    .map(tab => ({
                        url: tab.url ?? '',
                        title: tab.title ?? '',
                        pinned: Boolean(tab.pinned),
                        active: Boolean(tab.active),
                    })),
            })),
    });
}

function scheduleReport() {
    if (reportTimer)
        clearTimeout(reportTimer);
    reportTimer = setTimeout(() => {
        reportTimer = null;
        report().catch(error => console.warn(`restore-wss: report failed: ${error}`));
    }, REPORT_DEBOUNCE_MS);
}

/**
 * Fill in the windows Firefox's own session restore did not bring back.
 *
 * The browser restores itself first (that setting stays on, per the answered open question), so this
 * is a reconciliation and not a rebuild:
 *
 *   * a window already showing exactly this URL set is left alone — that is what makes running a
 *     restore twice harmless;
 *   * a window whose URLs are a superset is also left alone, because the user has since opened
 *     something in it and closing that would be worse than a missing window;
 *   * tabs are never appended to an existing window.
 */
async function reconcile(request) {
    const existing = (await browser.windows.getAll({ populate: true }))
        .filter(window => window.type === 'normal' && !window.incognito)
        .map(window => new Set((window.tabs ?? []).map(tab => tab.url)));

    const created = [];
    for (const group of request.windows ?? []) {
        const urls = (group.urls ?? []).filter(url => url && !url.startsWith('about:'));
        if (urls.length === 0)
            continue;
        if (existing.some(open => urls.every(url => open.has(url))))
            continue;

        const window = await browser.windows.create({ url: urls });
        created.push(window?.id);

        // Pinning happens after creation: windows.create takes no pinned flag. The active tab is
        // set the same way, and both are best-effort — a tab that failed to load still exists.
        const tabs = window?.tabs ?? [];
        const pinned = group.pinned ?? [];
        for (let index = 0; index < tabs.length; index++) {
            if (pinned[index])
                await browser.tabs.update(tabs[index].id, { pinned: true });
        }
        if (Number.isInteger(group.active) && tabs[group.active])
            await browser.tabs.update(tabs[group.active].id, { active: true });

        existing.push(new Set(urls));
    }

    // Report immediately after acting, so the daemon sees the result rather than waiting for the
    // debounce and wondering whether anything happened.
    await report();
    return created;
}

for (const event of [
    browser.tabs.onCreated, browser.tabs.onRemoved, browser.tabs.onUpdated, browser.tabs.onMoved,
    browser.tabs.onAttached, browser.tabs.onDetached, browser.tabs.onActivated,
    browser.windows.onCreated, browser.windows.onRemoved, browser.windows.onFocusChanged,
]) {
    event?.addListener(() => scheduleReport());
}

browser.runtime.onStartup?.addListener(() => connect());
browser.runtime.onInstalled?.addListener(() => connect());

connect();
report().catch(error => console.warn(`restore-wss: first report failed: ${error}`));
