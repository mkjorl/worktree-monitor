const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const html = fs.readFileSync(new URL('../index.html', `file://${__filename}`), 'utf8');
const source = html.slice(html.indexOf('// Update settings are available'), html.indexOf("const launchToggle=document.querySelector('#launch-toggle')"));
assert.ok(source.includes('checkForUpdates'));
function harness(native) {
  const handlers = {}, sent = [], elements = Object.fromEntries(['#updates-check', '#updates-auto', '#updates-status'].map(id => [id, { disabled: true, checked: false, textContent: 'Available in the Mac app.', handlers: {}, addEventListener(type, callback) { this.handlers[type] = callback; } }]));
  const context = {
    window: { addEventListener(type, callback) { handlers[type] = callback; }, ...(native ? { webkit: { messageHandlers: { app: {} } } } : {}) },
    document: { querySelector(id) { assert.ok(elements[id]); return elements[id]; } },
    nativePost(message) { sent.push(JSON.parse(JSON.stringify(message))); },
  };
  vm.runInNewContext(source, context);
  return { elements, sent, settings(detail) { handlers['native-settings']({ detail }); } };
}
const browser = harness(false);
browser.settings({ available: true, updateAvailable: true, canCheckForUpdates: true, automaticUpdateChecks: true });
assert.equal(browser.elements['#updates-check'].disabled, true);
assert.equal(browser.elements['#updates-auto'].disabled, true);
assert.equal(browser.elements['#updates-auto'].checked, false);
assert.equal(browser.elements['#updates-status'].textContent, 'Available in the Mac app.');
browser.elements['#updates-check'].handlers.click(); browser.elements['#updates-auto'].handlers.change();
assert.deepEqual(browser.sent, []);
const native = harness(true), check = native.elements['#updates-check'], auto = native.elements['#updates-auto'], status = native.elements['#updates-status'];
native.settings({ available: true });
assert.equal(check.disabled, true, 'Mac availability alone does not imply an initialized updater');
native.settings({ updateAvailable: false, updatesMessage: 'Updater configuration is invalid.' });
assert.equal(status.textContent, 'Updater configuration is invalid.');
native.settings({ updateAvailable: true, canCheckForUpdates: true, automaticUpdateChecks: false, updatesMessage: 'Check manually.' });
assert.equal(check.disabled, false); assert.equal(auto.disabled, false); assert.equal(auto.checked, false);
check.handlers.click(); check.handlers.click();
assert.deepEqual(native.sent, [{ action: 'checkForUpdates' }]);
assert.equal(check.disabled, true); assert.equal(status.textContent, 'Checking for updates…');
native.settings({ updateAvailable: true, canCheckForUpdates: false, automaticUpdateChecks: false, updatesMessage: 'Waiting for local operations to finish.' });
assert.equal(check.disabled, true); assert.equal(status.textContent, 'Waiting for local operations to finish.');
check.handlers.click(); assert.equal(native.sent.length, 1);
native.settings({ updateAvailable: true, canCheckForUpdates: true, automaticUpdateChecks: false });
assert.equal(check.disabled, false, 'canCheckForUpdates changes restore the manual button');
auto.checked = true; auto.handlers.change();
assert.equal(auto.disabled, true);
assert.deepEqual(native.sent.at(-1), { action: 'setAutomaticUpdateChecks', enabled: true });
native.settings({ updateAvailable: true, canCheckForUpdates: true, automaticUpdateChecks: true });
assert.equal(auto.checked, true); assert.equal(auto.disabled, false);
auto.checked = false; auto.handlers.change();
assert.deepEqual(native.sent.at(-1), { action: 'setAutomaticUpdateChecks', enabled: false });
assert.match(html, /id="updates-auto"[^>]*disabled[^>]*aria-label="Check for app updates automatically"/);
assert.match(html, /id="updates-status"[^>]*role="status"[^>]*aria-live="polite"/);
assert.match(html, /Releases from mkjorl\/worktree-monitor on GitHub/);
console.log('Update settings UI tests passed');
