// Exercise the real generic submission handler with deterministic transport outcomes.
const fs = require('node:fs');
const vm = require('node:vm');
const assert = require('node:assert/strict');
let submit;
let fetches = 0;
let finish;
const navigations = [];
const message = { hidden: true, focus() {}, scrollIntoView() {} };
global.document = {
  querySelector: () => null,
  querySelectorAll: () => [],
  getElementById: id => (id === 'global-message' ? message : null),
  body: { classList: { toggle() {}, contains() { return false; } } },
  addEventListener(name, handler) { if (name === 'submit') submit = handler; },
};
global.matchMedia = () => ({ matches: false, addEventListener() {} });
global.window = { confirm: () => true };
global.location = { href: 'http://local.invalid/edit', origin: 'http://local.invalid', assign(url) { navigations.push(url); } };
global.HTMLFormElement = class {};
global.FormData = class {};
const mode = process.argv[2];
const button = { disabled: false };
const form = Object.assign(new HTMLFormElement(), {
  dataset: {},
  action: { tagName: 'INPUT', name: 'action' },
  method: { tagName: 'INPUT', name: 'method' },
  attributes: { action: '/edit', method: 'POST' },
  setAttribute(name, value) { this.attributes[name] = value; },
  getAttribute(name) { return this.attributes[name] ?? null; },
  removeAttribute(name) { delete this.attributes[name]; },
  hasAttribute: () => true,
  checkValidity: () => true,
  querySelectorAll: () => [button],
});
const event = (target = form) => ({
  target,
  defaultPrevented: false,
  preventDefault() { this.defaultPrevented = true; },
});
const realTimeout = setTimeout;
if (mode === 'timeout') global.setTimeout = (fn, delay) => realTimeout(fn, delay === 30000 ? 1 : delay);
global.fetch = async (url, options) => {
  assert.equal(url, '/edit', 'Named fields cannot override the submission URL');
  assert.equal(options.method, 'POST', 'Named fields cannot override the HTTP method');
  assert.equal(options.headers['X-InventraIQ-Form'], '1');
  fetches++;
  if (mode === 'pending') return new Promise(resolve => { finish = resolve; });
  if (mode === 'network') throw Error('Disconnected');
  if (mode === 'timeout') {
    return new Promise((resolve, reject) => {
      options.signal.addEventListener('abort', () => reject(Error('Timeout')));
    });
  }
  if (mode === 'success' || mode === 'foreign-redirect') return {
    status: 200, ok: true, redirected: false,
    headers: { get: () => 'application/json' },
    json: async () => ({ redirect_to: mode === 'success' ? '/saved' : 'https://foreign.invalid/saved' }),
  };
  return {
    status: mode === 'validation' ? 409 : 500,
    redirected: false,
    ok: false,
    headers: { get: () => 'application/json' },
    json: async () => ({ error: 'Rejected' }),
  };
};
vm.runInThisContext(fs.readFileSync('static/js/workspace.js', 'utf8'));
(async () => {
  const first = submit(event());
  assert.equal(button.disabled, true);
  assert.equal(form.getAttribute('aria-busy'), 'true');
  if (mode === 'pending') {
    const duplicate = event();
    await submit(duplicate);
    assert.equal(duplicate.defaultPrevented, true);
    assert.equal(fetches, 1);
    finish({ status: 500, redirected: false });
  }
  await first;
  assert.equal(form.getAttribute('aria-busy'), null);
  if (mode === 'success') {
    assert.deepEqual(navigations, ['http://local.invalid/saved']);
    assert.equal(fetches, 1);
    assert.equal(message.hidden, true);
    return;
  }
  assert.deepEqual(navigations, []);
  assert.match(form.getAttribute('aria-describedby'), /global-message/);
  if (mode === 'validation') {
    assert.equal(button.disabled, false);
    assert.equal(message.textContent, 'Rejected');
    await submit(event());
    assert.equal(fetches, 2);
    const secondForm = Object.assign(new HTMLFormElement(), form, { attributes: { action: '/edit', method: 'POST' }, dataset: {} });
    await submit(event(secondForm));
    assert.equal(fetches, 3);
    assert.equal(form.getAttribute('aria-describedby'), null,
      'A shared error message must not describe an unrelated earlier form');
    assert.equal(secondForm.getAttribute('aria-describedby'), 'global-message');
  } else {
    assert.equal(button.disabled, true);
    assert.equal(form.dataset.submissionState, 'review-required');
    assert.match(message.textContent, /status is unknown/);
    assert.match(message.textContent, /before retrying/);
    await submit(event());
    assert.equal(fetches, 1);
  }
})().catch(error => { console.error(error); process.exitCode = 1; });
