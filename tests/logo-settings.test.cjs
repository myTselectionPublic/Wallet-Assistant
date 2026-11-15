const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const source = fs.readFileSync(path.join(__dirname, '../src/walletassistant-main.js'), 'utf8')
  .replace(/^\uFEFF/, '')
  .replace(/^import .*;\r?\n/gm, '');

test('settings select the custom browser fallback key and clearing restores the default', async () => {
  const context = vm.createContext({
    window: {}, HTMLElement: class {},
    customElements: { get() { return true; } },
    Image: class { set src(value) { this.onload(); } },
  });
  vm.runInContext(source + '\nglobalThis.Card = WalletAssistantCard;', context);
  const card = Object.create(context.Card.prototype);
  card.render = () => {};
  const defaultUrl = context.getLogoDevUrl('google.com');
  let settings = { logo_dev_api_key: 'pk_custom' };
  card._hass = { callApi: async () => settings };
  vm.runInContext('logoImageCache.set("slug:64:google.com", { status: "failed" })', context);

  await card.loadSettings();
  assert.match(context.getLogoDevUrl('google.com'), /token=pk_custom&/);
  assert.match(await context.resolveLogoSource('slug:64:google.com'), /token=pk_custom&/);
  assert.equal(vm.runInContext('logoImageCache.size', context), 0);

  settings = { logo_dev_api_key: '' };
  await card.loadSettings({ force: true });
  assert.equal(context.getLogoDevUrl('google.com'), defaultUrl);
});
