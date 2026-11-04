const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const html = fs.readFileSync(new URL('../deck.html', `file://${__filename}`), 'utf8');
const script = html.match(/<script>([\s\S]*?)<\/script>/)[1];
new vm.Script(script);
function harness({ hash = '', stored = null, storageFails = false } = {}) {
  const elements = new Map(), calls = [], saved = [], historyCalls = [], timers = new Map();
  let timerId = 0;
  const decode = s => s.replace(/&quot;/g, '"').replace(/&#39;/g, "'").replace(/&lt;/g, '<').replace(/&gt;/g, '>').replace(/&amp;/g, '&');
  function element(selector) {
    if (!elements.has(selector)) elements.set(selector, {
      textContent: '', innerHTML: '', hidden: false, value: '', dataset: {}, handlers: {},
      classList: { toggle(name, value) { this[name] = value; } },
      addEventListener(name, fn) { this.handlers[name] = fn; },
      setAttribute(name, value) { this[name] = value; },
      focus() { state.document.activeElement = this; },
      querySelectorAll(selector) { if(selector==='[data-pin-id]')return [...this.innerHTML.matchAll(/<button[^>]*data-pin-id="([^"]*)"[^>]*>/g)].map(m=>({dataset:{pinId:decode(m[1])},disabled:m[0].includes('disabled'),focus(){state.document.activeElement=this}}));return [...this.innerHTML.matchAll(/<button[^>]*data-chat-id="([^"]*)"[^>]*>/g)].map(m => ({ dataset: { chatId: decode(m[1]) }, disabled: m[0].includes('disabled'), focus() { state.document.activeElement = this; } })); },
    });
    return elements.get(selector);
  }
  const state = {
    URLSearchParams, AbortController,
    location: { hash, pathname: '/deck', search: '' },
    history: { replaceState(...args) { historyCalls.push(args); state.location.hash = ''; } },
    localStorage: { getItem(key) { assert.equal(key, 'deck-mode'); if (storageFails) throw Error('Storage unavailable'); return stored; }, setItem(key, value) { if (storageFails) throw Error('Storage unavailable'); saved.push({ key, value }); } },
    window: { handlers: {}, addEventListener(name, fn) { this.handlers[name] = fn; } },
    document: { activeElement: null, documentElement: { dataset: {} }, querySelector: element },
    setTimeout(fn, ms) { const id = ++timerId; timers.set(id, { fn, ms, interval: false }); return id; },
    clearTimeout(id) { timers.delete(id); },
    setInterval(fn, ms) { const id = ++timerId; timers.set(id, { fn, ms, interval: true }); return id; },
    clearInterval(id) { timers.delete(id); },
    async fetch(url, options) { calls.push({ url, options }); return state.respond(url, options); },
    async respond() { return { ok: true, status: 200, json: async () => ({ status: 'available', csrf: 'status-token', all_chats: [] }) }; },
  };
  vm.createContext(state); vm.runInContext(script.replace(/\nboot\(\);\s*$/, ''), state);
  return { state, calls, saved, historyCalls, timers, element, run: code => vm.runInContext(code, state) };
}
const chats = [
  { id: 'working', name: 'Build', workspace: 'Beta', agent: 'codex', state: 'working', unread: false },
  { id: 'ready', name: 'Done', workspace: 'Alpha', agent: 'claude', state: 'ready', unread: true },
  { id: 'blocked', name: 'Question', workspace: 'Beta', agent: 'codex', state: 'blocked', unread: false },
  { id: 'idle', name: 'Waiting', workspace: 'Gamma', agent: '', state: 'idle', unread: false },
  { id: 'unknown', name: 'Mystery', workspace: 'Delta', agent: '', state: 'unknown', unread: false },
  { id: 'viewed', name: 'Read', workspace: 'Alpha', agent: 'codex', state: 'ready', unread: false },
];
function seed(h, rows = chats) { h.state.rows = rows.map(c => ({...c})); h.run("chats=rows;available=true;csrf='token';render()"); }
function ids(h) { return JSON.parse(JSON.stringify(h.run('visibleChats().map(c=>c.id)'))); }
function response(data, status = 200) { return { ok: status >= 200 && status < 300, status, json: async () => data }; }
(async () => {
  const h = harness(); seed(h);
  assert.deepEqual(ids(h), ['blocked', 'ready', 'viewed']);
  assert.equal(h.element('#summary').textContent, '3 chats');
  for (const [mode, expected] of Object.entries({ unread: ['ready'], ready: ['ready', 'viewed'], input: ['blocked'], pinned: [], all: ['blocked', 'ready', 'viewed', 'working', 'idle', 'unknown'] })) {
    h.element('#mode').value = mode; h.element('#mode').handlers.change();
    assert.deepEqual(ids(h), expected);
    assert.deepEqual(h.saved.at(-1), { key: 'deck-mode', value: mode });
  }
  const pins = harness({stored:'all'}); seed(pins);
  pins.state.respond=async(url,options)=>{
    assert.equal(url,'/deck/api/pin');assert.deepEqual(JSON.parse(options.body),{id:'idle',pinned:true});assert.equal(options.headers['X-CSRF-Token'],'token');return response({pinned:true});
  };
  await pins.state.pinChat('idle');
  assert.deepEqual(ids(pins),['idle','blocked','ready','viewed','working','unknown']);
  assert.equal(pins.calls.length,1,'Pinning must never focus a chat');
  pins.element('#mode').value='pinned';pins.element('#mode').handlers.change();assert.deepEqual(ids(pins),['idle']);
  pins.state.respond=async()=>response({error:'Save failed'},400);await pins.state.pinChat('idle');assert.deepEqual(ids(pins),['idle']);
  pins.state.respond=async()=>response({pinned:false});await pins.state.pinChat('idle');assert.deepEqual(ids(pins),[]);
  const pinRace=harness({stored:'all'});seed(pinRace);let releaseStatus;
  pinRace.state.respond=(url)=>url==='/deck/api/status'?new Promise(resolve=>{releaseStatus=resolve}):Promise.resolve(response({pinned:true}));
  const oldStatus=pinRace.state.refresh();await pinRace.state.pinChat('idle');
  releaseStatus(response({status:'available',all_chats:chats,csrf:'token'}));await oldStatus;
  assert.equal(ids(pinRace)[0],'idle','An older poll must not overwrite a newly saved pin');
  const pinErrorRace=harness({stored:'all'});seed(pinErrorRace);let rejectStatus;
  pinErrorRace.state.respond=(url)=>url==='/deck/api/status'?new Promise((resolve,reject)=>{rejectStatus=reject}):Promise.resolve(response({pinned:true}));
  const failedOldStatus=pinErrorRace.state.refresh();await pinErrorRace.state.pinChat('idle');
  rejectStatus(Error('Old request timed out'));await failedOldStatus;
  assert.equal(pinErrorRace.run('available'),true);assert.equal(ids(pinErrorRace)[0],'idle');
  const pinExpired=harness();seed(pinExpired);pinExpired.state.respond=async()=>response({},401);await pinExpired.state.pinChat('ready');assert.equal(pinExpired.run('authRequired'),true);assert.equal(pinExpired.element('#grid').innerHTML,'');
  assert.equal(harness({ stored: 'bad' }).run('mode'), 'attention');
  assert.equal(harness({ stored: 'all' }).run('mode'), 'all');
  const denied = harness({ storageFails: true }); denied.element('#mode').value = 'all'; denied.element('#mode').handlers.change(); assert.equal(denied.run('mode'), 'all');
  for (const [state, label] of Object.entries({ blocked: 'Needs input', ready: 'Ready for reply', working: 'Working', idle: 'Idle', unknown: 'Unknown' })) {
    const markup = h.state.chatButton({ id: state, state });
    assert.match(markup, /<svg/); assert.ok(markup.includes(label)); assert.match(markup, /aria-hidden="true"/);
  }
  assert.match(h.state.chatButton({ state: 'working' }), /class="spinner"/);
  assert.match(h.state.chatButton({ state: '<script>' }), /Unknown/);
  const escaped = h.state.chatButton({ id: '" onclick="bad', state: 'ready', workspace: '<img>', name: '<script>', agent: '&agent', unread: true });
  assert.doesNotMatch(escaped, /<img>|<script>|" onclick="bad/);
  assert.match(escaped, /&lt;img&gt;/); assert.match(escaped, /&lt;script&gt;/); assert.match(escaped, /&amp;agent/); assert.match(escaped, / · Unread/);

  // Pair token is removed before any request and never enters browser storage.
  const pair = harness({ hash: '#pair=one-time%2Bsecret' });
  pair.state.respond = async (url, options) => {
    assert.equal(pair.state.location.hash, '');
    assert.equal(pair.historyCalls.length, 1);
    if (url === '/deck/api/pair') { assert.deepEqual(JSON.parse(options.body), { token: 'one-time+secret' }); return response({ csrf: 'paired-token' }); }
    return response({ status: 'available', all_chats: chats, csrf: 'fresh-token', appearance: 'dark' });
  };
  await pair.state.boot();
  assert.deepEqual(pair.calls.map(c => c.url), ['/deck/api/pair', '/deck/api/status']);
  assert.deepEqual(pair.historyCalls[0], [null, '', '/deck']);
  assert.deepEqual(pair.saved, []);
  assert.equal(pair.state.document.documentElement.dataset.appearance, 'dark');
  assert.equal([...pair.timers.values()].filter(t => t.interval && t.ms === 5000).length, 1);
  assert.ok(pair.calls.every(c => c.options.cache === 'no-store' && c.options.credentials === 'same-origin'));
  const badPair = harness({ hash: '#pair=expired' }); badPair.state.respond = async () => response({ error: 'Do not show secret expired' }, 401);
  await badPair.state.boot();
  assert.equal(badPair.calls.length, 1, 'pairing is never silently retried');
  assert.equal(badPair.element('#grid').innerHTML, ''); assert.match(badPair.element('#empty').textContent, /Phone access/);
  assert.doesNotMatch(badPair.element('#message').textContent, /secret/); assert.equal(badPair.timers.size, 0);

  // Existing cookie needs no pairing request; 401 clears tiles and stops future polling.
  const auth = harness(); await auth.state.boot(); seed(auth);
  assert.equal(auth.calls[0].url, '/deck/api/status');
  auth.state.respond = async () => response({}, 401); await auth.state.refresh();
  assert.equal(auth.element('#grid').innerHTML, ''); assert.equal(auth.run('authRequired'), true);
  assert.equal(auth.timers.size, 0); const count = auth.calls.length; await auth.state.refresh(); assert.equal(auth.calls.length, count);

  // HTTP/network/unavailable/malformed snapshots clear stale actions without claiming zero chats.
  for (const reply of [() => response({ error: 'Server failed' }, 503), () => { throw Error('Offline'); }, () => response({ status: 'unavailable', message: 'Herdr not running' }), () => response({ status: 'available' })]) {
    const stale = harness(); seed(stale); stale.state.respond = reply; await stale.state.refresh();
    assert.equal(stale.element('#grid').innerHTML, ''); assert.equal(stale.run('available'), false);
    assert.equal(stale.element('#summary').textContent, 'Connection unavailable');
    assert.doesNotMatch(stale.element('#empty').textContent, /No chats/);
  }
  const timed = harness(); seed(timed);
  timed.state.respond = (url, options) => new Promise((resolve, reject) => options.signal.addEventListener('abort', () => reject(Error('Aborted'))));
  const timedRefresh = timed.state.refresh();
  const deadline = [...timed.timers.values()].find(timer => timer.ms === 8000);
  assert.ok(deadline); deadline.fn(); await timedRefresh;
  assert.equal(timed.element('#grid').innerHTML, ''); assert.equal(timed.run('refreshing'), false);
  const fallback = harness(); fallback.state.respond = async () => response({ status: 'available', chats, appearance: 'light' }); await fallback.state.refresh();
  assert.equal(fallback.run('chats.length'), chats.length); assert.equal(fallback.state.document.documentElement.dataset.appearance, 'light');
  let release;
  const slow = harness(); slow.state.respond = () => new Promise(resolve => { release = resolve; });
  const loading = slow.state.refresh(); await slow.state.refresh(); assert.equal(slow.calls.length, 1);
  release(response({ status: 'available', all_chats: [] })); await loading;
  assert.equal(slow.run('refreshing'), false);

  // A focus mutation has exact payload + CSRF and locks all buttons until its result.
  const focus = harness(); seed(focus); focus.state.respond = () => new Promise(resolve => { release = resolve; });
  const selecting = focus.state.focusChat('blocked');
  assert.equal(focus.calls[0].url, '/deck/api/focus');
  assert.equal(focus.calls[0].options.headers['X-CSRF-Token'], 'token');
  assert.deepEqual(JSON.parse(focus.calls[0].options.body), { id: 'blocked' });
  assert.ok(focus.element('#grid').querySelectorAll().every(b => b.disabled));
  await focus.state.focusChat('ready'); await focus.state.refresh(); assert.equal(focus.calls.length, 1);
  release(response({ message: 'Selected on Mac' })); await selecting;
  assert.equal(focus.run('focusing'), false); assert.equal(focus.element('#message').textContent, 'Selected on Mac');
  assert.equal(focus.state.document.activeElement.dataset.chatId, 'blocked');
  assert.ok(focus.element('#grid').querySelectorAll().every(b => !b.disabled));
  const before = focus.calls.length; await focus.state.focusChat('missing'); assert.equal(focus.calls.length, before);
  for (const status of [401, 409, 'network']) {
    const fail = harness(); seed(fail);
    fail.state.respond = async () => { if (status === 'network') throw Error('Lost response'); return response({ error: 'Chat no longer exists.' }, status); };
    await fail.state.focusChat('ready');
    assert.equal(fail.calls.length, 1); assert.equal(fail.run('focusing'), false);
    assert.equal(fail.element('#message').classList.error, true);
    if (status === 401) { assert.equal(fail.element('#grid').innerHTML, ''); assert.equal(fail.run('authRequired'), true); }
    if (status === 'network') assert.match(fail.element('#message').textContent, /Check your Mac before trying again/);
  }
  focus.state.window.handlers.pagehide(); assert.equal(focus.element('#grid').innerHTML, '');
  assert.match(html, /viewport-fit=cover/); assert.doesNotMatch(html, /user-scalable=no|maximum-scale|serviceWorker|caches\./);
  assert.match(html, /apple-mobile-web-app-capable/); assert.match(html, /orientation:landscape/);
  console.log('Deck UI tests passed');
})().catch(error => { console.error(error); process.exitCode = 1; });
