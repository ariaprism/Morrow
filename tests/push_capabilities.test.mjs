import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import vm from 'node:vm';

const html = readFileSync(new URL('../static/index.html', import.meta.url), 'utf8');
const capability = html.match(/function isWebPushCapable\(\)\{[\s\S]*?;\}/)?.[0];
assert(capability, 'Web Push capability detection must be present');

function supported({ secure = true, android = false, missing } = {}) {
  const browser = {
    isSecureContext: secure,
    navigator: { userAgent: android ? 'Android' : 'Windows desktop', serviceWorker: {} },
    PushManager: class {},
    Notification: class {},
  };
  if (missing === 'serviceWorker') delete browser.navigator.serviceWorker;
  else if (missing) delete browser[missing];
  browser.window = browser;
  return vm.runInNewContext(capability + '\nisWebPushCapable()', browser);
}

test('secure desktop and Android browsers can both enable Web Push', () => {
  assert.equal(supported(), true);
  assert.equal(supported({ android: true }), true);
});

test('insecure origins cannot enable Web Push even with browser APIs', () => {
  assert.equal(supported({ secure: false }), false);
});

test('each required browser capability is checked before enabling Web Push', () => {
  for (const missing of ['serviceWorker', 'PushManager', 'Notification']) {
    assert.equal(supported({ missing }), false, missing);
  }
});
