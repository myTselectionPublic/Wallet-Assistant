const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const context = vm.createContext({
  window: {}, HTMLElement: class {},
  customElements: { get() { return true; } },
  alert(message) { throw new Error(message); },
});

test('image controls provide separate camera and device pickers', () => {
  const markup = context.imageUploadControls('add');
  assert.match(markup, /capture="environment"/);
  assert.match(markup, /id="add-image-file" type="file" accept="image\/jpeg,image\/png,image\/gif,image\/webp" hidden/);
  assert.match(markup, /Take photo/);
  assert.match(markup, /Choose photo/);
});

function imageUploadFixture() {
  const elements = new Map();
  const root = {
    querySelector(selector) {
      if (!elements.has(selector)) elements.set(selector, {
        value: '', disabled: false, hidden: true, listeners: {},
        addEventListener(name, callback) { this.listeners[name] = callback; },
        click() { this.clicked = true; },
      });
      return elements.get(selector);
    },
    querySelectorAll(selector) { return selector.split(', ').map(s => this.querySelector(s)); },
  };
  const card = Object.create(context.Card.prototype);
  card._inputState = { logo_slug: '' };
  card.dynamicContainer = { querySelector: () => root };
  card.bindImageUpload(root, 'add');
  return { card, root };
}

test('device uploads disable saving and put the returned local path into the card form', async () => {
  const { card, root } = imageUploadFixture();
  let finish;
  const file = { size: 200, type: 'image/png' };
  card._hass = { fetchWithAuth(url, options) {
    assert.equal(url, '/api/wallet_assistant/images/upload');
    assert.equal(options.method, 'POST');
    assert.equal(options.body, file);
    return new Promise(resolve => { finish = resolve; });
  } };
  root.querySelector('#add-take-photo').listeners.click();
  assert.equal(root.querySelector('#add-camera-file').clicked, true);
  const input = root.querySelector('#add-image-file');
  input.files = [file];
  const uploading = input.listeners.change();
  assert.equal(root.querySelector('#save-btn').disabled, true);
  finish({ ok: true, json: async () => ({ path: '/local/wallet_assistant/images/photo.png' }) });
  await uploading;
  assert.equal(root.querySelector('#save-btn').disabled, false);
  assert.equal(card._inputState.logo_slug, '/local/wallet_assistant/images/photo.png');
  assert.equal(root.querySelector('#add-logo-slug').value, card._inputState.logo_slug);
  assert.equal(root.querySelector('#add-image-preview').src, card._inputState.logo_slug);
  assert.match(root.querySelector('#add-image-status').textContent, /Save to link/);
});

test('upload errors keep the previous logo and allow retry', async () => {
  const { card, root } = imageUploadFixture();
  root.querySelector('#add-logo-slug').value = '/local/previous.png';
  card._hass = { fetchWithAuth: async () => ({ ok: false, json: async () => ({ error: 'Unsupported image' }) }) };
  const input = root.querySelector('#add-image-file');
  input.files = [{ size: 200 }];
  await input.listeners.change();
  assert.equal(root.querySelector('#add-logo-slug').value, '/local/previous.png');
  assert.equal(root.querySelector('#save-btn').disabled, false);
  assert.match(root.querySelector('#add-image-status').textContent, /Unsupported image/);
});
const source = fs.readFileSync(path.join(__dirname, '../src/walletassistant-main.js'), 'utf8')
  .replace(/^\uFEFF/, '')
  .replace(/^import .*;\r?\n/gm, '');
vm.runInContext(source + '\nglobalThis.Card = WalletAssistantCard;', context);

test('new cards allow empty and whitespace-only codes', async () => {
  for (const code of ['', '   ', '123456']) {
    const card = Object.create(context.Card.prototype);
    const fields = { 'add-name': { value: 'My card' }, 'add-code': { value: code } };
    const saveButton = { disabled: false };
    card.shadowRoot = {
      getElementById: id => fields[id],
      querySelector: selector => selector.includes('button') ? saveButton : null,
    };
    let saved;
    card._hass = {
      user: { id: 'owner', name: 'Owner' },
      async callApi(method, url, data) { saved = data; },
    };
    card.loadCards = async () => {};
    await card.addCard({ preventDefault() {} });
    assert.equal(saved.name, 'My card');
    assert.equal(saved.code, code.trim());
    assert.equal(card.showAddDialog, false);
    assert.equal(saveButton.disabled, false);
  }
});

test('card popup only shows and renders code controls when a code is set', () => {
  for (const code of ['', '   ', null, undefined, '123456']) {
    for (const mode of ['barcode', 'qr']) {
      const card = Object.create(context.Card.prototype);
      let rendered = 0;
      context.QRCode = { toCanvas() { rendered++; } };
      context.JsBarcode = () => { rendered++; };
      context.HTMLCanvasElement = class {};
      context.document = { createElement: () => ({}) };
      card.dynamicContainer = {
        innerHTML: '',
        querySelector(selector) {
          if (selector === '#code-preview' && this.innerHTML.includes('id="code-preview"')) {
            return { appendChild() {}, innerHTML: '' };
          }
          return null;
        },
        querySelectorAll: () => [],
      };
      card.shadowRoot = {};
      card.toolbarContainer = { querySelector: () => null };
      card.ownCards = [];
      card.otherCards = [];
      card.viewModes = { c1: mode };
      card.selectedCard = { item_id: 'c1', name: 'My card', code, user_id: 'owner' };
      card._hass = { user: { id: 'owner' } };
      card.render();
      const hasCode = Boolean(code?.trim());
      assert.equal(card.dynamicContainer.innerHTML.includes('id="toggle-code"'), hasCode);
      assert.equal(card.dynamicContainer.innerHTML.includes('id="code-preview"'), hasCode);
      assert.equal(rendered, hasCode ? 1 : 0);
      assert.match(card.dynamicContainer.innerHTML, /id="edit"/);
      assert.match(card.dynamicContainer.innerHTML, /id="close"/);
    }
  }
});

test('vouchers keep their badge while loyalty cards use their original plain label', () => {
  assert.match(context.typeBadge({ item_type: 'voucher' }), /ticket-confirmation-outline.*Voucher/);
  assert.equal(context.typeBadge({ item_type: 'loyalty' }), '<span>Card</span>');
});

test('voucher details show balance and escaped history, with controls only for managers', () => {
  const item = { item_type: 'voucher', initial_value: 25, remaining_balance: 15, currency: 'EUR',
    spending: [{ id: 's1', amount: 10, spent_on: '2026-11-11', note: '<script>bad()</script>' }] };
  const readonly = context.voucherDetails(item, false);
  assert.match(readonly, /Remaining balance/);
  assert.match(readonly, /&lt;script&gt;/);
  assert.doesNotMatch(readonly, /<script>|spending-form|data-remove-spending/);
  const editable = context.voucherDetails(item, true);
  assert.match(editable, /spending-form/);
  assert.match(editable, /data-remove-spending="s1"/);
  assert.doesNotMatch(context.voucherDetails({ ...item, remaining_balance: 0 }, true), /spending-form/);
  assert.equal(context.voucherDetails({ item_type: 'loyalty' }, true), '');
});

test('tracking is optional and hidden inputs are disabled for loyalty cards', () => {
  assert.match(context.voucherDetails({ item_type: 'voucher' }, true), /Balance tracking is off/);
  assert.match(context.voucherFields('add', { item_type: 'loyalty' }), /hidden/);
  assert.match(context.voucherFields('add', { item_type: 'loyalty' }), /disabled/);
  assert.doesNotMatch(context.voucherFields('add', { item_type: 'voucher' }), /disabled/);
});

test('recording spending uses the saved balance and suppresses duplicate submissions', async () => {
  const card = Object.create(context.Card.prototype);
  const previous = { item_id: 'v1', item_type: 'voucher', remaining_balance: 25 };
  let complete;
  let calls = 0;
  card._hass = { callApi(method, url, data) {
    calls++;
    assert.equal(method, 'post');
    assert.equal(url, 'wallet_assistant/v1');
    assert.equal(data.action, 'record_spending');
    return new Promise(resolve => { complete = resolve; });
  } };
  card.selectedCard = previous;
  card.ownCards = [previous];
  card.otherCards = [];
  card.render = () => {};
  const button = { disabled: false };
  const saving = card.saveSpending({ action: 'record_spending', amount: 10 }, button);
  assert.equal(button.disabled, true);
  await card.saveSpending({ action: 'record_spending', amount: 10 }, button);
  assert.equal(calls, 1);
  complete({ ...previous, remaining_balance: 15 });
  await saving;
  assert.equal(card.selectedCard.remaining_balance, 15);
  assert.equal(card.ownCards[0].remaining_balance, 15);
  assert.equal(button.disabled, false);
});
