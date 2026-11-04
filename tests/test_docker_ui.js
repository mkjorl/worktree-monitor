const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const html = fs.readFileSync(new URL('../index.html', `file://${__filename}`), 'utf8');
const source = html.slice(html.indexOf('// Docker controls'), html.indexOf('function setSettingsView'));
const watchSource = html.match(/async function watchJob\(id,report=showMessage,complete=\(\)=>\{\}\)\{[^\n]*\}/)[0];
assert.doesNotMatch(html, /No containers, images, or volumes will be deleted/);
assert.match(html, /Stopping containers created with auto-remove can remove them and their anonymous volumes/);
const id = 'a'.repeat(64);
const snapshot = () => ({ status: 'available', desktop: { state: 'running', actions: ['stop', 'restart'] }, containers: [{ id, name: 'postgres', image: 'postgres:16', state: 'running', status_text: 'Up 1 hour', cpu: '0.1%', memory: '100 MiB', actions: ['stop', 'restart', 'pause'] }], operation: { busy: false } });
function harness() {
  const elements = new Map(), requests = [], native = [];
  function element(selector) {
    if (!elements.has(selector)) elements.set(selector, {
      innerHTML: '', textContent: '', hidden: false, disabled: false, open: false, handlers: {}, dataset: {},
      classList: { toggle(name, value) { this[name] = value; } },
      addEventListener(event, handler) { this.handlers[event] = handler; },
      focus() { state.document.activeElement = this; },
      showModal() { this.open = true; },
      close() { this.open = false; this.handlers.close?.(); },
    });
    return elements.get(selector);
  }
  const state = {
    window: { handlers: {}, addEventListener(event, handler) { this.handlers[event] = handler; } }, jobs: new Set(), csrf: 'docker-token', pending: new Map(),
    document: { activeElement: null, querySelector: element, querySelectorAll() { return state.buttons; } }, buttons: [],
    esc: value => String(value ?? '—').replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c])),
    icon: () => '<svg></svg>', nativePost(message) { native.push(message); },
    async refresh() { state.refreshes++; }, refreshes: 0,
    showMessage() { throw Error('Docker results must remain in Infrastructure.'); },
    watchJob(job, report, complete) { state.jobs.add(job); state.watched = { job, report, complete }; },
    async fetch(url, options) { requests.push({ url, options }); return { ok: true, json: async () => ({ job: 'docker-job' }) }; },
  };
  state.window.jobNames = new Map();
  vm.createContext(state); vm.runInContext(source, state);
  const run = code => vm.runInContext(code, state);
  function load(data) { state.snapshot = data; run('latestDocker=snapshot;renderDocker()'); }
  return { state, run, load, element, requests, native };
}
(async () => {
  const h = harness(); h.load(snapshot());
  assert.match(h.element('#docker-desktop-actions').innerHTML, />Stop Docker Desktop</);
  assert.match(h.element('#docker-desktop-actions').innerHTML, />Restart Docker Desktop</);
  assert.doesNotMatch(h.element('#docker-desktop-actions').innerHTML, />Start Docker Desktop</);
  assert.match(h.element('#containers').innerHTML, /data-docker-action="pause"/);
  assert.doesNotMatch(h.element('#containers').innerHTML, /data-docker-action="unpause"/);
  const paused = snapshot(); paused.containers[0].state = 'paused'; paused.containers[0].actions = ['unpause'];
  h.load(paused);
  assert.match(h.element('#containers').innerHTML, /aria-label="Resume postgres"/);
  assert.doesNotMatch(h.element('#containers').innerHTML, /data-docker-action="pause"/);
  h.load({ status: 'unavailable', desktop: { state: 'stopped', actions: ['start'] }, containers: [] });
  assert.match(h.element('#docker-desktop-actions').innerHTML, />Start Docker Desktop</);
  assert.equal(h.element('#docker-open').hidden, true, 'browser must not offer a native-only control');
  assert.equal(h.element('#docker-guidance').hidden, false);
  h.state.window.webkit = { messageHandlers: { app: {} } }; h.state.renderDocker();
  assert.equal(h.element('#docker-open').hidden, false);
  h.element('#docker-open').handlers.click(); assert.equal(h.native[0].action, 'openDocker');
  h.state.window.handlers['native-docker-error']({ detail: 'Docker Desktop is not installed.' });
  assert.equal(h.element('#docker-message').textContent, 'Docker Desktop is not installed.');
  assert.equal(h.element('#docker-message').classList.error, true);
  h.state.window.handlers['native-docker-error']({});
  assert.equal(h.element('#docker-message').textContent, 'Could not open Docker Desktop.');
  h.load({ status: 'unknown', desktop: { state: 'unknown', actions: [] } });
  assert.equal(h.element('#docker-desktop-actions').innerHTML, '');
  const customEngine = snapshot(); customEngine.desktop = { state: 'unknown', message: 'Desktop CLI unavailable.', actions: [] };
  h.load(customEngine);
  assert.match(h.element('#docker-state').textContent, /Docker Desktop status is unknown/);
  assert.match(h.element('#docker-state').textContent, /Connected to local Docker engine/);
  assert.doesNotMatch(h.element('#docker-state').textContent, /Desktop is running/);
  assert.equal(h.element('#docker-open').hidden, false, 'native Docker help remains available when the engine works but Desktop status is unknown');
  customEngine.status = 'unavailable'; customEngine.message = 'Local socket cannot be reached.';
  h.load(customEngine);
  assert.match(h.element('#docker-state').textContent, /Desktop CLI unavailable/);
  assert.match(h.element('#docker-state').textContent, /Local socket cannot be reached/);
  customEngine.desktop = { state: 'stopped', actions: ['start'] }; h.load(customEngine);
  assert.match(h.element('#docker-state').textContent, /Docker Desktop is stopped/);
  customEngine.desktop = { state: 'unknown', actions: ['start'] }; h.load(customEngine);
  assert.match(h.element('#docker-desktop-actions').innerHTML, />Start Docker Desktop</, 'unknown Desktop state may still advertise a safe start action');
  const malicious = snapshot(); Object.assign(malicious.containers[0], { name: '<img onerror="bad">', image: '<script>bad</script>', status_text: '<b>bad</b>', actions: ['stop', 'destroy', '<script>'] });
  h.load(malicious);
  assert.match(h.element('#containers').innerHTML, /&lt;img onerror=&quot;bad&quot;&gt;/);
  assert.doesNotMatch(h.element('#containers').innerHTML, /<img|<script|data-docker-action="destroy"/);

  // Cancel never submits; the current equivalent button receives focus after a polling render.
  h.load(snapshot());
  const replacement = { dataset: { dockerScope: 'desktop', dockerAction: 'stop' }, focus() { this.focused = true; } };
  h.state.buttons = [replacement];
  h.state.requestDockerAction({ scope: 'desktop', action: 'stop' });
  assert.equal(h.element('#docker-dialog').open, true);
  assert.equal(h.element('#docker-auto-remove').hidden, false);
  assert.match(h.element('#docker-warning').textContent, /all containers in Docker Desktop/);
  assert.equal(h.requests.length, 0);
  h.element('#docker-cancel').handlers.click();
  assert.equal(h.run('dockerTarget'), null);
  assert.equal(h.requests.length, 0);
  assert.equal(replacement.focused, true);
  h.state.requestDockerAction({ scope: 'container', action: 'pause', id });
  assert.match(h.element('#docker-warning').textContent, /shared service/);
  assert.equal(h.element('#docker-auto-remove').hidden, true);
  h.element('#docker-cancel').handlers.click();

  // A confirmed action is revalidated against the latest snapshot before submission.
  h.state.requestDockerAction({ scope: 'desktop', action: 'stop' });
  h.load({ status: 'unavailable', desktop: { actions: [] } });
  await h.state.confirmDockerAction({ preventDefault() {} });
  assert.equal(h.requests.length, 0);
  assert.match(h.element('#docker-message').textContent, /status changed/);

  // POST uses exact scope/id/action and CSRF, then locks all Docker controls while tracking.
  h.load(snapshot());
  h.state.requestDockerAction({ scope: 'container', action: 'restart', id, name: 'ignored' });
  await h.state.confirmDockerAction({ preventDefault() {} });
  assert.equal(h.requests[0].url, '/api/docker/action');
  assert.equal(h.requests[0].options.method, 'POST');
  assert.equal(h.requests[0].options.headers['X-CSRF-Token'], 'docker-token');
  assert.deepEqual(JSON.parse(h.requests[0].options.body), { scope: 'container', action: 'restart', id });
  assert.equal(h.state.watched.job, 'docker-job');
  assert.match(h.element('#docker-desktop-actions').innerHTML, /disabled/);
  assert.ok([...h.element('#containers').innerHTML.matchAll(/<button[^>]+>/g)].every(match => match[0].includes('disabled')));
  const count = h.requests.length;
  await h.state.runDockerAction({ scope: 'desktop', action: 'stop' });
  assert.equal(h.requests.length, count);
  h.state.watched.report('Docker restarted.');
  assert.equal(h.element('#docker-message').textContent, 'Docker restarted.');
  h.state.jobs.clear(); h.state.watched.complete();
  assert.equal(h.run('dockerBusy()'), false);

  // Start/resume bypass confirmation; failed POST and transport failures release the lock.
  for (const failure of [async () => ({ ok: false, status: 409, json: async () => ({ error: 'Container changed.' }) }), async () => { throw Error('Offline'); }]) {
    const f = harness(); f.load({ status: 'unavailable', desktop: { actions: ['start'] } });
    f.state.fetch = failure;
    await f.state.requestDockerAction({ scope: 'desktop', action: 'start' });
    assert.equal(f.element('#docker-dialog').open, false);
    assert.equal(f.run('dockerBusy()'), false);
    assert.equal(f.element('#docker-message').classList.error, true);
    assert.equal(f.state.refreshes, 1);
  }
  const resume = harness(); resume.load(paused);
  await resume.state.requestDockerAction({ scope: 'container', action: 'unpause', id });
  assert.equal(resume.element('#docker-dialog').open, false);
  assert.deepEqual(JSON.parse(resume.requests[0].options.body), { scope: 'container', action: 'unpause', id });
  const start = harness(); start.load({ status: 'unavailable', desktop: { actions: ['start'] } });
  await start.state.requestDockerAction({ scope: 'desktop', action: 'start' });
  assert.deepEqual(JSON.parse(start.requests[0].options.body), { scope: 'desktop', action: 'start' });

  // Reload resumes an active job once, and busy snapshots disable every control.
  const reload = harness(), active = snapshot(); active.operation = { busy: true, job: 'restored-job', stage: 'Restarting Docker Desktop' };
  reload.load(active); assert.equal(reload.state.watched.job, 'restored-job');
  assert.match(reload.element('#docker-progress').innerHTML, /Restarting Docker Desktop/);
  let duplicate = false; reload.state.watchJob = () => { duplicate = true; };
  reload.state.renderDocker(); assert.equal(duplicate, false);
  assert.equal(reload.element('#docker-open').disabled, true);

  // Real shared polling routes Docker output, cleans pending jobs, refreshes, and tolerates failure.
  for (const ending of ['done', 'error', 'unknown', 'http-error']) {
    const poll = harness();
    vm.runInContext(watchSource, poll.state);
    poll.state.setTimeout = callback => callback();
    let reads = 0;
    poll.state.fetch = async () => ({ ok: ending !== 'http-error', status: 404, json: async () => ({ state: ++reads === 1 ? 'running' : ending, message: 'Docker result', error: 'Job unavailable' }) });
    await poll.state.watchDockerJob('polled-job');
    assert.equal(poll.state.jobs.size, 0);
    assert.equal(poll.run('dockerPending'), false);
    assert.equal(poll.element('#docker-message').classList.error, ending !== 'done');
    assert.ok(poll.state.refreshes >= 1);
    assert.equal(poll.state.window.jobNames.size, 0);
  }
  console.log('Docker UI tests passed');
})().catch(error => { console.error(error); process.exitCode = 1; });
