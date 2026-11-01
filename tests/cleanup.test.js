// Run with: node --test tests/cleanup.test.js
const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');

test('cleanup submits exactly the displayed torrents after manual changes', async () => {
  const elements = {
    content: { innerHTML: '' },
    'btn-cleanup': { disabled: false },
    'cleanup-status': { textContent: '' },
    toast: { classList: { add() {} } },
  };
  let sent, confirmation;
  const context = {
    window: {},
    sessionStorage: { removeItem() {} },
    document: {
      addEventListener() {},
      getElementById(id) { return elements[id]; },
      querySelector() { return { style: {} }; },
    },
    confirm(message) { confirmation = message; return true; },
    fetch: async (url, opts) => {
      assert.equal(url, '/api/torrents/delete');
      sent = JSON.parse(opts.body);
      return { json: async () => ({ status: 'ok', deleted: sent.hashes.length, deleted_hashes: sent.hashes }) };
    },
    setTimeout() {}, clearTimeout() {},
  };
  vm.createContext(context);
  vm.runInContext(fs.readFileSync('static/app.js', 'utf8'), context);
  vm.runInContext(`
    state.dedup = [
      { keep: { torrent_hash: 'a', file_index: 0 }, delete: [{ torrent_hash: 'b', file_index: 0 }] },
      { keep: { torrent_hash: 'c', file_index: 0 }, delete: [{ torrent_hash: 'd', file_index: 0 }] },
      { keep: { torrent_hash: 'e', file_index: 0 }, delete: [{ torrent_hash: 'd', file_index: 1 }] },
    ];
    state.keepOverrides = { 'a|0': false, 'b|0': true };
    renderCleanup(document.getElementById('content'));
  `, context);
  assert.match(elements.content.innerHTML, /删除 2 个种子/);
  await context.window.confirmDelete();
  assert.match(confirmation, /删除 2 个种子/);
  assert.deepEqual(sent, { hashes: ['a', 'd'], delete_files: true });
  assert.match(elements.content.innerHTML, /删除 0 个种子/);
});

test('refresh restores saved step and manual choices without resetting server state', async () => {
  const callbacks = {};
  const content = { innerHTML: '' };
  const storage = { qb_step: '5', qb_keep_overrides: JSON.stringify({ 'a|0': false, 'b|0': true }) };
  const group = { keep: { torrent_hash: 'a', file_index: 0 }, delete: [{ torrent_hash: 'b', file_index: 0 }] };
  const responses = {
    '/api/config': { config: {} },
    '/api/status': { running: false, completed: { fetch: true, tmdb: true, analyze: true, dedup: true } },
    '/api/torrents': { torrents: [] }, '/api/tmdb/results': { matches: [] },
    '/api/analyze/profiles': { profiles: [] },
    '/api/dedup/results': { groups: [group], summary: {} },
  };
  const context = {
    window: {},
    document: {
      addEventListener(name, cb) { callbacks[name] = cb; },
      getElementById(id) { return id === 'content' ? content : null; },
      querySelectorAll() { return []; },
      documentElement: { setAttribute() {} },
    },
    sessionStorage: { getItem(key) { return storage[key] || null; }, setItem(key, value) { storage[key] = value; }, removeItem(key) { delete storage[key]; } },
    localStorage: { getItem() { return null; } },
    fetch: async (url) => { assert.ok(responses[url], `unexpected request ${url}`); return { json: async () => responses[url] }; },
    setTimeout() {}, clearTimeout() {},
  };
  vm.createContext(context);
  vm.runInContext(fs.readFileSync('static/app.js', 'utf8'), context);
  await callbacks.DOMContentLoaded();
  assert.match(content.innerHTML, /删除 1 个种子/);
  assert.deepEqual(Array.from(vm.runInContext('getDeleteList().map(p => p.torrent_hash)', context)), ['a']);
});

test('matching and analysis buttons reflect start, pause, resume and completion', async () => {
  const buttons = { 'btn-tmdb': { disabled: false }, 'btn-analyze': { disabled: false } };
  let paused = false;
  const context = {
    window: {}, document: { addEventListener() {}, getElementById(id) { return buttons[id]; } },
    fetch: async (url) => { assert.equal(url, '/api/tmdb/pause'); paused = !paused; return { json: async () => ({ status: 'ok', paused }) }; },
  };
  vm.createContext(context);
  vm.runInContext(fs.readFileSync('static/app.js', 'utf8'), context);
  assert.match(vm.runInContext("taskButtonLabel('tmdb')", context), /开始匹配/);
  vm.runInContext("state.busy = true; state.busyStep = 'tmdb'", context);
  assert.match(vm.runInContext("taskButtonLabel('tmdb')", context), /暂停匹配/);
  await context.window.controlTask('tmdb');
  assert.match(buttons['btn-tmdb'].textContent, /继续匹配/);
  await context.window.controlTask('tmdb');
  assert.match(buttons['btn-tmdb'].textContent, /暂停匹配/);
  vm.runInContext("state.busy = false; state.completed.tmdb = true; state.completed.analyze = true", context);
  assert.match(vm.runInContext("taskButtonLabel('tmdb')", context), /匹配完成/);
  assert.match(vm.runInContext("taskButtonLabel('analyze')", context), /分析完成/);
  vm.runInContext("state.completed.analyze = false; state.busy = true; state.busyStep = 'analyze'; state.paused = true", context);
  assert.match(vm.runInContext("taskButtonLabel('analyze')", context), /继续分析/);
});
