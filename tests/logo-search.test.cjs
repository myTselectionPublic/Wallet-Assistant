const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const source = fs.readFileSync(path.join(__dirname, '../src/walletassistant-main.js'), 'utf8')
  .replace(/^\uFEFF/, '').replace(/^import .*;\r?\n/gm, '');

function fixture() {
  const timers = new Map();
  let nextId = 0;
  const context = vm.createContext({
    window: {}, HTMLElement: class {}, Event: class { constructor(type) { this.type = type; } },
    customElements: { get() { return true; } },
    setTimeout(fn) { const id = ++nextId; timers.set(id, fn); return id; },
    clearTimeout(id) { timers.delete(id); },
  });
  vm.runInContext(source + '\nglobalThis.Card = WalletAssistantCard;', context);
  class Element {
    constructor() { this.listeners = {}; this.value = ''; this.isConnected = true; this.buttons = []; this.hidden = true; }
    addEventListener(type, fn) { (this.listeners[type] ||= []).push(fn); }
    dispatchEvent(event) { for (const fn of this.listeners[event.type] || []) fn(event); }
    setAttribute() {}
    focus() { this.focused = true; }
    set innerHTML(html) {
      this.html = html;
      this.buttons = [...html.matchAll(/data-domain="([^"]+)"/g)].map(match => {
        const button = new Element(); button.dataset = { domain: match[1] }; return button;
      });
    }
    querySelectorAll(type) { return type === 'button' ? this.buttons : []; }
    querySelector() { return this.buttons[0]; }
  }
  const elements = new Map();
  const root = { querySelector(selector) {
    if (!elements.has(selector)) elements.set(selector, new Element());
    return elements.get(selector);
  } };
  const card = Object.create(context.Card.prototype);
  const calls = [];
  let response = { status: 'ok', results: [{ name: '<IKEA>', domain: 'ikea.com' }] };
  card._hass = { callApi: async (method, url) => { calls.push(url); return typeof response === 'function' ? response() : response; } };
  const input = root.querySelector('#add-logo-slug');
  let savedValue = '';
  input.addEventListener('input', () => { savedValue = input.value; });
  card.bindLogoSearch(root, 'add');
  return {
    input, calls, timers, root, context, card,
    results: root.querySelector('#add-logo-results'),
    status: root.querySelector('#add-logo-search-status'),
    type(value) { input.value = value; input.dispatchEvent({ type: 'input' }); },
    response(value) { response = value; },
    savedValue: () => savedValue,
    async flush() { const pending = [...timers.values()]; timers.clear(); await Promise.all(pending.map(fn => fn())); },
  };
}

test('search debounces partial input and selection fills the saved domain safely', async () => {
  const f = fixture();
  f.type('ik'); await f.flush(); assert.equal(f.calls.length, 0);
  f.type('ike'); f.type('ikea');
  assert.equal(f.timers.size, 1);
  await f.flush();
  assert.equal(f.calls[0], 'wallet_assistant/logo_search?q=ikea');
  assert.equal(f.results.hidden, false);
  assert.match(f.results.html, /&lt;IKEA&gt;/);
  f.results.buttons[0].dispatchEvent({ type: 'click' });
  assert.equal(f.input.value, 'ikea.com');
  assert.equal(f.savedValue(), 'ikea.com');
  assert.equal(f.results.hidden, true);
  assert.equal(f.timers.size, 0);
});

test('late responses cannot replace newer input, local paths, or a closed form', async () => {
  for (const nextValue of ['new brand', '/local/image.png', 'https://example.com/image.png']) {
    const f = fixture();
    let finish;
    f.response(() => new Promise(resolve => { finish = resolve; }));
    f.type('ike'); const pending = f.flush();
    f.type(nextValue);
    finish({ status: 'ok', results: [{ name: 'Old', domain: 'old.com' }] });
    await pending;
    assert.equal(f.results.hidden, true);
    assert.equal(f.input.value, nextValue);
  }
  const f = fixture();
  f.type('ike'); f.input.isConnected = false; await f.flush();
  assert.equal(f.calls.length, 0);
});

test('free quota exhaustion preserves manual entry and Escape dismisses results', async () => {
  const f = fixture();
  f.response({ status: 'credits_exhausted', results: [] });
  f.type('ike'); await f.flush();
  assert.match(f.status.textContent, /credits are used up/);
  assert.equal(f.input.value, 'ike');
  f.response({ status: 'ok', results: [{ name: 'IKEA', domain: 'ikea.com' }] });
  f.type('ikea'); await f.flush();
  let prevented = false;
  f.input.dispatchEvent({ type: 'keydown', key: 'Escape', preventDefault() { prevented = true; }, stopPropagation() {} });
  assert.equal(prevented, true);
  assert.equal(f.results.hidden, true);
});
