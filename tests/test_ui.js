const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');

const html = fs.readFileSync(new URL('../index.html', `file://${__filename}`), 'utf8');
// The desktop panel toggle is enabled only by the native app's settings response.
{
  const callbacks = {}, messages = [], toggle = { disabled: true, checked: false, addEventListener(type, callback) { callbacks[type] = callback; } };
  const mode = { disabled: true, value: 'attention', addEventListener(type, callback) { callbacks['mode-'+type] = callback; } }, help = {textContent:''};
  const state = { launchAvailable: true, document: { querySelector: selector => selector === '#herdr-mode' ? mode : selector === '#herdr-mode-help' ? help : toggle }, window: { addEventListener(type, callback) { callbacks[type] = callback; } }, nativePost: message => messages.push(message) };
  const source = html.slice(html.indexOf('const herdrToggle='), html.indexOf("document.querySelector('#open-browser').addEventListener"));
  assert.ok(source.includes('setHerdrPanel'));
  vm.runInNewContext(source, state);
  callbacks['native-settings']({ detail: { available: true, herdrPanel: true, herdrPanelMode: 'unread' } });
  assert.equal(toggle.disabled, false); assert.equal(toggle.checked, true);
  assert.equal(mode.value, 'unread'); assert.equal(mode.disabled, false);
  assert.match(help.textContent, /not marked as viewed/);
  toggle.checked = false; callbacks.change();
  assert.deepEqual(JSON.parse(JSON.stringify(messages[0])), { action: 'setHerdrPanel', enabled: false });
  mode.value = 'input'; callbacks['mode-change']();
  assert.deepEqual(JSON.parse(JSON.stringify(messages[1])), { action: 'setHerdrPanelMode', mode: 'input' });
  assert.match(help.textContent, /Questions and approvals/);
  mode.value = 'invalid'; callbacks['mode-change'](); assert.equal(messages.length, 2);
  callbacks['native-settings']({ detail: { available: true, herdrPanel: true, herdrPanelMode: 'invalid' } });
  assert.equal(mode.value, 'attention');
  callbacks['native-settings']({ detail: { available: false } });
  assert.equal(toggle.disabled, true); assert.equal(toggle.checked, false);
  assert.equal(mode.disabled, true);
}
const definitions = [
  html.match(/function nativePost\(message\)\{[^\n]*\}/)?.[0],
  html.match(/function setSettingsView\(show\)\{[^\n]*\}/)?.[0],
  html.match(/function setNativeAvailable\(available\)\{.*?nativeActions\.forEach\(button=>button\.disabled=!available\)\}/)?.[0],
].filter(Boolean);
assert.equal(definitions.length, 3, 'expected native settings helpers in index.html');

const elements = Object.fromEntries(['#dashboard-view', '#settings-view', '#main-nav', '#settings-open', '#settings-back']
  .map(selector => [selector, { hidden: false, focus() { this.focused = true; } }]));
const sent = [];
let directoryLoadRequested = false;
const context = {
  window: { webkit: { messageHandlers: { app: { postMessage: message => sent.push(message) } } } },
  document: { querySelector: selector => elements[selector] },
  loadDirectories() { directoryLoadRequested = true; },
  loadPhoneAccess() {},
  launchToggle: { disabled: false },
  nativeActions: [{ disabled: false }, { disabled: false }],
};
vm.runInNewContext(definitions.join('\n'), context);
vm.runInNewContext('setSettingsView(true); setNativeAvailable(false);', context);
assert.equal(sent.length, 1);
assert.equal(sent[0].action, 'settings');
assert.equal(elements['#settings-view'].hidden, false);
assert.equal(context.launchToggle.disabled, true);
assert.ok(context.nativeActions.every(button => button.disabled));

assert.equal(directoryLoadRequested, true);

const directoryHelpers = [
  html.match(/function applyDirectorySettings\(d\)\{[^\n]*\}/)?.[0],
  html.match(/function directoryValues\(\)\{[^\n]*\}/)?.[0],
  html.match(/async function loadDirectories\(\)\{[\s\S]*?\n\}/)?.[0],
  html.match(/async function saveDirectories\(e\)\{[\s\S]*?\n\}/)?.[0],
].filter(Boolean);
assert.equal(directoryHelpers.length, 4, 'expected directory settings helpers');

(async () => {
  let calls = [], rows = [], message = '', refreshes = 0;
  const state = {
    directoriesBusy: false,
    directoriesDirty: true,
    csrf: 'token',
    firstSettingsLoad: true,
    showpassCheckout: { value: '' },
    dockerSocket: { value: '' },
    document: { querySelector() { return {}; } },
    applyAppearance() {},
    setSettingsView() { throw Error('Existing configuration must not open settings.'); },
    directoryList: {
      querySelectorAll() { return [{ value: '/Projects/one' }, { value: '/Projects/two' }]; },
      replaceChildren() { rows = []; },
    },
    directoryBusy(busy) { state.directoriesBusy = busy; },
    directoryMessage(text) { message = text; },
    addDirectory(path) { rows.push(path); },
    async refresh() { refreshes++; },
    async fetch(url, options) {
      calls.push({ url, options });
      return { ok: true, json: async () => ({ directories: ['/Projects/normalized'], showpass_checkout: '/Projects/web-app', docker_socket: '/tmp/docker.sock', csrf: 'new-token' }) };
    },
  };
  vm.runInNewContext(directoryHelpers.join('\n'), state);
  await vm.runInNewContext('loadDirectories()', state);
  assert.equal(calls.length, 0, 'opening settings must preserve unsaved paths');
  state.directoriesDirty = false;
  await vm.runInNewContext('loadDirectories()', state);
  assert.equal(calls[0].url, '/api/settings');
  assert.deepEqual(rows, ['/Projects/normalized']);
  state.directoriesDirty = true;
  await vm.runInNewContext('saveDirectories({preventDefault(){}})', state);
  const request = calls[1];
  assert.equal(request.options.method, 'POST');
  assert.equal(request.options.headers['X-CSRF-Token'], 'new-token');
  assert.deepEqual(JSON.parse(request.options.body), { directories: ['/Projects/one', '/Projects/two'], showpass_checkout: '/Projects/web-app', docker_socket: '/tmp/docker.sock' });
  assert.equal(state.directoriesDirty, false);
  assert.equal(state.directoriesBusy, false);
  assert.equal(refreshes, 1);
  assert.equal(message, 'Settings saved.');
  state.directoriesDirty = true;
  state.fetch = async () => ({ ok: false, status: 400, json: async () => ({ error: 'Directory does not exist.' }) });
  await vm.runInNewContext('saveDirectories({preventDefault(){}})', state);
  assert.equal(message, 'Directory does not exist.');
  assert.equal(state.directoriesDirty, true, 'failed save must preserve edits');
  assert.equal(state.directoriesBusy, false);
})().catch(error => { console.error(error); process.exitCode = 1; });

// Discovered projects sharing a name must retain separate controls and pending states.
const projectHelpers = [
  html.match(/function operationLabel\(w\)\{[^\n]*\}/)?.[0],
  html.match(/function workspaceBusy\(w\)\{[^\n]*\}/)?.[0],
  html.match(/function projectKey\(w\)\{[^\n]*\}/)?.[0],
  html.match(/function actionPayload\(w,action\)\{[^\n]*\}/)?.[0],
  html.match(/function metricValue\(value,digits,unit\)\{[^\n]*\}/)?.[0],
  html.match(/function workspaceCard\(w\)\{[\s\S]*?\n\}/)?.[0],
];
assert.ok(projectHelpers.every(Boolean));
const projectContext = {
  esc: value => String(value ?? '—').replace(/[&<>"']/g, char => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[char])),
  num: value => Number(value) || 0,
  pending: new Map([['/Projects/one/demo','start']]),
  icon: name => `<svg>${name}</svg>`,
};
vm.runInNewContext(projectHelpers.join('\n'), projectContext);
const generic = { id: '/Projects/one/demo', path: '/Projects/one/demo', name: 'demo', managed: false, kind: 'worktree', branch: 'feature/test', status: 'detected', cpu_percent: null, memory_mb: null, can_destroy: true };
const markup = projectContext.workspaceCard(generic);
assert.match(markup, /Worktree · feature\/test/);
assert.match(markup, /<b>—<\/b>/);
assert.doesNotMatch(markup, /Centrifugo|data-delete/);
assert.match(markup, /Configure demo/);
assert.match(markup, /data-configure="\/Projects\/one\/demo"/);
assert.equal(projectContext.projectKey({ ...generic, id: '/Projects/two/demo' }), '/Projects/two/demo');
assert.deepEqual(JSON.parse(JSON.stringify(projectContext.actionPayload(generic, 'start'))), { action: 'start', project: '/Projects/one/demo' });
assert.deepEqual(JSON.parse(JSON.stringify(projectContext.actionPayload({ name: 'managed-demo', managed: true }, 'stop'))), { action: 'stop', name: 'managed-demo' });
assert.equal(projectContext.metricValue(0, 1, '%'), '0.0%');

const saveProjectSource = html.match(/async function saveProject\(e\)\{[\s\S]*?\n\}/)?.[0];
assert.ok(saveProjectSource);
(async () => {
  let request, closed = false, refreshed = false, message = '';
  const state = {
    configuredProject: generic,
    projectSave: { disabled: false },
    projectUrl: { value: 'http://localhost:3000' },
    projectCommand: { value: 'npm run dev' },
    csrf: 'project-token',
    projectMessageText(text) { message = text; },
    projectDialog: { close() { closed = true; } },
    async refresh() { refreshed = true; },
    async fetch(url, options) { request = { url, options }; return { ok: true, json: async () => ({}) }; },
  };
  vm.runInNewContext(saveProjectSource, state);
  await vm.runInNewContext('saveProject({preventDefault(){}})', state);
  assert.equal(request.url, '/api/project');
  assert.equal(request.options.headers['X-CSRF-Token'], 'project-token');
  assert.deepEqual(JSON.parse(request.options.body), { path: generic.path, url: 'http://localhost:3000', command: 'npm run dev' });
  assert.ok(closed && refreshed);
  closed = false; state.projectSave.disabled = false;
  state.fetch = async () => ({ ok: false, status: 400, json: async () => ({ error: 'Invalid command.' }) });
  await vm.runInNewContext('saveProject({preventDefault(){}})', state);
  assert.equal(message, 'Invalid command.');
  assert.equal(closed, false);
  assert.equal(state.projectSave.disabled, false);
})().catch(error => { console.error(error); process.exitCode = 1; });

// Native folder responses are correlated to requests; cancel and forged events do not edit settings.
const pickerSource = ['chooseDirectory', 'receiveDirectory'].map(name => html.match(new RegExp(`function ${name}\\([^\\n]*\\}`))?.[0]);
assert.ok(pickerSource.every(Boolean));
let picked = [], dirtyCount = 0;
const pickerContext = {
  directoriesBusy: false, directoryRequest: 0, directoryRequests: new Map(),
  window: { webkit: { messageHandlers: { app: {} } } },
  nativePost(message) { picked.push(message); },
  addDirectory(path) { picked.push(path); return { focus() {} }; },
  showpassCheckout: { value: '' },
  markDirectoriesDirty() { dirtyCount++; },
};
vm.runInNewContext(pickerSource.join('\n'), pickerContext);
pickerContext.chooseDirectory('workspace');
assert.equal(picked[0].action, 'chooseDirectory');
pickerContext.receiveDirectory({ detail: { requestId: 'unknown', purpose: 'workspace', path: '/ignored' } });
assert.equal(dirtyCount, 0);
pickerContext.receiveDirectory({ detail: { ...picked[0], path: '/Projects/chosen' } });
assert.equal(picked[1], '/Projects/chosen');
assert.equal(dirtyCount, 1);
pickerContext.chooseDirectory('showpass');
pickerContext.receiveDirectory({ detail: { ...picked[2], path: null } });
assert.equal(dirtyCount, 1, 'cancelling a folder chooser must preserve settings');
pickerContext.chooseDirectory('showpass');
pickerContext.receiveDirectory({ detail: { ...picked[3], path: '/Projects/web-app' } });
assert.equal(pickerContext.showpassCheckout.value, '/Projects/web-app');

const cleanupMarkup = projectContext.workspaceCard({ name: 'deleting-demo', status: 'destroying', can_stop: true, can_destroy: true, operation: { busy: true, stage: 'Removing checkout' } });
assert.match(cleanupMarkup, /Removing checkout/);
assert.match(cleanupMarkup, /aria-busy="true"/);
assert.match(cleanupMarkup, /Deleting…/);
assert.doesNotMatch(cleanupMarkup, /data-action/);
assert.doesNotMatch(cleanupMarkup, /data-delete/);
assert.match(projectContext.workspaceCard({ name: 'interrupted-demo', status: 'destroying', can_destroy: true, operation: { busy: false } }), /Cleanup interrupted/);

(async () => {
  let settingsOpened = 0, writes = 0;
  const state = {
    directoriesBusy: false, directoriesDirty: false, firstSettingsLoad: true, csrf: '',
    directoryList: { replaceChildren() {}, querySelectorAll() { return []; } },
    showpassCheckout: { value: '' }, dockerSocket: { value: '' },
    document: { querySelector() { return {}; } },
    directoryBusy(busy) { state.directoriesBusy = busy; }, directoryMessage() {}, addDirectory() {}, applyAppearance() {},
    setSettingsView(show) { if (show) settingsOpened++; },
    async fetch(url, options) { if (options?.method === 'POST') writes++; return { ok: true, json: async () => ({ directories: [], csrf: 'first-run' }) }; },
  };
  vm.runInNewContext(directoryHelpers.join('\n'), state);
  await state.loadDirectories();
  await state.loadDirectories();
  assert.equal(settingsOpened, 1, 'empty first-run settings should open setup only once');
  assert.equal(writes, 0, 'setup must never save a guessed directory');
})().catch(error => { console.error(error); process.exitCode = 1; });

// A save or finished job during a slow snapshot must trigger another snapshot immediately.
const refreshSource = html.match(/async function refresh\(\)\{[^\n]*\}/)?.[0];
assert.ok(refreshSource);
(async () => {
  let calls = 0, release;
  const firstFetch = new Promise(resolve => { release = resolve; });
  const state = {
    refreshing: false, refreshQueued: false, csrf: '', latest: [], render() {}, renderDocker() {}, showMessage() {},
    document: { querySelector() { return {}; } },
    async fetch() { calls++; if (calls === 1) await firstFetch; return { ok: true, json: async () => ({ workspaces: [], docker: {} }) }; },
  };
  vm.runInNewContext(refreshSource, state);
  const loading = state.refresh();
  await state.refresh();
  assert.equal(calls, 1);
  release();
  await loading;
  assert.equal(calls, 2);
  assert.equal(state.refreshing, false);
})().catch(error => { console.error(error); process.exitCode = 1; });

// Pending operations replace stale status badges even before the next snapshot.
const stopped = { name: 'managed-demo', status: 'stopped', health: { status: 'stopped' }, can_start: true };
projectContext.pending.set('managed-demo', 'start');
const starting = projectContext.workspaceCard(stopped);
assert.match(starting, /Starting…/);
assert.match(starting, /aria-busy="true"/);
assert.doesNotMatch(starting, /health-stopped|>stopped<|data-action/);
assert.match(projectContext.workspaceCard(generic), /Starting…/);
projectContext.pending.delete('managed-demo');
assert.match(projectContext.workspaceCard(stopped), /aria-busy="false"/);
assert.match(projectContext.workspaceCard(stopped), /data-action="start"/);
const stopping = projectContext.workspaceCard({ ...stopped, can_start: false, can_stop: false, operation: { busy: true, action: 'stop', stage: 'Stopping services' } });
assert.match(stopping, /Stopping…/);
assert.match(stopping, /Stopping services/);
assert.match(stopping, /role="status"/);
assert.doesNotMatch(stopping, /data-action/);
assert.match(html, /\.spinner\{animation:spin/);
assert.match(html, /@keyframes spin/);
assert.match(html, /@media\(prefers-reduced-motion:reduce\)[^\n]*\.spinner\{animation:none\}/);

// Polling refreshes intermediate metrics, then releases local pending state on every exit.
const watchJobSource = html.match(/async function watchJob\(id,report=showMessage,complete=\(\)=>\{\}\)\{[^\n]*\}/)?.[0];
assert.ok(watchJobSource);
(async () => {
  for (const ending of ['done', 'error', 'unknown', 'http-error']) {
    let fetches = 0, refreshes = 0, messages = [];
    const state = {
      jobs: new Set(), pending: new Map([['demo', 'start']]), window: { jobNames: new Map([['job', 'demo']]) },
      setTimeout(callback) { callback(); },
      showMessage(text, error) { messages.push({ text, error }); },
      async refresh() { refreshes++; },
      async fetch() { fetches++; return { ok: ending !== 'http-error', status: 404, json: async () => ({ state: fetches === 1 ? 'running' : ending, error: ending === 'http-error' ? 'Unknown job' : undefined }) }; },
    };
    vm.runInNewContext(watchJobSource, state);
    await state.watchJob('job');
    assert.equal(state.pending.size, 0);
    assert.equal(state.jobs.size, 0);
    assert.equal(state.window.jobNames.size, 0);
    assert.equal(refreshes, ending === 'http-error' ? 1 : 2);
    assert.equal(messages.at(-1).error, ending !== 'done');
  }
})().catch(error => { console.error(error); process.exitCode = 1; });

// Reset reuses managed lifecycle requests, preserves data, and disappears during operations.
const resetWorkspace = { ...stopped, managed: true, can_reset: true };
for (const status of ['running', 'stopped', 'provisioned']) {
  const card = projectContext.workspaceCard({ ...resetWorkspace, status });
  assert.match(card, /data-action="reset"/);
  assert.match(card, /title="Reset managed-demo: restart services, preserving code and data"/);
  assert.match(card, /aria-label="Reset managed-demo: restart services, preserving code and data"/);
  assert.match(card, /<svg>reset<\/svg>/);
}
assert.deepEqual(JSON.parse(JSON.stringify(projectContext.actionPayload(resetWorkspace, 'reset'))), { action: 'reset', name: 'managed-demo' });
assert.doesNotMatch(projectContext.workspaceCard({ ...resetWorkspace, can_reset: false }), /data-action="reset"/);
assert.doesNotMatch(projectContext.workspaceCard({ ...generic, id: '/Projects/idle/demo', can_reset: true }), /data-action="reset"/);
assert.doesNotMatch(projectContext.workspaceCard({ ...resetWorkspace, operation: { busy: true, action: 'stop' } }), /data-action="reset"/);
projectContext.pending.set('managed-demo', 'reset');
const pendingReset = projectContext.workspaceCard(resetWorkspace);
assert.match(pendingReset, /Resetting…/);
assert.match(pendingReset, /aria-busy="true"/);
assert.doesNotMatch(pendingReset, /data-action|data-delete/);
projectContext.pending.delete('managed-demo');
const resetting = projectContext.workspaceCard({ ...resetWorkspace, operation: { busy: true, action: 'reset', stage: 'Restarting services' } });
assert.match(resetting, /Resetting…/);
assert.match(resetting, /Restarting services/);
assert.doesNotMatch(resetting, /data-action|data-delete/);
assert.match(html, /reset:'<path d="M21 3v6h-6/);

// Clicking reset immediately shows pending progress, posts the managed name, and follows the job.
const cardActionSource = html.match(/document\.querySelector\('#cards'\)\.addEventListener\('click',async e=>\{[^\n]*\}\);/)?.[0];
assert.ok(cardActionSource);
(async () => {
  let click, request, message = '', renders = 0, watched;
  const state = {
    latest: [resetWorkspace], csrf: 'reset-token', pending: new Map(),
    document: { querySelector() { return { addEventListener(event, handler) { click = handler; } }; } },
    render() { renders++; }, showMessage(text) { message = text; },
    window: { jobNames: new Map() }, watchJob(id) { watched = id; },
    async fetch(url, options) {
      request = { url, options };
      assert.equal(state.pending.get('managed-demo'), 'reset');
      assert.equal(message, 'Reset requested for managed-demo…');
      assert.equal(renders, 1);
      return { ok: true, json: async () => ({ job: 'reset-job' }) };
    },
  };
  vm.runInNewContext(projectHelpers.slice(0, 4).join('\n'), state);
  vm.runInNewContext(cardActionSource, state);
  await click({ target: { closest() { return { dataset: { key: 'managed-demo', action: 'reset' } }; } } });
  assert.equal(request.url, '/api/action');
  assert.equal(request.options.method, 'POST');
  assert.equal(request.options.headers['X-CSRF-Token'], 'reset-token');
  assert.deepEqual(JSON.parse(request.options.body), { action: 'reset', name: 'managed-demo' });
  assert.equal(state.window.jobNames.get('reset-job'), 'managed-demo');
  assert.equal(watched, 'reset-job');
})().catch(error => { console.error(error); process.exitCode = 1; });

// Appearance is a separate persisted setting; changing it must not save or discard directory edits.
const appearanceHelpers = [
  html.match(/function applyAppearance\(value\)\{[^\n]*\}/)?.[0],
  html.match(/function appearanceMessage\(text,error=false\)\{[^\n]*\}/)?.[0],
  html.match(/async function saveAppearance\(\)\{[\s\S]*?\n\}/)?.[0],
  html.match(/function directoryBusy\(busy\)\{[^\n]*\}/)?.[0],
];
assert.ok(appearanceHelpers.every(Boolean));
assert.match(html, /@media\(prefers-color-scheme:dark\)\{:root:not\(\[data-appearance\]\),:root\[data-appearance="system"\]/);
assert.match(html, /:root\[data-appearance="dark"\]\{color-scheme:dark/);
assert.match(html, /:root\{color-scheme:light/);
assert.match(html, /<label[^>]+for="appearance">Appearance<\/label>/);
assert.match(html, /document.documentElement.dataset.appearance\|\|'system'/);
assert.doesNotMatch(html.slice(html.indexOf('*{box-sizing'), html.indexOf('</style>')), /#[a-f0-9]{3,8}\b|rgba\(/i, 'component colors must use the theme palette');
assert.doesNotMatch(refreshSource, /applyAppearance/, 'snapshot polling must not reset the selected appearance');
(async () => {
  const messages = [], requests = [], inputs = [{ value: '/Unsaved/path' }];
  let release;
  const state = {
    appearance: 'system', appearanceSelect: { value: 'system' },
    appearanceStatus: { textContent: '', classList: { toggle(name, value) { this.error = value; } } },
    document: { documentElement: { dataset: {} } },
    window: {}, csrf: 'appearance-token', directoriesBusy: false, directoriesDirty: true,
    directoryAdd: {}, directoriesSave: {}, directoryBrowse: {}, showpassBrowse: {},
    showpassCheckout: { value: '/Unsaved/checkout' }, dockerSocket: { value: '/tmp/unsaved.sock' },
    directoryList: { querySelectorAll() { return inputs; } },
    nativePost(message) { messages.push(message); },
    async fetch(url, options) {
      requests.push({ url, options });
      await new Promise(resolve => { release = resolve; });
      return { ok: true, json: async () => ({ appearance: JSON.parse(options.body).appearance, csrf: 'saved-token' }) };
    },
  };
  vm.runInNewContext(appearanceHelpers.join('\n'), state);
  state.applyAppearance(undefined);
  assert.equal(state.document.documentElement.dataset.appearance, 'system');
  for (const mode of ['light', 'dark', 'system']) {
    state.applyAppearance(mode);
    assert.equal(state.document.documentElement.dataset.appearance, mode);
    assert.equal(state.appearanceSelect.value, mode);
    assert.equal(messages.at(-1).action, 'setAppearance');
    assert.equal(messages.at(-1).appearance, mode);
  }
  state.appearanceSelect.value = 'dark';
  const saving = state.saveAppearance();
  assert.equal(state.appearanceSelect.disabled, true);
  assert.equal(state.directoriesSave.disabled, true);
  assert.equal(inputs[0].disabled, true);
  assert.equal(state.appearanceStatus.textContent, 'Saving appearance…');
  assert.equal(state.document.documentElement.dataset.appearance, 'system', 'theme changes only after persistence succeeds');
  await state.saveAppearance();
  assert.equal(requests.length, 1, 'busy settings must not start another save');
  release(); await saving;
  assert.equal(requests[0].url, '/api/settings');
  assert.equal(requests[0].options.headers['X-CSRF-Token'], 'appearance-token');
  assert.deepEqual(JSON.parse(requests[0].options.body), { appearance: 'dark' });
  assert.equal(state.document.documentElement.dataset.appearance, 'dark');
  assert.equal(messages.at(-1).appearance, 'dark');
  assert.equal(state.csrf, 'saved-token');
  assert.equal(inputs[0].value, '/Unsaved/path');
  assert.equal(state.showpassCheckout.value, '/Unsaved/checkout');
  assert.equal(state.dockerSocket.value, '/tmp/unsaved.sock');
  assert.equal(state.directoriesDirty, true);
  assert.equal(state.directoriesSave.disabled, false);
  assert.equal(state.appearanceSelect.disabled, false);
  for (const fail of [async () => ({ ok: false, status: 400, json: async () => ({ error: 'Cannot save appearance.' }) }), async () => { throw Error('Connection lost.'); }]) {
    state.fetch = fail;
    state.appearanceSelect.value = 'light';
    await state.saveAppearance();
    assert.equal(state.appearanceSelect.value, 'dark');
    assert.equal(state.document.documentElement.dataset.appearance, 'dark');
    assert.equal(messages.at(-1).appearance, 'dark');
    assert.equal(state.appearanceStatus.classList.error, true);
    assert.equal(state.directoriesBusy, false);
    assert.equal(state.directoriesDirty, true);
  }
  // Loading persisted settings applies the saved preference and synchronizes the native window.
  state.directoryList.replaceChildren = () => {};
  state.addDirectory = () => {};
  state.document.querySelector = () => ({});
  vm.runInNewContext(directoryHelpers[0], state);
  state.applyDirectorySettings({ directories: [], appearance: 'light' });
  assert.equal(state.document.documentElement.dataset.appearance, 'light');
  assert.equal(messages.at(-1).appearance, 'light');
})().catch(error => { console.error(error); process.exitCode = 1; });
