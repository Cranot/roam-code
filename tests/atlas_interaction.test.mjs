// Execute the real browser module with a small DOM double and controlled promises.
// No network, real clock, external package, or browser layout/AT claim. The double
// checks DOM writes and focus requests; real browser focus behavior is separate QA.
import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';

const snapshot = JSON.parse(readFileSync(new URL('../templates/distribution/landing-page/atlas-data.json', import.meta.url), 'utf8'));
const response = () => ({ ok: true, json: async () => structuredClone(snapshot) });
const deferred = () => {
  let resolve, reject;
  const promise = new Promise((yes, no) => { resolve = yes; reject = no; });
  return { promise, resolve, reject };
};
const flush = async () => { for (let i = 0; i < 30; i++) await Promise.resolve(); };
let run = 0;

class Element {
  constructor(document, tag) {
    this.ownerDocument = document; this.tagName = tag; this.children = [];
    this.attributes = new Map(); this.listeners = new Map(); this.dataset = {};
    this.style = {}; this.hidden = false; this.textContent = ''; this.value = '';
    this.clientWidth = 400; this.scrollLeft = 0;
    this.classes = new Set();
    this.classList = { toggle: (name, on) => on ? this.classes.add(name) : this.classes.delete(name) };
  }
  setAttribute(name, value) { this.attributes.set(name, String(value)); }
  getAttribute(name) { return this.attributes.get(name) ?? null; }
  set innerHTML(value) { assert.fail('Snapshot labels must be written as text, not HTML'); }
  hasAttribute(name) { return this.attributes.has(name); }
  removeAttribute(name) { this.attributes.delete(name); }
  append(...children) { for (const child of children) { child.parentElement = this; this.children.push(child); } }
  replaceChildren(...children) { this.children = []; this.append(...children); }
  addEventListener(name, listener) {
    if (!this.listeners.has(name)) this.listeners.set(name, []);
    this.listeners.get(name).push(listener);
  }
  dispatch(name, event = {}) { for (const listener of this.listeners.get(name) || []) listener(event); }
  focus(options) { this.ownerDocument.activeElement = this; this.focusOptions = options; }
  getBoundingClientRect() { return { width: 960 }; }
}

async function fixture(t, fetchImpl, count = 1, compact = false) {
  const document = {
    activeElement: null,
    createElement(tag) { return new Element(this, tag); },
    createElementNS(ns, tag) { return this.createElement(tag); },
    querySelectorAll(selector) { assert.equal(selector, '[data-atlas]'); return this.roots; },
    querySelector(selector) { assert.equal(selector, '[data-provenance]'); return this.provenance; },
  };
  document.provenance = document.createElement('p');
  document.roots = Array.from({ length: count }, () => {
    const root = document.createElement('section');
    if (compact) root.setAttribute('data-compact', '');
    const names = ['retry', 'node-select', 'graph', 'selection-title', 'selection-copy', 'map-count', 'map-hint', 'detail-stats', 'files', 'load-status', 'relationships'];
    root.parts = Object.fromEntries(names.map(name => [name, document.createElement(name === 'retry' ? 'button' : name === 'node-select' ? 'select' : 'div')]));
    root.parts.retry.hidden = true;
    root.fallback = document.createElement('img'); root.parts.graph.append(root.fallback);
    root.modes = ['all', 'incoming', 'outgoing'].map(mode => {
      const button = document.createElement('button'); button.dataset.mode = mode; return button;
    });
    root.controls = Array.from({ length: 3 }, () => { const control = document.createElement('div'); control.hidden = true; return control; });
    root.querySelector = selector => root.parts[selector.slice(6, -1)] ?? null;
    root.querySelectorAll = selector => selector === '[data-controls]' ? root.controls : selector === '[data-mode]' ? root.modes : [];
    return root;
  });
  const oldDocument = Object.getOwnPropertyDescriptor(globalThis, 'document');
  Object.defineProperty(globalThis, 'document', { configurable: true, value: document });
  t.after(() => oldDocument ? Object.defineProperty(globalThis, 'document', oldDocument) : delete globalThis.document);
  const calls = [], timers = new Map(); let timerId = 0;
  t.mock.method(globalThis, 'fetch', (url, options) => {
    calls.push({ url, options }); return fetchImpl(url, options, calls.length);
  });
  t.mock.method(globalThis, 'setTimeout', (callback, delay) => { timers.set(++timerId, { callback, delay }); return timerId; });
  t.mock.method(globalThis, 'clearTimeout', id => timers.delete(id));
  // Query identity gives each test its own request cache; source/imports are real.
  await import(`../templates/distribution/landing-page/atlas.mjs?test=${++run}`);
  await flush();
  return {
    document, root: document.roots[0], calls, timers,
    expire() {
      assert.equal(timers.size, 1, 'one bounded request deadline must be armed');
      const [id, timer] = timers.entries().next().value;
      assert.equal(timer.delay, 15000, '15 second interaction deadline');
      timers.delete(id); timer.callback();
    },
  };
}

function assertFallback(root) {
  assert.equal(root.parts.graph.children[0], root.fallback);
  assert.ok(root.controls.every(control => control.hidden));
  assert.equal(root.parts.retry.hidden, false);
  assert.match(root.parts['load-status'].textContent, /could not be loaded/i);
}
function assertLoaded(root) {
  assert.equal(root.parts.graph.children.length, 1);
  assert.equal(root.parts.graph.children[0].tagName, 'svg');
  assert.equal(root.parts['node-select'].children.length, snapshot.nodes.length);
  assert.ok(root.controls.every(control => !control.hidden));
  assert.equal(root.parts.retry.hidden, true);
}

test('real atlas mounts once, with shared fetch and no focus theft on initial success', async t => {
  const state = await fixture(t, async () => response(), 2);
  state.document.roots.forEach(assertLoaded);
  assert.equal(state.calls.length, 1); assert.equal(state.calls[0].url, '/atlas-data.json');
  assert.equal(state.timers.size, 0); assert.equal(state.document.activeElement, null);
});

for (const [name, fetchImpl] of [
  ['HTTP failure', async () => ({ ok: false, json: () => { throw new Error('must not parse'); } })],
  ['rejected fetch', async () => { throw new Error('offline'); }],
  ['unreadable JSON', async () => ({ ok: true, json: async () => { throw new Error('bad JSON'); } })],
  ['invalid evidence', async () => ({ ok: true, json: async () => ({ schema_version: 1, nodes: [], edges: [] }) })],
]) test(`${name} keeps fallback and exposes retry, without a dangling timer`, async t => {
  const state = await fixture(t, fetchImpl);
  assertFallback(state.root); assert.equal(state.timers.size, 0);
});

test('stalled request is aborted and reveals retry instead of waiting forever', async t => {
  const state = await fixture(t, () => new Promise(() => {}));
  state.expire(); await flush();
  assertFallback(state.root); assert.equal(state.calls[0].options.signal.aborted, true);
  assert.equal(state.timers.size, 0);
});

test('deadline also covers a body that stalls after HTTP success', async t => {
  const state = await fixture(t, async () => ({ ok: true, json: () => new Promise(() => {}) }));
  state.expire(); await flush();
  assertFallback(state.root); assert.equal(state.calls[0].options.signal.aborted, true);
});

test('focused retry stays visible while pending, mounts once, then requests chooser focus', async t => {
  const pending = deferred();
  const state = await fixture(t, async (url, options, n) => n === 1 ? { ok: false } : pending.promise);
  const { root } = state, button = root.parts.retry;
  assertFallback(root); button.focus(); button.onclick(); button.onclick(); await flush();
  assert.equal(button.hidden, false, 'do not hide the keyboard user while retry is pending');
  assert.equal(button.getAttribute('aria-disabled'), 'true');
  assert.match(root.parts['load-status'].textContent, /loading/i);
  pending.resolve(response()); await flush();
  assertLoaded(root); assert.equal(state.calls.length, 2);
  assert.equal(root.parts['node-select'].listeners.get('change').length, 1);
  root.modes.forEach(button => assert.equal(button.listeners.get('click').length, 1));
  assert.equal(state.document.activeElement, root.parts['node-select']);
  assert.deepEqual(root.parts['node-select'].focusOptions, { preventScroll: true });
});

test('retry success does not steal focus if the reader moved elsewhere', async t => {
  const pending = deferred();
  const state = await fixture(t, async (url, options, n) => n === 1 ? { ok: false } : pending.promise);
  const button = state.root.parts.retry; button.focus(); button.onclick();
  const elsewhere = state.document.createElement('a'); elsewhere.focus();
  pending.resolve(response()); await flush();
  assertLoaded(state.root); assert.equal(state.document.activeElement, elsewhere);
});

test('failed retry remains focused and can be tried again', async t => {
  const state = await fixture(t, async () => ({ ok: false }));
  const button = state.root.parts.retry; button.focus(); button.onclick(); await flush();
  assertFallback(state.root); assert.equal(state.document.activeElement, button);
  assert.notEqual(button.getAttribute('aria-disabled'), 'true');
  button.onclick(); await flush(); assert.equal(state.calls.length, 3);
});

test('late old body cannot replace or invalidate the recovered shared request', async t => {
  const oldBody = deferred(), recovery = deferred();
  const state = await fixture(t, async (url, options, n) => n === 1 ? { ok: true, json: () => oldBody.promise } : recovery.promise, 2);
  state.expire(); await flush(); state.document.roots.forEach(assertFallback);
  state.root.parts.retry.onclick(); await flush();
  oldBody.resolve({}); await flush(); // stale invalid evidence must not clear the new cache
  state.document.roots[1].parts.retry.onclick(); await flush();
  assert.equal(state.calls.length, 2);
  recovery.resolve(response()); await flush(); state.document.roots.forEach(assertLoaded);
  assert.equal(state.timers.size, 0);
});

test('real selection and mode event handlers retain the snapshot relationships', async t => {
  const state = await fixture(t, async () => response()); const { root } = state;
  const select = root.parts['node-select'];
  for (const node of snapshot.nodes) {
    select.value = node.id; select.dispatch('change');
    for (const button of root.modes) {
      button.dispatch('click');
      const edges = snapshot.edges.filter(edge => button.dataset.mode === 'incoming' ? edge.target === node.id
        : button.dataset.mode === 'outgoing' ? edge.source === node.id : edge.source === node.id || edge.target === node.id);
      const neighbors = new Set(edges.flatMap(edge => [edge.source, edge.target]).filter(id => id !== node.id));
      assert.equal(root.parts['selection-title'].textContent, node.title);
      assert.equal(root.parts['detail-stats'].children[1].children[0].textContent, neighbors.size);
      assert.equal(button.getAttribute('aria-pressed'), 'true');
    }
  }
});

function relationshipGroups(root) {
  return root.parts.relationships.children.map(group => ({
    heading: group.children[0].textContent,
    tag: group.children[1].tagName,
    labels: group.children[1].children.map(item => item.textContent),
    empty: group.children[1].textContent,
  }));
}

test('every source area and filter has matching readable directional neighbor names', async t => {
  const state = await fixture(t, async () => response()); const { root } = state;
  const select = root.parts['node-select'];
  const labels = new Map(snapshot.nodes.map(node => [node.id, node.label]));
  for (const node of snapshot.nodes) {
    select.value = node.id; select.dispatch('change');
    for (const button of root.modes) {
      button.focus(); button.dispatch('click');
      const expected = [['outgoing', 'Imports from'], ['incoming', 'Imported by']]
        .filter(([direction]) => button.dataset.mode === 'all' || button.dataset.mode === direction)
        .map(([direction, heading]) => ({
          heading,
          labels: snapshot.edges.filter(edge => edge[direction === 'outgoing' ? 'source' : 'target'] === node.id)
            .map(edge => labels.get(edge[direction === 'outgoing' ? 'target' : 'source'])),
        }));
      const groups = relationshipGroups(root);
      assert.deepEqual(groups.map(({ heading, labels }) => ({ heading, labels })), expected);
      assert.equal(state.document.activeElement, button, 'updating text must retain control focus');
    }
  }
  assert.equal(state.calls.length, 1, 'relationship lists use the same snapshot');
});

function directionalSnapshot() {
  const data = structuredClone(snapshot);
  data.nodes = ['commands', 'reciprocal', 'dependency', 'consumer', 'isolated'].map((id, index) => ({
    id, label: id, title: id, description: `${id} description`, files: 1,
    examples: [`${id}/example.py`], x: 100 + index * 150, y: 300,
  }));
  data.edges = [
    { source: 'commands', target: 'reciprocal', count: 2 },
    { source: 'reciprocal', target: 'commands', count: 1 },
    { source: 'commands', target: 'dependency', count: 3 },
    { source: 'consumer', target: 'commands', count: 1 },
  ];
  data.files_scanned = 5; data.module_connections = 7; data.unresolved_local_imports = 0;
  return data;
}

test('reciprocal imports remain in both lists while the combined area count stays deduplicated', async t => {
  const data = directionalSnapshot();
  const { root } = await fixture(t, async () => ({ ok: true, json: async () => data }));
  assert.deepEqual(relationshipGroups(root).map(({ heading, labels }) => ({ heading, labels })), [
    { heading: 'Imports from', labels: ['reciprocal', 'dependency'] },
    { heading: 'Imported by', labels: ['reciprocal', 'consumer'] },
  ]);
  assert.equal(root.parts['detail-stats'].children[1].children[0].textContent, 3);
  for (const group of root.parts.relationships.children) {
    assert.equal(group.children[1].tagName, 'ul');
    assert.ok(group.children[1].children.every(item => item.tagName === 'li' && item.children.length === 0));
  }
});

test('isolated area lists disclose snapshot-scoped absence in every filter', async t => {
  const data = directionalSnapshot();
  const { root } = await fixture(t, async () => ({ ok: true, json: async () => data }));
  root.parts['node-select'].value = 'isolated'; root.parts['node-select'].dispatch('change');
  for (const button of root.modes) {
    button.dispatch('click');
    const groups = relationshipGroups(root);
    assert.equal(groups.length, button.dataset.mode === 'all' ? 2 : 1);
    for (const group of groups) {
      assert.equal(group.tag, 'p'); assert.deepEqual(group.labels, []);
      assert.equal(group.empty, group.heading === 'Imports from'
        ? 'No outgoing connections to other areas in this snapshot.'
        : 'No incoming connections from other areas in this snapshot.');
    }
    assert.equal(root.parts['detail-stats'].children[1].children[0].textContent, 0);
  }
});

test('hostile-looking neighbor labels remain literal text without new elements or controls', async t => {
  const data = directionalSnapshot();
  const label = '<img src=x onerror="alert(1)"> & <button>not a button</button>';
  data.nodes.find(node => node.id === 'reciprocal').label = label;
  const { root } = await fixture(t, async () => ({ ok: true, json: async () => data }));
  assert.equal(relationshipGroups(root).filter(group => group.labels.includes(label)).length, 2);
  const tags = element => [element.tagName, ...element.children.flatMap(tags)];
  assert.ok(tags(root.parts.relationships).every(tag => ['div', 'h4', 'ul', 'li'].includes(tag)));
});

test('compact map keeps relationship lists out of the homepage preview', async t => {
  const { root } = await fixture(t, async () => response(), 1, true);
  for (const node of snapshot.nodes) {
    root.parts['node-select'].value = node.id; root.parts['node-select'].dispatch('change');
    assert.equal(root.parts.relationships.children.length, 0);
  }
});

test('Enter and Space activate real SVG nodes, while unrelated keys do not', async t => {
  const { root } = await fixture(t, async () => response());
  const graph = root.parts.graph.children[0];
  const nodes = graph.children.filter(element => element.getAttribute('role') === 'button');
  assert.equal(nodes.length, snapshot.nodes.length);
  for (const [index, element] of nodes.entries()) {
    for (const key of ['Enter', ' ']) {
      let prevented = false;
      element.dispatch('keydown', { key, preventDefault() { prevented = true; } });
      assert.equal(prevented, true); assert.equal(element.getAttribute('aria-pressed'), 'true');
      assert.equal(root.parts['selection-title'].textContent, snapshot.nodes[index].title);
    }
  }
  nodes[0].dispatch('keydown', { key: 'Escape', preventDefault() { assert.fail('unrelated key consumed'); } });
  assert.equal(nodes.at(-1).getAttribute('aria-pressed'), 'true');
});

test('compact homepage retry retains labels and never pans the full-map viewport', async t => {
  const state = await fixture(t, async (url, options, n) => n === 1 ? { ok: false } : response(), 1, true);
  state.root.parts.retry.focus(); state.root.parts.retry.onclick(); await flush();
  assertLoaded(state.root);
  const node = snapshot.nodes.at(-1), select = state.root.parts['node-select'];
  select.value = node.id; select.dispatch('change');
  assert.equal(state.root.parts['selection-title'].textContent, node.label);
  assert.equal(state.root.parts.graph.scrollLeft, 0);
  assert.equal(state.document.activeElement, select);
});

test('each stalled retry has a fresh bounded attempt and still permits recovery', async t => {
  const state = await fixture(t, async (url, options, n) => n < 3 ? new Promise(() => {}) : response());
  state.expire(); await flush(); assertFallback(state.root);
  const retry = state.root.parts.retry; retry.focus(); retry.onclick(); await flush();
  state.expire(); await flush(); assertFallback(state.root);
  assert.equal(state.document.activeElement, retry);
  retry.onclick(); await flush(); assertLoaded(state.root);
  assert.equal(state.calls.length, 3); assert.equal(state.timers.size, 0);
});
