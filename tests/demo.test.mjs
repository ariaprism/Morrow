import test from 'node:test';
import assert from 'node:assert/strict';
import vm from 'node:vm';
import fs from 'node:fs';

const fixtures = fs.readFileSync(new URL('../demo/fixtures.js', import.meta.url), 'utf8');
const adapter = fs.readFileSync(new URL('../demo/demo.js', import.meta.url), 'utf8');

function browser(storage = {}) {
  const writes = [];
  const network = [];
  const localStorage = {...storage};
  Object.defineProperties(localStorage, {
    getItem: {value: key => localStorage[key] ?? null},
    setItem: {value: (key, value) => { localStorage[key] = String(value); writes.push(String(value)); }},
    removeItem: {value: key => { delete localStorage[key]; }}
  });
  const context = {Response, Request, URL, DOMException, Date, console, localStorage,
    navigator: {sendBeacon: () => { throw new Error('Unexpected beacon'); }},
    document: {readyState: 'loading', addEventListener() {}},
    location: {href: 'https://example.test/', origin: 'https://example.test'},
    fetch: async (...args) => { network.push(args); return new Response('{}'); },
    addEventListener() {}};
  context.window = context;
  vm.createContext(context);
  vm.runInContext(fixtures, context);
  vm.runInContext(adapter, context);
  const call = async (path, method = 'GET', body) => {
    const response = await context.fetch(path, {method, ...(body === undefined ? {} : {body: JSON.stringify(body)})});
    return {status: response.status, data: await response.json()};
  };
  return {context, localStorage, network, writes, call};
}

test('chat, memory and diary edits survive reload and remain isolated between visitors', async () => {
  const visitor = browser();
  const memory = await visitor.call('/api/memory/entries', 'POST', {content: 'A fictional new memory', kind: 'fixed'});
  assert.equal(memory.status, 200);
  const diary = await visitor.call('/api/diary', 'POST', {content: 'A fictional diary entry', tag: 'light'});
  assert.equal(diary.data.author, 'user');
  const response = await visitor.context.fetch('/api/chat/send', {method: 'POST', body: JSON.stringify({message: '番茄面', session_id: 3})});
  assert.match(response.headers.get('Content-Type'), /text\/event-stream/);
  const events = (await response.text()).trim().split('\n\n').map(line => JSON.parse(line.slice(6)));
  assert.equal(events[0].type, 'session');
  assert.equal(events.at(-1).type, 'done');
  assert.match(events[1].content, /预设演示回复/);
  const reloaded = browser(visitor.localStorage);
  assert.ok((await reloaded.call('/api/memory/entries')).data.some(item => item.id === memory.data.id));
  assert.ok((await reloaded.call('/api/diary')).data.some(item => item.id === diary.data.id));
  assert.equal((await reloaded.call('/api/chat/messages/3')).data.at(-2).content, '番茄面');
  assert.equal((await browser().call('/api/chat/messages/3')).data.length, 6);
  assert.equal(visitor.network.length, 0);
});

test('keys, integrations, unknown writes and upload bodies never reach storage or network', async () => {
  const visitor = browser();
  for (const path of ['/api/providers', '/api/tts/settings', '/api/identity', '/api/mcp-servers']) {
    const response = await visitor.call(path, 'PUT', {api_key: 'synthetic-secret-for-test'});
    assert.equal(response.status, 409);
  }
  assert.equal((await visitor.call('/api/providers', 'POST', {name: 'synthetic'})).status, 409);
  assert.equal((await visitor.call('/api/unknown', 'POST', {})).status, 409);
  assert.equal((await visitor.context.fetch('https://example.com/api', {method: 'POST', body: 'private'})).status, 409);
  assert.equal((await visitor.context.fetch('/api/uploads', {method: 'POST', body: new FormData()})).status, 409);
  assert.equal(visitor.context.navigator.sendBeacon('/api/activity', 'private'), false);
  assert.equal(visitor.network.length, 0);
  assert.ok(visitor.writes.every(value => !value.includes('synthetic-secret-for-test')));
});

test('mutable configuration rejects extra credential fields and filters identity values', async () => {
  const visitor = browser();
  const response = await visitor.call('/api/identity', 'PUT', {user_name: '访客', assistant_name: 'Morrow', assistant_pronoun: 'ta', unexpected: 'drop-me'});
  assert.equal(response.data.user_name, '访客');
  assert.equal(response.data.unexpected, undefined);
  assert.equal((await visitor.call('/api/identity', 'PUT', {nested: {token: 'synthetic-secret-for-test'}})).status, 409);
});

test('sessions support archive, pin and deletion with local message cleanup', async () => {
  const visitor = browser();
  const session = (await visitor.call('/api/chat/sessions', 'POST', {title: 'Temporary demo conversation'})).data;
  await visitor.call('/api/chat/sessions/' + session.id, 'PUT', {archived: 1, pinned: 1});
  assert.ok(!(await visitor.call('/api/chat/sessions')).data.some(item => item.id === session.id));
  assert.ok((await visitor.call('/api/chat/sessions?archived=1')).data.some(item => item.id === session.id && item.pinned));
  await visitor.call('/api/chat/sessions/' + session.id, 'DELETE');
  assert.deepEqual((await visitor.call('/api/chat/messages/' + session.id)).data, []);
});

test('corrupt local demo data starts cleanly', async () => {
  for (const stored of ['invalid-json', '{"schema":1,"sessions":[]}']) {
    const visitor = browser({'morrow-demo-state-v1': stored});
    assert.equal((await visitor.call('/api/chat/sessions')).data.length, 3);
  }
});
