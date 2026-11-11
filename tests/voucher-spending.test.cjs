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
const source = fs.readFileSync(path.join(__dirname, '../src/walletassistant-main.js'), 'utf8')
  .replace(/^\uFEFF/, '')
  .replace(/^import .*;\r?\n/gm, '');
vm.runInContext(source + '\nglobalThis.Card = WalletAssistantCard;', context);

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
